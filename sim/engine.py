"""The grid simulator: simulate(scenario) -> results (CONTRACT.md §6/§7, BRIEF.md Part 4).

Per half hour, in MW:
  1. potential wind per BMU (planned output, or the power curve on scaled wind: lever 8)
  2. surplus_N = wind_N + residual_N
  3. batteries charge from would-be waste / discharge into boundary headroom (lever 4, 13)
  4. flow_B4 = min(surplus_N, B4); surplus_S = wind_S + residual_S + flow_B4; flow_B6 = min(surplus_S, B6)
  5. waste = what's over a limit; B4 waste from North farms, B6 waste from any Scottish farm,
     cheapest bids (least negative) first
  6. costs: switch-off payments, replacement gas, CO2

The engine always runs the full period (so batteries arrive realistically charged) and the
results are sliced to the requested window (lever 12).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd

from app import contract, data
from sim import levers

try:   # Person A's dispatcher (#6); same signature as the stand-in
    from dispatch.rule_based import plan_day
    DISPATCHER = "dispatch.rule_based"
except ImportError:
    from sim.dispatch_stub import plan_day
    DISPATCHER = "sim.dispatch_stub (stand-in until #6)"

H = 0.5   # hours per settlement period: MW x H = MWh

DEFAULTS = {
    "b4": {"mode": "historical", "mw": 0.0},
    "b6": {"mode": "historical", "mw": 0.0},
    "batteries": [],
    "weather": {"wind_scale": 1.0, "replay_start": None, "replay_end": None},
    "window": {"start": None, "end": None},
    "forecast_quality": "ours",
}
# Full-tier levers (#15): accepted by the API, not applied yet
NOT_YET_APPLIED = {"subsea_link": None, "demand_growth": None, "gas_price_p_therm": contract.DEFAULT_GAS_P_THERM,
                   "carbon_price_gbp_t": contract.DEFAULT_CARBON_GBP_T, "switch_off_price": None,
                   "carbon_intensity_t_mwh": contract.DEFAULT_CARBON_INTENSITY_T_MWH}


def with_defaults(sc: dict) -> dict:
    out = copy.deepcopy(DEFAULTS)
    for k, v in (sc or {}).items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k].update({kk: vv for kk, vv in v.items()})
        else:
            out[k] = v
    return out


# ---------- inputs ----------

@dataclass
class Inputs:
    periods: pd.DatetimeIndex     # T half-hour starts, UTC
    bmu: list                     # U unit ids, in dispatch priority order (cheapest bid first)
    farm: np.ndarray              # (U,) farm_id
    north: np.ndarray             # (U,) bool, unit is north of B4
    cap_mw: np.ndarray            # (U,)
    planned_mw: np.ndarray        # (T, U) potential output, MW (planned_mwh / 0.5)
    wind_ms: np.ndarray           # (T, U) actual 100 m wind, NaN if unknown
    bid_gbp_mwh: np.ndarray       # (T, U) price used for payments (period bid, else unit median)
    hist_curtailed_mwh: np.ndarray  # (T, U) Part 1 Method A, for calibration
    res_n: np.ndarray             # (T,) residual MW
    res_s: np.ndarray
    lim4: np.ndarray              # (T,) historical boundary limits MW
    lim6: np.ndarray
    price: np.ndarray             # (T,) market price £/MWh
    fc_n: np.ndarray              # (T,) forecast waste behind each zone, MWh per half hour, NaN if none
    fc_s: np.ndarray

    @staticmethod
    def synthetic(rng, T: int, U: int) -> "Inputs":
        """Random inputs of a given size, for speed tests."""
        cap = rng.uniform(30, 400, U)
        planned = cap * rng.uniform(0, 1, (T, U))
        north = np.arange(U) < int(U * 0.7)
        periods = pd.date_range("2024-01-01", periods=T, freq="30min", tz="UTC")
        z = np.zeros(T)
        return Inputs(periods, [f"U{i}" for i in range(U)], np.array([f"f{i}" for i in range(U)]), north, cap,
                      planned, rng.uniform(0, 25, (T, U)), np.full((T, U), -60.0), np.zeros((T, U)),
                      z - 500, z - 800, z + 2400, z + 2600, 70 + z, z + 5, z + 1)


def _wide(df: pd.DataFrame, col: str, periods, bmus) -> np.ndarray:
    return df.pivot(index="period_start", columns="bmu_id", values=col).reindex(index=periods, columns=bmus).to_numpy(float)


CORE_FILES = ("curtailment.parquet", "residual.parquet", "farms.csv")


@lru_cache(maxsize=2)
def _load(signature: tuple, source: str) -> Inputs:
    # one consistent dataset: real only if curtailment AND residual are real (see data.group_source)
    curt, _ = data.table("curtailment.parquet", source)
    resid, _ = data.table("residual.parquet", source)
    farms, _ = data.table("farms.csv", source)
    fc_path, fc_source = data.resolve("forecast.parquet")
    if fc_source == source:
        fc, _ = data.table("forecast.parquet", source)
    else:   # fake forecast with real farms is meaningless: plan without one (reactive fallback)
        fc = data.table("forecast.parquet", "fixtures")[0].iloc[:0]

    periods = pd.DatetimeIndex(sorted(curt["period_start"].unique()))
    start, end = periods.min().normalize(), periods.max().normalize() + pd.Timedelta(days=1)
    periods = pd.date_range(start, end, freq="30min", inclusive="left", tz="UTC")   # whole UTC days

    # dispatch priority: least negative median bid first (cheapest for NESO), unknown last
    med = curt.groupby("bmu_id")["bid_price_gbp_mwh"].median()
    units = farms[farms["bmu_id"].isin(curt["bmu_id"].unique())].copy()
    units["med_bid"] = units["bmu_id"].map(med)
    units = units.sort_values("med_bid", ascending=False, na_position="last")
    bmus = units["bmu_id"].tolist()

    planned = np.nan_to_num(_wide(curt, "planned_mwh", periods, bmus)) / H
    hist = np.nan_to_num(_wide(curt, "curtailed_mwh_a", periods, bmus))
    bid = _wide(curt, "bid_price_gbp_mwh", periods, bmus)
    fallback = units["med_bid"].fillna(med.median() if med.notna().any() else -50.0).to_numpy()
    bid = np.where(np.isnan(bid), fallback[None, :], bid)

    def zone_series(zone, col, fill=0.0):
        s = resid[resid["zone"] == zone].set_index("period_start")[col].reindex(periods)
        return s.ffill().bfill().fillna(fill).to_numpy(float)

    # forecast waste per zone, hourly -> half-hourly (latest issue per farm-hour)
    f = fc.sort_values("issued_at").drop_duplicates(["farm_id", "hour_start"], keep="last")
    fz = f.groupby(["zone", "hour_start"])["curtailed_mwh_pred"].sum()

    def fc_series(zone):
        if zone not in fz.index.get_level_values(0):
            return np.full(len(periods), np.nan)
        s = fz.loc[zone]
        s.index = pd.DatetimeIndex(s.index)
        return (s.reindex(periods.floor("h")) / 2).to_numpy(float)

    return Inputs(
        periods=periods, bmu=bmus, farm=units["farm_id"].to_numpy(), north=(units["zone"] == "north_scotland").to_numpy(),
        cap_mw=units["capacity_mw"].to_numpy(float), planned_mw=planned,
        wind_ms=_wide(curt, "wind_speed_100m_ms", periods, bmus), bid_gbp_mwh=bid, hist_curtailed_mwh=hist,
        res_n=zone_series("north_scotland", "residual_mw"), res_s=zone_series("south_scotland", "residual_mw"),
        lim4=zone_series("north_scotland", "boundary_limit_mw", np.inf),
        lim6=zone_series("south_scotland", "boundary_limit_mw", np.inf),
        price=zone_series("north_scotland", "market_price_gbp_mwh"),
        fc_n=fc_series("north_scotland"), fc_s=fc_series("south_scotland"),
    )


def input_source() -> str:
    return data.group_source(*CORE_FILES)


def load_inputs() -> Inputs:
    src = input_source()
    return _load(data.signature(*CORE_FILES, source=src) + data.signature("forecast.parquet"), src)


# ---------- core ----------

def waterfall(m: np.ndarray, excess: np.ndarray) -> np.ndarray:
    """Curtail `excess` (T,) from columns of m (T, U) in column order: first column first."""
    before = np.cumsum(m, axis=1) - m
    return np.clip(excess[:, None] - before, 0.0, m)


def _boundaries(pot_n, pot_s, res_n, res_s, l4, l6, net_n=0.0, net_s=0.0):
    """Zone physics for all periods. net_* = battery injection (+ discharge, - charge), MW."""
    s_n = pot_n + res_n + net_n
    ex4 = np.clip(s_n - l4, 0.0, pot_n)
    flow4 = s_n - ex4
    s_s = pot_s + res_s + flow4 + net_s
    ex6 = np.clip(s_s - l6, 0.0, pot_n + pot_s - ex4)
    return ex4, ex6, flow4, s_s - ex6


def _price_expectation(price: np.ndarray, days: int) -> np.ndarray:
    """Average price for each half hour over the previous 7 days (day 0 uses its own day)."""
    p = price[: days * 48].reshape(days, 48)
    out = np.empty_like(p)
    for d in range(days):
        out[d] = np.nanmean(p[max(0, d - 7):d] if d else p[:1], axis=0)
    return np.nan_to_num(out, nan=np.nanmean(price) if np.isfinite(price).any() else 0.0)


def _run_batteries(inp, sc, pot_n, pot_s, l4, l6, ex4_0, ex6_0, flow4_0, flow6_0):
    """Plan each battery a day ahead with the dispatcher, then execute half hour by half hour."""
    T, days = len(pot_n), len(pot_n) // 48
    bats = sc["batteries"]
    waste_n = (ex4_0 + ex6_0) * H           # MWh a North battery could absorb
    waste_s = ex6_0 * H                     # only B6 waste for a South battery
    head_n = np.maximum(0.0, np.minimum(l4 - flow4_0, l6 - flow6_0))
    head_s = np.maximum(0.0, l6 - flow6_0)
    price_exp = _price_expectation(inp.price, days)
    q = sc["forecast_quality"]

    def signal(zone_north):
        actual = waste_n if zone_north else waste_s
        if q == "perfect":
            return actual, 0
        reactive = np.r_[0.0, actual[:-1]]      # what's happening right now
        if q == "none":
            return reactive, 0
        fc = inp.fc_n if zone_north else inp.fc_s
        return np.where(np.isnan(fc), reactive, fc), int(np.isnan(fc).sum())

    plans = []
    for b in bats:
        north = b["zone"] == "north_scotland"
        sig, fallback = signal(north)
        plans.append({"north": north, "sig": sig, "fallback": fallback, "soc": 0.0,
                      "head": head_n if north else head_s, "charge": np.zeros(T), "discharge": np.zeros(T)})

    net_n, net_s = np.zeros(T), np.zeros(T)
    res_n, res_s = inp.res_n, inp.res_s
    for d in range(days):
        sl = slice(d * 48, d * 48 + 48)
        day_plans = [plan_day(b, p["sig"][sl], price_exp[d], p["head"][sl], p["soc"]) for b, p in zip(bats, plans)]
        for i in range(d * 48, d * 48 + 48):
            j = i - d * 48
            # charging: North batteries first (they can absorb B4 and B6 waste), then South
            s_n = pot_n[i] + res_n[i]
            room_n = ex4_0[i] + ex6_0[i]
            cn = 0.0
            for b, p, dp in zip(bats, plans, day_plans):
                if p["north"] and dp["action"][j] == "charge":
                    c = min(dp["power_mw"][j], b["power_mw"], (b["energy_mwh"] - p["soc"]) / (H * b["efficiency"]), room_n - cn)
                    c = max(c, 0.0); p["charge"][i] = c; p["soc"] += c * H * b["efficiency"]; cn += c
            ex4 = min(max(s_n - cn - l4[i], 0.0), pot_n[i])
            flow4 = s_n - cn - ex4
            s_s = pot_s[i] + res_s[i] + flow4
            ex6 = min(max(s_s - l6[i], 0.0), pot_n[i] + pot_s[i] - ex4)
            cs = 0.0
            for b, p, dp in zip(bats, plans, day_plans):
                if not p["north"] and dp["action"][j] == "charge":
                    c = min(dp["power_mw"][j], b["power_mw"], (b["energy_mwh"] - p["soc"]) / (H * b["efficiency"]), ex6 - cs)
                    c = max(c, 0.0); p["charge"][i] = c; p["soc"] += c * H * b["efficiency"]; cs += c
            flow6 = s_s - cs - max(ex6 - cs, 0.0)
            # discharging: only into spare boundary capacity, so it never creates waste
            for b, p, dp in zip(bats, plans, day_plans):
                if dp["action"][j] != "discharge":
                    continue
                room = min(l4[i] - flow4, l6[i] - flow6) if p["north"] else l6[i] - flow6
                dmw = max(0.0, min(dp["power_mw"][j], b["power_mw"], p["soc"] / H, room))
                p["discharge"][i] = dmw; p["soc"] -= dmw * H
                flow6 += dmw
                if p["north"]:
                    flow4 += dmw
            net_n[i] = -cn + sum(p["discharge"][i] for p in plans if p["north"])
            net_s[i] = -cs + sum(p["discharge"][i] for p in plans if not p["north"])
    return net_n, net_s, plans


def run_core(inp: Inputs, sc: dict) -> dict:
    """Full-period arrays for one scenario (sc must be complete: use with_defaults)."""
    pot = levers.potential_mw(inp.planned_mw, inp.wind_ms, inp.cap_mw, float(sc["weather"]["wind_scale"]))
    l4 = levers.boundary_mw(inp.lim4, sc["b4"])
    l6 = levers.boundary_mw(inp.lim6, sc["b6"])
    pot_n, pot_s = pot[:, inp.north].sum(axis=1), pot[:, ~inp.north].sum(axis=1)
    ex4, ex6, flow4, flow6 = _boundaries(pot_n, pot_s, inp.res_n, inp.res_s, l4, l6)
    plans = []
    if sc["batteries"]:
        net_n, net_s, plans = _run_batteries(inp, sc, pot_n, pot_s, l4, l6, ex4, ex6, flow4, flow6)
        ex4, ex6, flow4, flow6 = _boundaries(pot_n, pot_s, inp.res_n, inp.res_s, l4, l6, net_n, net_s)
        # charging was capped at would-be waste, so this only removes waste; clip float noise
        ex4, ex6 = np.maximum(ex4, 0.0), np.maximum(ex6, 0.0)

    # who gets switched off: B4 waste from North units, then B6 waste from all Scottish units,
    # each in priority order (inp.bmu is already sorted cheapest first)
    cut = np.zeros_like(pot)
    cut[:, inp.north] = waterfall(pot[:, inp.north], ex4)
    cut += waterfall(pot - cut, ex6)
    return {"pot": pot, "cut_mw": cut, "ex4": ex4, "ex6": ex6, "flow4": flow4, "flow6": flow6, "plans": plans}


# ---------- results ----------

def _utc(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _mask(inp: Inputs, window: dict) -> np.ndarray:
    """Periods inside [start, end) of the window (lever 12); open ends = whole period."""
    m = np.ones(len(inp.periods), bool)
    if window.get("start"):
        m &= inp.periods >= _utc(window["start"])
    if window.get("end"):
        m &= inp.periods < _utc(window["end"])
    return m


def _totals(inp: Inputs, r: dict, m: np.ndarray) -> dict:
    cut_mwh = r["cut_mw"][m] * H
    mwh = float(cut_mwh.sum())
    years = max(m.sum(), 1) * H / (365.25 * 24)
    return {
        "curtailed_twh": round(mwh / 1e6, 9),
        "by_boundary_twh": {"B4": round(float(r["ex4"][m].sum()) * H / 1e6, 9),
                            "B6": round(float(r["ex6"][m].sum()) * H / 1e6, 9)},
        "payments_gbp": round(float((cut_mwh * -inp.bid_gbp_mwh[m]).sum()), 1),
        "replacement_gas_gbp": round(mwh * contract.replacement_gbp_mwh(), 1),
        "co2_t": round(mwh * contract.DEFAULT_CARBON_INTENSITY_T_MWH, 1),
        "homes_equiv": round(mwh / contract.MWH_PER_HOME_PER_YEAR / years, 1),
    }


def _farm_mwh(inp: Inputs, r: dict, m: np.ndarray) -> pd.Series:
    return pd.Series((r["cut_mw"][m] * H).sum(axis=0), index=inp.farm).groupby(level=0).sum()


_baseline_cache: dict = {}


def _baseline(inp: Inputs) -> dict:
    key = id(inp)
    if key not in _baseline_cache:
        _baseline_cache.clear()
        _baseline_cache[key] = run_core(inp, with_defaults({}))
    return _baseline_cache[key]


def _is_baseline(sc: dict) -> bool:
    d = with_defaults({})
    return (sc["b4"] == d["b4"] or sc["b4"].get("mode") == "historical") and \
           (sc["b6"] == d["b6"] or sc["b6"].get("mode") == "historical") and \
           not sc["batteries"] and float(sc["weather"]["wind_scale"]) == 1.0


def simulate(scenario: dict) -> dict:
    """Scenario dict as in POST /api/scenario (missing fields = historical) -> results body."""
    sc = with_defaults(scenario)
    inp = load_inputs()
    window = dict(sc["window"])
    if not window.get("start") and not window.get("end") and sc["weather"].get("replay_start"):
        window = {"start": sc["weather"]["replay_start"], "end": sc["weather"].get("replay_end")}
    m = _mask(inp, window)
    base = _baseline(inp)
    res = base if _is_baseline(sc) else run_core(inp, sc)

    s_tot, b_tot = _totals(inp, res, m), _totals(inp, base, m)
    delta = {k: ({kk: round(v[kk] - b_tot[k][kk], 9) for kk in v} if isinstance(v, dict) else round(v - b_tot[k], 9))
             for k, v in s_tot.items()}
    sf, bf = _farm_mwh(inp, res, m), _farm_mwh(inp, base, m)
    batteries = []
    for b, p in zip(sc["batteries"], res["plans"]):
        dis = float(p["discharge"][m].sum()) * H
        batteries.append({
            "battery_id": b["battery_id"],
            "captured_curtailment_mwh": round(float(p["charge"][m].sum()) * H, 3),
            "revenue_gbp": round(float((p["discharge"][m] * H * np.nan_to_num(inp.price[m])).sum()), 1),
            "cycles": round(dis / b["energy_mwh"], 3) if b["energy_mwh"] else 0.0,
            "forecast_fallback_periods": p["fallback"],
        })
    ignored = sorted(k for k, default in NOT_YET_APPLIED.items()
                     if k in (scenario or {}) and scenario[k] not in (None, default) and not _is_default_full(k, scenario[k]))
    win_idx = np.flatnonzero(m)
    return {
        "window": {"start": inp.periods[win_idx[0]].strftime("%Y-%m-%dT%H:%M:%SZ") if len(win_idx) else None,
                   "end": (inp.periods[win_idx[-1]] + pd.Timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
                   if len(win_idx) else None},
        "scenario": s_tot, "baseline": b_tot, "delta": delta, "batteries": batteries,
        "farms": [{"farm_id": f, "curtailed_mwh": round(float(sf[f]), 3), "baseline_curtailed_mwh": round(float(bf[f]), 3)}
                  for f in bf.sort_values(ascending=False).index],
        "dispatcher": DISPATCHER if sc["batteries"] else None,
        "data_source": input_source(),
        "levers_not_yet_applied": ignored,
    }


def _is_default_full(key: str, value) -> bool:
    """Full-tier lever dicts sent with all-default values count as not set."""
    if key == "demand_growth":
        return all(not any(z.values()) for z in value.values()) if isinstance(value, dict) else False
    if key == "switch_off_price":
        return isinstance(value, dict) and value.get("mode", "multiplier") == "multiplier" and value.get("value", 1.0) == 1.0
    return False
