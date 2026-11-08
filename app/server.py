"""Wasted Wind API. Endpoints and shapes: docs/CONTRACT.md §7. Run: make serve

Tables from app.data are cached and shared: never modify them in place.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app import contract, data

app = FastAPI(title="Wasted Wind", version="0.1")
STATIC_DIR = Path(__file__).resolve().parent / "static"
ISO = "%Y-%m-%dT%H:%M:%SZ"


# ---------- helpers ----------

def meta(*sources: str, **extra) -> dict:
    source = "fixtures" if "fixtures" in sources else "processed"
    return {"data_version": data.data_version(source), "source": source,
            "generated_at": datetime.now(timezone.utc).strftime(ISO), "estimated": True, **extra}


def num(x, nd: int = 3):
    """Plain float rounded, or None for NaN/missing."""
    if x is None or pd.isna(x):
        return None
    return round(float(x), nd)


def iso(ts) -> Optional[str]:
    return None if ts is None or pd.isna(ts) else pd.Timestamp(ts).tz_convert("UTC").strftime(ISO)


def records(df: pd.DataFrame, nd: int = 3) -> list[dict]:
    """DataFrame -> JSON-safe records: UTC times as ISO 'Z' strings, NaN as null."""
    out = df.copy()
    for c in out.columns:
        if isinstance(out[c].dtype, pd.DatetimeTZDtype):
            out[c] = out[c].dt.strftime(ISO)
        elif pd.api.types.is_float_dtype(out[c]):
            out[c] = out[c].round(nd)
    return json.loads(out.to_json(orient="records"))


def parse_ts(value: Optional[str], name: str) -> Optional[pd.Timestamp]:
    if value is None or value == "":
        return None
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        raise HTTPException(400, f"{name}={value!r} is not an ISO date or datetime")
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def window(df: pd.DataFrame, col: str, start: Optional[str], end: Optional[str]) -> pd.DataFrame:
    """Rows with start <= df[col] < end (UTC). None = open-ended."""
    s, e = parse_ts(start, "start"), parse_ts(end, "end")
    mask = pd.Series(True, index=df.index)
    if s is not None:
        mask &= df[col] >= s
    if e is not None:
        mask &= df[col] < e
    return df[mask]


def window_years(df: pd.DataFrame, start, end) -> float:
    s = parse_ts(start, "start") or df["period_start"].min()
    e = parse_ts(end, "end") or (df["period_start"].max() + pd.Timedelta(minutes=30))
    return max((e - s).total_seconds(), 1800) / (365.25 * 86400)


def b4_binding_periods(resid: pd.DataFrame) -> pd.Index:
    r = resid[(resid["zone"] == "north_scotland") & (resid["residual_method"] == "binding")]
    return pd.Index(r["period_start"].unique())


def boundary_split_mwh(curt: pd.DataFrame, resid: pd.DataFrame) -> dict:
    """Attribute waste to a boundary: North Scotland waste while B4 was full -> B4, all else -> B6.

    Approximate: the reconstruction can't see which boundary caused each acceptance.
    """
    in_b4 = (curt["zone"] == "north_scotland") & curt["period_start"].isin(b4_binding_periods(resid))
    b4 = float(curt.loc[in_b4, "curtailed_mwh_a"].sum())
    return {"B4": b4, "B6": float(curt["curtailed_mwh_a"].sum()) - b4}


def cost_totals(curt: pd.DataFrame, resid: pd.DataFrame, years: float,
                gas_p_therm=contract.DEFAULT_GAS_P_THERM, carbon_gbp_t=contract.DEFAULT_CARBON_GBP_T,
                intensity=contract.DEFAULT_CARBON_INTENSITY_T_MWH) -> dict:
    mwh = float(curt["curtailed_mwh_a"].sum())
    payments = float((curt["curtailed_mwh_a"] * -curt["bid_price_gbp_mwh"]).fillna(0).sum())
    split = boundary_split_mwh(curt, resid) if resid is not None else None
    return {
        "curtailed_twh": mwh / 1e6,
        "by_boundary_twh": {k: v / 1e6 for k, v in split.items()} if split else None,
        "payments_gbp": payments,
        "replacement_gas_gbp": mwh * contract.replacement_gbp_mwh(gas_p_therm, carbon_gbp_t, intensity),
        "co2_t": mwh * intensity,
        "homes_equiv": mwh / contract.MWH_PER_HOME_PER_YEAR / years,
    }


def round_totals(t: dict) -> dict:
    return {k: (None if v is None else {kk: num(vv, 9) for kk, vv in v.items()} if isinstance(v, dict)
                else num(v, 9 if k == "curtailed_twh" else 1)) for k, v in t.items()}


RESIDUAL_NOTE = "B4/B6 split needs the real residual.parquet (#4); not mixing fake residual into real curtailment"


def residual_matching(curt_source: str):
    """residual.parquet only if it comes from the same side as curtailment, else None."""
    resid, src = data.table("residual.parquet")
    return (resid, src) if src == curt_source else (None, None)


def known_farm(farm_id: str) -> None:
    farms, _ = data.table("farms.csv")
    if farm_id not in set(farms["farm_id"]):
        raise HTTPException(404, f"unknown farm_id {farm_id!r}")


# ---------- health and farms ----------

@app.get("/api/health")
def health():
    curt, src = data.table("curtailment.parquet")
    return {"status": "ok",
            "range": {"start": iso(curt["period_start"].min()),
                      "end": iso(curt["period_start"].max() + pd.Timedelta(minutes=30))},
            "meta": meta(src)}


@app.get("/api/farms")
def farms():
    df, src = data.table("farms.csv")
    out = []
    for fid, g in df.groupby("farm_id", sort=False):
        cap = g["capacity_mw"].sum()
        w = g["capacity_mw"] / cap if cap else 1 / len(g)
        out.append({"farm_id": fid, "farm_name": g["farm_name"].iloc[0],
                    "lat": num((g["lat"] * w).sum(), 5), "lon": num((g["lon"] * w).sum(), 5),
                    "capacity_mw": num(cap, 1), "zone": g["zone"].iloc[0],
                    "site_type": g["site_type"].iloc[0], "bmu_ids": g["bmu_id"].tolist()})
    return {"farms": out, "meta": meta(src)}


# ---------- reveal ----------

@app.get("/api/reveal/summary")
def reveal_summary(start: Optional[str] = None, end: Optional[str] = None):
    curt_all, s1 = data.table("curtailment.parquet")
    resid, s2 = residual_matching(s1)
    curt = window(curt_all, "period_start", start, end)
    totals = round_totals(cost_totals(curt, resid, window_years(curt_all, start, end)))
    g = curt.groupby("farm_id").agg(curtailed_mwh=("curtailed_mwh_a", "sum"),
                                    potential_mwh=("planned_mwh", "sum"))
    g["share_wasted"] = (g["curtailed_mwh"] / g["potential_mwh"]).where(g["potential_mwh"] > 0, 0.0)
    g = g.sort_values("curtailed_mwh", ascending=False).reset_index()
    extra = {} if resid is not None else {"note": RESIDUAL_NOTE}
    return {"totals": totals, "farms": records(g), "meta": meta(s1, **extra)}


@app.get("/api/reveal/farm/{farm_id}")
def reveal_farm(farm_id: str, start: Optional[str] = None, end: Optional[str] = None):
    known_farm(farm_id)
    curt, src = data.table("curtailment.parquet")
    c = window(curt[curt["farm_id"] == farm_id], "period_start", start, end)
    cols = ["planned_mwh", "accepted_mwh", "metered_mwh", "curtailed_mwh_a", "curtailed_mwh_b"]
    s = c.groupby("period_start")[cols].sum(min_count=1).reset_index()   # keep null when all null
    return {"farm_id": farm_id, "series": records(s), "meta": meta(src)}


@app.get("/api/reveal/boundaries")
def reveal_boundaries(start: Optional[str] = None, end: Optional[str] = None):
    _, curt_src = data.resolve("curtailment.parquet")
    resid, src = residual_matching(curt_src)
    if resid is None:
        return {"boundaries": [{"boundary": b, "share_periods_binding": None, "approximate": True}
                               for b in contract.BOUNDARY_OF_ZONE.values()],
                "meta": meta(curt_src, note=RESIDUAL_NOTE)}
    r = window(resid, "period_start", start, end)
    out = []
    for zone, boundary in contract.BOUNDARY_OF_ZONE.items():
        z = r[r["zone"] == zone]
        share = float((z["residual_method"] == "binding").mean()) if len(z) else 0.0
        out.append({"boundary": boundary, "share_periods_binding": round(share, 4), "approximate": True})
    return {"boundaries": out, "meta": meta(src)}


# ---------- forecast ----------

def _latest_live(fc: pd.DataFrame) -> pd.DataFrame:
    live = fc[fc["split"] == "live"]
    if live.empty:
        return live
    live = live[live["issued_at"] == live["issued_at"].max()]
    return live.assign(lead_days=data.lead_days(live))


@app.get("/api/forecast/tomorrow")
def forecast_tomorrow(lead_days: int = Query(1, ge=1, le=7)):
    """The day-ahead (lead 1), or with lead_days=k the day k days after the issue date (#49)."""
    fc, src = data.table("forecast.parquet")
    live = _latest_live(fc)
    live = live[live["lead_days"] == lead_days] if len(live) else live
    if live.empty:
        return {"issued_at": None, "farms": [], "meta": meta(src, note="no live forecast available")}
    issued = live["issued_at"].max()
    live = live.sort_values("hour_start")
    out = []
    for fid, g in live.groupby("farm_id", sort=False):
        h = g[["hour_start", "curtailed_mwh_pred", "curtailed_mwh_p10", "curtailed_mwh_p90"]]
        h = h.rename(columns={"curtailed_mwh_pred": "pred_mwh", "curtailed_mwh_p10": "p10_mwh",
                              "curtailed_mwh_p90": "p90_mwh"})
        out.append({"farm_id": fid, "hours": records(h)})
    return {"issued_at": iso(issued), "lead_days": lead_days, "farms": out, "meta": meta(src)}


@app.get("/api/forecast/week")
def forecast_week(farm_id: Optional[str] = None):
    """#49: one row per live day, all farms summed (or one farm, with farm_id, so the strip shows the same
    scope as the selected farm's numbers): the forecast and the summed hourly p10/p90 (a sum of farm-hour
    ranges, not a band for the day). kind = "outlook" from the first lead where the simple wind rule beat the
    model in the backtest (forecast_skill_by_lead.json), else "forecast"."""
    fc, src = data.table("forecast.parquet")
    live = _latest_live(fc)
    if live.empty:
        return {"issued_at": None, "farm_id": farm_id, "days": [], "meta": meta(src, note="no live forecast available")}
    if farm_id is not None:
        live = live[live["farm_id"] == farm_id]
        if live.empty:
            raise HTTPException(404, f"no live forecast for farm_id {farm_id!r}")
    skill = _skill_by_lead(src) or []
    outlook_from = min((r["lead_days"] for r in skill if r["wind_rule_mae_mwh"] < r["mae_mwh"]), default=None)
    g = live.groupby("lead_days").agg(date=("hour_start", "min"), pred_mwh=("curtailed_mwh_pred", "sum"))
    for c in ("curtailed_mwh_p10", "curtailed_mwh_p90"):      # null when the day has no band (leads 2+)
        g[c] = live.groupby("lead_days")[c].sum(min_count=1)
    opt = lambda v: None if pd.isna(v) else float(v)  # noqa: E731
    days = [{"date": r.date.strftime("%Y-%m-%d"), "lead_days": int(k), "pred_mwh": float(r.pred_mwh),
             "p10_mwh": opt(r.curtailed_mwh_p10), "p90_mwh": opt(r.curtailed_mwh_p90),
             "kind": "outlook" if outlook_from is not None and k >= outlook_from else "forecast"}
            for k, r in g.sort_index().iterrows()]
    return {"issued_at": iso(live["issued_at"].max()), "farm_id": farm_id, "outlook_from_lead": outlook_from,
            "days": days, "meta": meta(src)}


@app.get("/api/forecast/backtest")
def forecast_backtest(farm_id: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None):
    fc, src = data.table("forecast.parquet")
    bt = window(fc[fc["split"] == "backtest"], "hour_start", start, end)
    # if an hour was forecast more than once, keep the latest issue
    bt = bt.sort_values("issued_at").drop_duplicates(["farm_id", "hour_start"], keep="last")
    if farm_id is not None:
        if farm_id not in set(fc["farm_id"]):
            raise HTTPException(404, f"no forecast for farm_id {farm_id!r}")
        bt = bt[bt["farm_id"] == farm_id]
    cols = {"curtailed_mwh_pred": "pred_mwh", "curtailed_mwh_actual": "actual_mwh",
            "baseline_persistence_mwh": "persistence_mwh", "baseline_climatology_mwh": "climatology_mwh",
            "baseline_wind_rule_mwh": "wind_rule_mwh"}
    s = bt.groupby("hour_start")[list(cols)].sum(min_count=1).rename(columns=cols).reset_index()
    return {"farm_id": farm_id or "all", "series": records(s), "meta": meta(src)}


@app.get("/api/forecast/metrics")
def forecast_metrics():
    m, src = data.json_file("model_metrics.json")
    return {**m, "skill_by_lead": _skill_by_lead(src), "meta": meta(src)}


def _skill_by_lead(src: str):
    """#49: error by days ahead (pipeline.lead_skill's "chart" rows), from the same side as the
    metrics; None if that file isn't built yet."""
    path, side = data.resolve("forecast_skill_by_lead.json", src)
    if side != src or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("chart")


# ---------- autopilot ----------

@app.get("/api/autopilot/schedule")
def autopilot_schedule(date: Optional[str] = None, battery_id: Optional[str] = None,
                       strategy: Literal["do_nothing", "reactive", "forecast", "perfect"] = "forecast",
                       dispatcher: Optional[Literal["rule", "lp", "lp_band"]] = None):
    sched, src = data.table("schedules.parquet")
    s = sched[sched["strategy"] == strategy]
    if dispatcher is None:                      # prefer the optimiser once it exists
        dispatcher = "lp" if (s["dispatcher"] == "lp").any() else "rule"
    s = s[s["dispatcher"] == dispatcher]
    if battery_id is not None:
        if battery_id not in set(sched["battery_id"]):
            raise HTTPException(404, f"unknown battery_id {battery_id!r}")
        s = s[s["battery_id"] == battery_id]
    if s.empty:
        return {"plan_issued_at": None, "live": False, "batteries": [], "available": None, "meta": meta(src)}
    available = {"first": s["period_start"].min().strftime("%Y-%m-%d"),
                 "last": s["period_start"].max().strftime("%Y-%m-%d")}
    day = parse_ts(date, "date").normalize() if date else s["period_start"].max().normalize()
    s = s[(s["period_start"] >= day) & (s["period_start"] < day + pd.Timedelta(days=1))]
    # live mode re-plans: for each half hour keep the newest plan
    s = s.sort_values("plan_issued_at").drop_duplicates(["battery_id", "period_start"], keep="last")
    s = s.assign(executed=False)
    # past live half hours: what the battery actually did (live_executed.parquet), not the plan
    ex = executed_rows(src, strategy, dispatcher, day, battery_id)
    if ex is not None and len(ex):
        key = ["battery_id", "period_start"]
        done = pd.MultiIndex.from_frame(ex[key])
        s = pd.concat([s[~pd.MultiIndex.from_frame(s[key]).isin(done)], ex.assign(executed=True)], ignore_index=True)
    today = pd.Timestamp.now(tz="UTC").normalize()
    out = [{"battery_id": bid,
            "steps": records(g.sort_values("period_start")[["period_start", "action", "power_mw", "soc_mwh", "reason",
                                                             "captured_curtailment_mwh", "revenue_gbp", "executed"]])}
           for bid, g in s.groupby("battery_id", sort=True)]
    return {"plan_issued_at": iso(s["plan_issued_at"].max()) if len(s) else None,
            "live": bool(day >= today), "date": day.strftime("%Y-%m-%d"),
            "strategy": strategy, "dispatcher": dispatcher, "batteries": out, "available": available,
            "meta": meta(src)}


def executed_rows(sched_src: str, strategy: str, dispatcher: str, day: pd.Timestamp, battery_id: Optional[str]):
    """Executed live rows for one day, or None. Only from the same side (real/fake) as the schedules, and only
    for the strategy and planner the live battery runs (the file records them)."""
    try:
        path, src = data.resolve("live_executed.parquet")
    except FileNotFoundError:
        return None
    if src != sched_src or not path.exists():
        return None
    ex, _ = data.table("live_executed.parquet")
    ex = ex[(ex["strategy"] == strategy) & (ex["dispatcher"] == dispatcher)
            & (ex["period_start"] >= day) & (ex["period_start"] < day + pd.Timedelta(days=1))]
    if battery_id is not None:
        ex = ex[ex["battery_id"] == battery_id]
    return ex


@app.get("/api/autopilot/scorecard")
def autopilot_scorecard():
    sc, src = data.json_file("scorecard.json")
    return {**sc, "meta": meta(src)}


# ---------- scenario (simulator) ----------

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BoundaryLever(_Strict):
    mode: Literal["historical", "fixed", "offset"] = "historical"
    mw: float = Field(0.0, ge=-10000, le=20000)


class BatteryLever(_Strict):
    battery_id: str
    lat: float
    lon: float
    zone: Literal["north_scotland", "south_scotland"]
    power_mw: float = Field(gt=0, le=5000)
    energy_mwh: float = Field(gt=0, le=20000)
    efficiency: float = Field(0.88, ge=0.80, le=0.95)
    dispatcher: Literal["rule", "lp", "lp_band"] = "rule"
    site: Literal["grid", "farm"] = "grid"          # "farm": shares a wind farm's connection, catches only its waste
    radius_km: float = Field(20.0, ge=1, le=100)    # farms within this distance count as "its" farms


class WeatherLever(_Strict):
    wind_scale: float = Field(1.0, ge=0.0, le=1.3)   # 0 allowed for the "no wind -> no waste" check
    replay_start: Optional[str] = None
    replay_end: Optional[str] = None


class WindowLever(_Strict):
    start: Optional[str] = None
    end: Optional[str] = None


class SubseaLink(_Strict):
    zone: Literal["north_scotland", "south_scotland"] = "north_scotland"
    gw: float = Field(2.0, ge=0, le=10)
    availability: float = Field(0.95, ge=0, le=1)


class ZoneDemand(_Strict):
    heat_pumps_gw: float = Field(0.0, ge=0, le=10)
    evs_gw: float = Field(0.0, ge=0, le=10)
    data_centres_gw: float = Field(0.0, ge=0, le=10)


class DemandGrowth(_Strict):
    north_scotland: ZoneDemand = ZoneDemand()
    south_scotland: ZoneDemand = ZoneDemand()


class SwitchOffPrice(_Strict):
    mode: Literal["multiplier", "flat"] = "multiplier"
    value: float = Field(1.0, ge=0)   # multiplier on the farms' bids, or a flat £/MWh paid to them


class Scenario(_Strict):
    # Core levers 1, 2, 4, 8, 12, 13
    b4: BoundaryLever = BoundaryLever()
    b6: BoundaryLever = BoundaryLever()
    batteries: list[BatteryLever] = []
    weather: WeatherLever = WeatherLever()
    window: WindowLever = WindowLever()
    forecast_quality: Literal["none", "ours", "perfect"] = "ours"
    # Full levers 3, 6, 9, 10, 11
    subsea_link: Optional[SubseaLink] = None
    demand_growth: DemandGrowth = DemandGrowth()
    gas_price_p_therm: float = Field(contract.DEFAULT_GAS_P_THERM, ge=0)
    carbon_price_gbp_t: float = Field(contract.DEFAULT_CARBON_GBP_T, ge=0)
    switch_off_price: SwitchOffPrice = SwitchOffPrice()
    carbon_intensity_t_mwh: float = Field(contract.DEFAULT_CARBON_INTENSITY_T_MWH, ge=0)


def normalise(sc: Scenario) -> dict:
    """Canonical dict: equivalent requests produce identical dicts (and fingerprints)."""
    d = sc.model_dump()
    for b in ("b4", "b6"):
        if d[b]["mode"] == "historical":
            d[b]["mw"] = 0.0
    d["batteries"] = sorted(d["batteries"], key=lambda x: x["battery_id"])
    return d


def fingerprint(d: dict) -> str:
    return "sha1-" + hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()


_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_CACHE_MAX = 256


def _baseline_only(sc: dict) -> dict:
    """Used until sim.engine.simulate exists (#9): every scenario returns the historical baseline."""
    curt_all, _ = data.table("curtailment.parquet")
    resid, _ = data.table("residual.parquet")
    w = sc["window"]
    curt = window(curt_all, "period_start", w["start"], w["end"])
    base = round_totals(cost_totals(curt, resid, window_years(curt_all, w["start"], w["end"])))
    farms_ = curt.groupby("farm_id")["curtailed_mwh_a"].sum().round(3)
    return {
        "window": {"start": iso(curt["period_start"].min()),
                   "end": iso(curt["period_start"].max() + pd.Timedelta(minutes=30)) if len(curt) else None},
        "scenario": base, "baseline": base,
        "delta": {k: ({kk: 0.0 for kk in v} if isinstance(v, dict) else 0.0) for k, v in base.items()},
        "batteries": [{"battery_id": b["battery_id"], "captured_curtailment_mwh": None,
                       "revenue_gbp": None, "cycles": None} for b in sc["batteries"]],
        "farms": [{"farm_id": f, "curtailed_mwh": float(v), "baseline_curtailed_mwh": float(v)}
                  for f, v in farms_.items()],
    }


def _engine():
    try:
        from sim.engine import simulate
        return simulate, "sim.engine"
    except ImportError:
        return _baseline_only, "baseline-only (simulator not built yet, see #9)"


@app.post("/api/scenario")
def scenario(sc: Scenario):
    t0 = time.perf_counter()
    norm = normalise(sc)
    fp = fingerprint(norm)
    sig = data.signature("curtailment.parquet", "residual.parquet", "farms.csv")
    key = (fp, sig)
    srcs = [s for _, s, _ in sig]
    if key in _CACHE:
        _CACHE.move_to_end(key)
        body = _CACHE[key]
        cached = True
    else:
        simulate, engine_name = _engine()
        body = {**simulate(norm), "engine": engine_name}
        _CACHE[key] = body
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
        cached = False
    engine_name = body.get("engine")
    result = {k: v for k, v in body.items() if k != "engine"}
    return {"fingerprint": fp, "cached": cached, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
            **result, "meta": meta(*([body["data_source"]] if body.get("data_source") else srcs), engine=engine_name)}


# ---------- method ----------

# ---------- wind (map layer): fetched live from Open-Meteo, cached ----------

OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
_wind_cache: dict = {}


def fetch_wind(lats: list[float], lons: list[float], day: str, recent: bool) -> list[dict]:
    """Hourly 100 m wind speed (m/s) and direction (degrees it blows FROM) at each point for one UTC day.
    The archive lags real time by a few days, so recent days and tomorrow come from the forecast API."""
    import requests
    url = OPEN_METEO_FORECAST if recent else OPEN_METEO_ARCHIVE
    r = requests.get(url, timeout=8, params={
        "latitude": ",".join(f"{x:.4f}" for x in lats), "longitude": ",".join(f"{x:.4f}" for x in lons),
        "start_date": day, "end_date": day, "hourly": "wind_speed_100m,wind_direction_100m",
        "wind_speed_unit": "ms", "timezone": "GMT"})
    r.raise_for_status()
    out = r.json()
    return out if isinstance(out, list) else [out]


@app.get("/api/wind")
def wind(date: str):
    """Wind at every farm for one day, for the map's arrows. 503 if Open-Meteo can't be reached
    (venue Wi-Fi): the dashboard then hides the layer and says so."""
    day = parse_ts(date, "date").normalize()
    key = day.strftime("%Y-%m-%d")
    if key not in _wind_cache:
        farms, src = data.table("farms.csv")
        f = farms.drop_duplicates("farm_id")[["farm_id", "lat", "lon"]].dropna()
        recent = day >= pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=30)
        try:
            pts = fetch_wind(f["lat"].tolist(), f["lon"].tolist(), key, recent)
        except Exception as e:  # noqa: BLE001 - offline or API trouble: say so, never crash the page
            raise HTTPException(503, f"wind not available: {type(e).__name__}")
        farms_out = []
        for fid, p in zip(f["farm_id"], pts):
            h = p.get("hourly", {})
            farms_out.append({"farm_id": fid, "speed_ms": h.get("wind_speed_100m", []),
                              "direction_deg": h.get("wind_direction_100m", [])})
        _wind_cache[key] = {"date": key, "hours": [f"{key}T{h:02d}:00:00Z" for h in range(24)], "farms": farms_out,
                            "source": "Open-Meteo forecast" if recent else "Open-Meteo archive (ERA5)",
                            "meta": meta(src)}
    return _wind_cache[key]


@app.get("/api/method/calibration")
def method_calibration():
    """Simulator with historical settings vs Part 1, both from the simulator's own inputs."""
    try:
        from sim.calibrate import calibration_by_month
        from sim.engine import input_source
    except ImportError:                          # simulator not available: history only
        curt, src = data.table("curtailment.parquet")
        hist = curt.groupby(curt["period_start"].dt.strftime("%Y-%m"))["curtailed_mwh_a"].sum()
        return {"historical_twh": num(float(hist.sum()) / 1e6, 6), "simulated_twh": None, "error_pct": None,
                "by_month": [{"month": m, "historical_mwh": num(v, 1), "simulated_mwh": None} for m, v in hist.items()],
                "meta": meta(src)}
    months = calibration_by_month()
    h_twh = sum(h for h, _ in months.values()) / 1e6
    s_twh = sum(v for _, v in months.values()) / 1e6
    return {"historical_twh": num(h_twh, 6), "simulated_twh": num(s_twh, 6),
            "error_pct": num((s_twh - h_twh) / h_twh * 100, 2) if h_twh else None,
            "by_month": [{"month": m, "historical_mwh": num(h, 1), "simulated_mwh": num(v, 1)}
                         for m, (h, v) in months.items()],
            "meta": meta(input_source())}


# Static dashboard last, so /api routes win.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
