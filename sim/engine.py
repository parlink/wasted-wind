"""The grid simulator: simulate(scenario) -> results (CONTRACT.md §6/§7, BRIEF.md Part 4).

Per half hour, in MW:
  1. potential wind per BMU (planned output, or the power curve on scaled wind: lever 8)
  2. surplus_N = wind_N + residual_N
  3. batteries charge from would-be waste / discharge into boundary headroom (lever 4, 13)
  4. flow_B4 = min(surplus_N, B4); surplus_S = wind_S + residual_S + flow_B4; flow_B6 = min(surplus_S, B6)
  5. waste = what's over a limit; B4 waste from North farms, B6 waste from any Scottish farm,
     split like history did in that half hour, else cheapest bids (least negative) first
  6. costs: switch-off payments, replacement gas, CO2

The engine always runs the full period (so batteries arrive realistically charged) and the
results are sliced to the requested window (lever 12).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
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

try:   # Person A's optimising dispatcher (#13); same signature, used for batteries with "dispatcher": "lp"
    from dispatch.optimiser import plan_day as lp_plan_day
    LP_DISPATCHER = "dispatch.optimiser"
except ImportError:
    lp_plan_day, LP_DISPATCHER = None, None


try:   # #52: the uncertainty-aware planner (plans towards the high end of the forecast band)
    from dispatch import band as band_planner
except ImportError:
    band_planner = None


def band_weight() -> float:
    """The weight tuned on May-Jul in scorecard.json, else the planner's default."""
    try:
        sc, _ = data.json_file("scorecard.json")
        return float(sc.get("band_weight", {}).get("chosen"))
    except Exception:   # noqa: BLE001 - older data or fixtures without it
        return band_planner.DEFAULT_WEIGHT if band_planner else 0.0


def planner_for(battery: dict):
    """(plan_day function, module name) for a scenario battery. Falls back to the rule dispatcher
    when the LP isn't available, and says so in the result."""
    if battery.get("dispatcher") == "lp_band" and band_planner is not None:
        return band_planner.plan_day, "dispatch.band"
    if battery.get("dispatcher") in ("lp", "lp_band") and lp_plan_day is not None:
        return lp_plan_day, LP_DISPATCHER
    return plan_day, DISPATCHER

H = 0.5   # hours per settlement period: MW x H = MWh

DEFAULTS = {
    "b4": {"mode": "historical", "mw": 0.0},
    "b6": {"mode": "historical", "mw": 0.0},
    "batteries": [],
    "weather": {"wind_scale": 1.0, "replay_start": None, "replay_end": None},
    "window": {"start": None, "end": None},
    "forecast_quality": "ours",
}
# Full-tier levers (#15)
NOT_YET_APPLIED: dict = {}   # every lever in CONTRACT §7 is applied (#15)


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
    planned_mw: np.ndarray        # (T, U) potential output, MW: (accepted_mwh + curtailed_mwh_a) / 0.5
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
    fc_n_p90: np.ndarray | None = None   # (T,) high end (p90) of the zone forecast, for the band planner
    fc_s_p90: np.ndarray | None = None
    data_gaps: dict = field(default_factory=dict)       # residual/limit/price gaps and how they were filled
    dropped_bmus: list = field(default_factory=list)    # in curtailment but not in farms.csv
    dropped_curtailed_mwh: float = 0.0
    unit_lat: np.ndarray | None = None   # (U,) farm centroid of each unit, for batteries sited at a farm
    unit_lon: np.ndarray | None = None
    fc_farm: dict = field(default_factory=dict)   # farm_id -> (T,) day-ahead forecast waste, MWh per half hour
    temp_n: np.ndarray | None = None   # (T,) zone temperature, deg C, NaN where unknown (heat-pump lever)
    temp_s: np.ndarray | None = None

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


GAP_FILL_PERIODS = 48   # carry a residual/limit value forward at most one day; longer gaps use the median


@lru_cache(maxsize=2)
def _load(signature: tuple, source: str) -> Inputs:
    # one consistent dataset: real only if curtailment AND residual are real (see data.group_source)
    curt, _ = data.table("curtailment.parquet", source)
    resid, _ = data.table("residual.parquet", source)
    farms, _ = data.table("farms.csv", source)
    _, fc_source = data.resolve("forecast.parquet")
    if fc_source == source:
        fc, _ = data.table("forecast.parquet", source)
    else:   # fake forecast with real farms is meaningless: plan without one
        fc = data.table("forecast.parquet", "fixtures")[0].iloc[:0]

    periods = pd.DatetimeIndex(sorted(curt["period_start"].unique()))
    start, end = periods.min().normalize(), periods.max().normalize() + pd.Timedelta(days=1)
    periods = pd.date_range(start, end, freq="30min", inclusive="left", tz="UTC")   # whole UTC days

    # dispatch priority: least negative median bid first (cheapest for NESO), unknown last
    med = curt.groupby("bmu_id")["bid_price_gbp_mwh"].median()
    units = farms[farms["bmu_id"].isin(curt["bmu_id"].unique())].copy()
    dropped = sorted(set(curt["bmu_id"]) - set(units["bmu_id"]))
    units["med_bid"] = units["bmu_id"].map(med)
    units = units.sort_values("med_bid", ascending=False, na_position="last")
    bmus = units["bmu_id"].tolist()

    # Potential = what the unit would have delivered without *constraint* actions:
    # delivered (accepted) + constraint waste. Not planned (PN): ordinary balancing turn-downs and
    # offers change delivery too, and residual.parquet is built from delivered wind, so using PN
    # would turn every non-constraint turn-down into simulated waste.
    planned = (np.nan_to_num(_wide(curt, "accepted_mwh", periods, bmus))
               + np.nan_to_num(_wide(curt, "curtailed_mwh_a", periods, bmus))) / H
    hist = np.nan_to_num(_wide(curt, "curtailed_mwh_a", periods, bmus))
    bid = _wide(curt, "bid_price_gbp_mwh", periods, bmus)
    fallback = units["med_bid"].fillna(med.median() if med.notna().any() else -50.0).to_numpy()
    bid = np.where(np.isnan(bid), fallback[None, :], bid)

    gaps = {}

    def zone_series(zone, col, fill=0.0):
        """Forward-fill short gaps only (never copy future values back); long gaps -> median."""
        s = resid[resid["zone"] == zone].set_index("period_start")[col].reindex(periods)
        missing = int(s.isna().sum())
        s = s.ffill(limit=GAP_FILL_PERIODS)
        if missing:
            gaps[f"{zone}.{col}"] = {"missing_periods": missing, "filled_with_median": int(s.isna().sum())}
        return s.fillna(s.median() if s.notna().any() else fill).to_numpy(float)

    # forecast waste per farm-hour: only the day-ahead issue (by D-1 10:00 UTC), never later ones
    if len(fc):
        fc = fc[data.lead_days(fc) == 1]          # #49: live rows now run 6 days ahead; plan on the day-ahead
        cutoff = fc["hour_start"].dt.normalize() - pd.Timedelta(hours=14)
        fc = fc[fc["issued_at"] <= cutoff]
    f = fc.sort_values("issued_at").drop_duplicates(["farm_id", "hour_start"], keep="last")
    fz = f.groupby(["zone", "hour_start"])["curtailed_mwh_pred"].sum()
    fz90 = (f.groupby(["zone", "hour_start"])["curtailed_mwh_p90"].sum(min_count=1)
            if "curtailed_mwh_p90" in f else None)

    def fc_series(zone, src=None):
        src = fz if src is None else src
        if zone not in src.index.get_level_values(0):
            return np.full(len(periods), np.nan)
        s = src.loc[zone]
        s.index = pd.DatetimeIndex(s.index)
        return (s.reindex(periods.floor("h")) / 2).to_numpy(float)

    inp = Inputs(
        periods=periods, bmu=bmus, farm=units["farm_id"].to_numpy(), north=(units["zone"] == "north_scotland").to_numpy(),
        cap_mw=units["capacity_mw"].to_numpy(float), planned_mw=planned,
        wind_ms=_wide(curt, "wind_speed_100m_ms", periods, bmus), bid_gbp_mwh=bid, hist_curtailed_mwh=hist,
        res_n=zone_series("north_scotland", "residual_mw"), res_s=zone_series("south_scotland", "residual_mw"),
        lim4=zone_series("north_scotland", "boundary_limit_mw", np.inf),
        lim6=zone_series("south_scotland", "boundary_limit_mw", np.inf),
        price=zone_series("north_scotland", "market_price_gbp_mwh"),
        fc_n=fc_series("north_scotland"), fc_s=fc_series("south_scotland"),
    )
    if fz90 is not None:
        inp.fc_n_p90, inp.fc_s_p90 = fc_series("north_scotland", fz90), fc_series("south_scotland", fz90)
    inp.data_gaps = gaps
    inp.dropped_bmus = dropped
    if {"lat", "lon"} <= set(units.columns):
        inp.unit_lat, inp.unit_lon = units["lat"].to_numpy(float), units["lon"].to_numpy(float)
    if len(f):
        ff = f.pivot_table(index="hour_start", columns="farm_id", values="curtailed_mwh_pred", aggfunc="sum")
        ff.index = pd.DatetimeIndex(ff.index)
        ff = ff.reindex(periods.floor("h")) / 2
        inp.fc_farm = {c: ff[c].to_numpy(float) for c in ff.columns}
    if "temperature_2m_c" in resid.columns:   # optional (#45): heat pumps follow real cold days
        for z, attr in (("north_scotland", "temp_n"), ("south_scotland", "temp_s")):
            t = resid[resid["zone"] == z].set_index("period_start")["temperature_2m_c"].reindex(periods)
            setattr(inp, attr, t.to_numpy(float))
    inp.dropped_curtailed_mwh = float(curt.loc[curt["bmu_id"].isin(dropped), "curtailed_mwh_a"].sum())
    return inp


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


def allocate(m: np.ndarray, excess: np.ndarray, hist: np.ndarray) -> np.ndarray:
    """Split each period's excess (T,) across units (columns of m, MW available) for curtailment.

    Where history curtailed someone in that half hour, follow that pattern: each unit's share of
    the historical curtailment, scaled to the new excess (so historical settings reproduce every
    unit exactly). Whatever is left (no history that period, or a unit runs out of output) goes
    cheapest bid first, i.e. in column order (BRIEF Part 4). Never exceeds m; conserves the excess
    up to the total available.
    """
    hs = hist.sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        share = np.where(hs[:, None] > 0, hist / hs[:, None], 0.0)
    first = np.minimum(share * excess[:, None], m)
    rest = np.maximum(excess - first.sum(axis=1), 0.0)
    return first + waterfall(m - first, rest)


def _boundaries(pot_n, pot_s, res_n, res_s, l4, l6, net_n=0.0, net_s=0.0, lk_n=0.0, lk_s=0.0):
    """Zone physics for all periods. net_* = battery injection (+ discharge, - charge), MW.
    lk_* = subsea link capacity from that zone straight to England (lever 3): it carries the zone's
    surplus first, so that power needs neither B4 nor B6."""
    s_n = pot_n + res_n + net_n
    s_n = s_n - np.clip(s_n, 0.0, lk_n)
    ex4 = np.clip(s_n - l4, 0.0, pot_n)
    flow4 = s_n - ex4
    s_s = pot_s + res_s + flow4 + net_s
    s_s = s_s - np.clip(s_s, 0.0, lk_s)
    ex6 = np.clip(s_s - l6, 0.0, pot_n + pot_s - ex4)
    return ex4, ex6, flow4, s_s - ex6


def _price_expectation(price: np.ndarray, days: int) -> np.ndarray:
    """Average price for each half hour over the previous 7 days. Day 0 has no history, so it
    gets NaN: the dispatcher has no price signal that day rather than peeking at it."""
    p = price[: days * 48].reshape(days, 48)
    out = np.full_like(p, np.nan)
    for d in range(1, days):
        out[d] = np.nanmean(p[max(0, d - 7):d], axis=0)
    return out


def planning_signals(inp: Inputs, q: str, waste_n: np.ndarray, waste_s: np.ndarray):
    """What each battery is told about waste when it plans a day (lever 13).

    perfect: the actual would-be waste (the hindsight ceiling)
    ours:    the day-ahead forecast; hours without one carry no information (0) and are counted
    none:    no information about future waste at all; the battery can only react live
    A North battery can absorb B4 and B6 waste, i.e. waste at any Scottish farm, so it is told
    the North + South forecast; a South battery only B6 waste, so the South forecast.
    Returns (signal_north, signal_south, hours_without_forecast_north, ..._south).
    """
    T = len(waste_n)
    if q == "perfect":
        return waste_n, waste_s, 0, 0
    if q == "none":
        return np.zeros(T), np.zeros(T), 0, 0
    fc_n, fc_s = inp.fc_n[:T], inp.fc_s[:T]
    sig_n = np.nan_to_num(fc_n) + np.nan_to_num(fc_s)
    return sig_n, np.nan_to_num(fc_s), int((np.isnan(fc_n) & np.isnan(fc_s)).sum()), int(np.isnan(fc_s).sum())


FARM_RADIUS_KM = 20.0


def _km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km (haversine)."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))


def farm_units(inp: Inputs, battery: dict) -> np.ndarray:
    """Units a battery sited at a wind farm shares a connection with: every unit of every farm within
    radius_km in the battery's own zone, else the nearest farm in that zone. Empty for a grid battery."""
    if battery.get("site", "grid") != "farm":
        return np.zeros(len(inp.bmu), bool)
    if inp.unit_lat is None or inp.unit_lon is None:
        raise ValueError("a battery sited at a farm needs farm coordinates (lat/lon in farms.csv)")
    in_zone = inp.north if battery["zone"] == "north_scotland" else ~inp.north
    d = _km(battery["lat"], battery["lon"], inp.unit_lat, inp.unit_lon)
    d = np.where(in_zone & np.isfinite(d), d, np.inf)
    near = d <= float(battery.get("radius_km") or FARM_RADIUS_KM)
    if not near.any() and np.isfinite(d).any():
        near = inp.farm == inp.farm[int(np.argmin(d))]   # nearest farm, all its units
    return near


def _north_room(ex4, ex6, flow4, lk_n):
    """MW of would-be waste a North battery can remove. Charging cuts the North surplus: first the B4 overflow,
    then the flow over B4 (which is what relieves B6). With a North subsea link, charging past that point only
    takes power off the link, which removes no waste, so B6 relief is capped at the B4 flow. Without a link the
    cut can continue into southbound-to-northbound flow, as before."""
    if not np.any(np.asarray(lk_n) > 0):
        return ex4 + ex6
    return ex4 + np.minimum(ex6, np.maximum(flow4, 0.0))


def _run_batteries(inp, sc, pot_n, pot_s, l4, l6, ex4_0, ex6_0, flow4_0, flow6_0, phys=None):
    """Plan each battery a day ahead with the dispatcher, then execute half hour by half hour.

    Execution reacts to what is happening now whatever the plan said: a battery with room
    charges from live waste in its zone (it sees constraint instructions as they happen). So
    forecast quality changes only the *plan* (being empty before a storm, not selling stored
    energy too early), and the gap between qualities is the value of the forecast.
    """
    T, days = len(pot_n), len(pot_n) // 48
    bats = sc["batteries"]
    lk_n0 = (phys or {}).get("lk_n", 0.0)
    waste_n = _north_room(ex4_0, ex6_0, flow4_0, lk_n0) * H   # MWh a North battery could absorb
    waste_s = ex6_0 * H                     # only B6 waste for a South battery
    head_n = np.maximum(0.0, np.minimum(l4 - flow4_0, l6 - flow6_0))
    head_s = np.maximum(0.0, l6 - flow6_0)
    price_exp = _price_expectation(inp.price, days)
    sig_n, sig_s, fb_n, fb_s = planning_signals(inp, sc["forecast_quality"], waste_n, waste_s)

    cut0 = (phys or {}).get("cut0")            # (T, U) would-be waste per unit, for batteries sited at a farm
    c4, c6 = (phys or {}).get("cut0_b4"), (phys or {}).get("cut0_b6")   # its B4 and B6 parts
    cut_after = np.zeros_like(cut0) if cut0 is not None else None       # what is left after every battery charged
    plans = []
    for b in bats:
        north = b["zone"] == "north_scotland"
        units = farm_units(inp, b)
        p = {"north": north, "sig": sig_n if north else sig_s, "fallback": fb_n if north else fb_s,
             "soc": 0.0, "head": head_n if north else head_s, "units": units, "farm_ids": sorted(set(inp.farm[units])),
             "charge": np.zeros(T), "discharge": np.zeros(T), "soc_mwh": np.zeros(T)}
        if (b.get("dispatcher") == "lp_band" and band_planner is not None and sc["forecast_quality"] == "ours"
                and not units.any() and inp.fc_n_p90 is not None):
            # #52: plan towards the high end of the band. A North battery sees North + South, like sig_n
            hi = (np.nan_to_num(inp.fc_n_p90[:T]) + np.nan_to_num(inp.fc_s_p90[:T])) if north else inp.fc_s_p90[:T]
            p["sig"] = band_planner.band_signal(p["sig"], hi, band_weight())
        if units.any() and cut0 is not None:
            # it can only ever see and catch its own farms' waste
            local = cut0[:, units].sum(axis=1) * H
            q = sc["forecast_quality"]
            if q == "perfect":
                p["sig"] = local
            elif q == "none":
                p["sig"] = np.zeros(T)
            else:
                fc = [inp.fc_farm[f][:T] for f in p["farm_ids"] if f in inp.fc_farm]
                p["sig"] = np.nansum(fc, axis=0) if fc else np.zeros(T)
                p["fallback"] = int(np.all(np.isnan(fc), axis=0).sum()) if fc else T
        plans.append(p)

    everyone = np.ones(len(inp.bmu), bool)

    def draw(c, units, b4_first):
        """Use up c MW of the units' remaining waste: B4 part first for a North battery (charging cuts the
        North surplus, so the B4 overflow goes first), then B6; only B6 for a South battery, then anything left
        (the physics guarantees the total is there; the split between parts is an accounting choice)."""
        pools = [(rem4, units), (rem6, units)] if b4_first else [(rem6, units)]
        pools += [(rem4, units), (rem6, everyone), (rem4, everyone)]
        for pool, m in pools:
            if c <= 1e-12:
                break
            w = pool[m]
            tot = float(w.sum())
            if tot <= 0:
                continue
            take = min(c, tot)
            pool[m] = np.maximum(w - take * w / tot, 0.0)
            c -= take

    def charge(b, p, want, cap):
        room = (b["energy_mwh"] - p["soc"]) / (H * b["efficiency"])
        farm = cut0 is not None and p["units"].any()
        if farm:   # a farm battery: only its own farms' remaining waste this half hour
            cap = min(cap, float(rem4[p["units"]].sum() + rem6[p["units"]].sum()))
        c = max(0.0, min(want, b["power_mw"], room, cap))
        if c > 0 and cut0 is not None:   # every battery's charge comes out of somebody's waste
            draw(c, p["units"] if farm else (inp.north if p["north"] else everyone), p["north"])
        return c

    net_n, net_s = np.zeros(T), np.zeros(T)
    phys = phys or {}
    res_n, res_s = phys.get("res_n", inp.res_n), phys.get("res_s", inp.res_s)
    lk_n, lk_s = phys.get("lk_n", 0.0), phys.get("lk_s", 0.0)
    for d in range(days):
        sl = slice(d * 48, d * 48 + 48)
        day_plans = [planner_for(b)[0](b, p["sig"][sl], price_exp[d], p["head"][sl], p["soc"]) for b, p in zip(bats, plans)]
        for i in range(d * 48, d * 48 + 48):
            j = i - d * 48
            if cut0 is not None:   # each unit's waste not yet caught this half hour, by bottleneck
                rem4, rem6 = c4[i].copy(), c6[i].copy()
            # charging from would-be waste: North batteries first (B4 and B6 waste), then South
            s_n = pot_n[i] + res_n[i]
            room_n = _north_room(ex4_0[i], ex6_0[i], flow4_0[i], lk_n)
            cn = 0.0
            for b, p, dp in zip(bats, plans, day_plans):
                if p["north"] and dp["action"][j] != "discharge":
                    want = dp["power_mw"][j] if dp["action"][j] == "charge" else b["power_mw"]
                    c = charge(b, p, want, room_n - cn)
                    p["charge"][i] = c; p["soc"] += c * H * b["efficiency"]; cn += c
            sn = s_n - cn
            sn -= min(max(sn, 0.0), lk_n)          # subsea link first (lever 3)
            ex4 = min(max(sn - l4[i], 0.0), pot_n[i])
            flow4 = sn - ex4
            s_s = pot_s[i] + res_s[i] + flow4
            s_s -= min(max(s_s, 0.0), lk_s)
            ex6 = min(max(s_s - l6[i], 0.0), pot_n[i] + pot_s[i] - ex4)
            cs = 0.0
            for b, p, dp in zip(bats, plans, day_plans):
                if not p["north"] and dp["action"][j] != "discharge":
                    want = dp["power_mw"][j] if dp["action"][j] == "charge" else b["power_mw"]
                    c = charge(b, p, want, ex6 - cs)
                    p["charge"][i] = c; p["soc"] += c * H * b["efficiency"]; cs += c
            flow6 = s_s - cs - max(ex6 - cs, 0.0)
            # discharging: only into spare boundary capacity, so it never creates waste
            for b, p, dp in zip(bats, plans, day_plans):
                if dp["action"][j] == "discharge":
                    room = min(l4[i] - flow4, l6[i] - flow6) if p["north"] else l6[i] - flow6
                    dmw = max(0.0, min(dp["power_mw"][j], b["power_mw"], p["soc"] / H, room))
                    p["discharge"][i] = dmw; p["soc"] -= dmw * H
                    flow6 += dmw
                    if p["north"]:
                        flow4 += dmw
                p["soc_mwh"][i] = p["soc"]
            if cut0 is not None:
                cut_after[i] = rem4 + rem6
            net_n[i] = -cn + sum(p["discharge"][i] for p in plans if p["north"])
            net_s[i] = -cs + sum(p["discharge"][i] for p in plans if not p["north"])
    if cut_after is not None:
        phys["cut_after"] = cut_after
    return net_n, net_s, plans


def run_core(inp: Inputs, sc: dict) -> dict:
    """Full-period arrays for one scenario (sc must be complete: use with_defaults)."""
    pot = levers.potential_mw(inp.planned_mw, inp.wind_ms, inp.cap_mw, float(sc["weather"]["wind_scale"]))
    l4 = levers.boundary_mw(inp.lim4, sc["b4"])
    l6 = levers.boundary_mw(inp.lim6, sc["b6"])
    pot_n, pot_s = pot[:, inp.north].sum(axis=1), pot[:, ~inp.north].sum(axis=1)
    # lever 6: new demand in a zone uses its own wind first, so it lowers the zone's residual
    dg = sc.get("demand_growth") or {}
    res_n = inp.res_n - levers.demand_growth_mw(inp.periods, dg.get("north_scotland"), inp.temp_n)
    res_s = inp.res_s - levers.demand_growth_mw(inp.periods, dg.get("south_scotland"), inp.temp_s)
    lk_n, lk_s = levers.subsea_link_mw(sc.get("subsea_link"))   # lever 3
    phys = dict(res_n=res_n, res_s=res_s, lk_n=lk_n, lk_s=lk_s)
    ex4, ex6, flow4, flow6 = _boundaries(pot_n, pot_s, res_n, res_s, l4, l6, lk_n=lk_n, lk_s=lk_s)
    plans = []
    hist_mw = inp.hist_curtailed_mwh / H
    if any(b.get("site", "grid") == "farm" for b in sc["batteries"]):
        # who would be switched off without batteries, split by bottleneck
        phys["cut0_b4"], phys["cut0_b6"] = _cut(pot, ex4, ex6, hist_mw, inp.north, split=True)
        phys["cut0"] = phys["cut0_b4"] + phys["cut0_b6"]
    if sc["batteries"]:
        net_n, net_s, plans = _run_batteries(inp, sc, pot_n, pot_s, l4, l6, ex4, ex6, flow4, flow6, phys)
        ex4, ex6, flow4, flow6 = _boundaries(pot_n, pot_s, res_n, res_s, l4, l6, net_n, net_s, lk_n, lk_s)
        # charging was capped at would-be waste, so this only removes waste; clip float noise
        ex4, ex6 = np.maximum(ex4, 0.0), np.maximum(ex6, 0.0)

    # who gets switched off: B4 waste from North units, then B6 waste from all Scottish units,
    # following each half hour's historical pattern, else cheapest first (see allocate)
    if "cut_after" in phys:
        # each battery's charge was taken out of specific units' waste (farm batteries: their own farms), so
        # the per-unit waste is simply what is left; row totals equal ex4 + ex6 (tested)
        cut = phys["cut_after"]
    else:
        cut = _cut(pot, ex4, ex6, hist_mw, inp.north)
    return {"pot": pot, "cut_mw": cut, "ex4": ex4, "ex6": ex6, "flow4": flow4, "flow6": flow6, "plans": plans}


def _cut(pot, ex4, ex6, hist_mw, north, split=False):
    """Who gets switched off: B4 waste from North units, then B6 waste from all Scottish units,
    following each half hour's historical pattern, else cheapest first (see allocate).
    split=True returns the (B4 part, B6 part) per unit instead of their sum."""
    b4 = np.zeros_like(pot)
    b4[:, north] = allocate(pot[:, north], ex4, hist_mw[:, north])
    b6 = allocate(pot - b4, ex6, np.maximum(hist_mw - b4, 0.0))
    return (b4, b6) if split else b4 + b6



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


def _payments(inp: Inputs, cut_mwh: np.ndarray, m: np.ndarray, sop: dict | None) -> float:
    """Lever 11: switch-off payments at the farms' bid prices, scaled, or at one flat price for all."""
    sop = sop or {}
    value = sop.get("value")
    if value is None:                       # unset in either mode: the farms' own bids
        return float((cut_mwh * -inp.bid_gbp_mwh[m]).sum())
    if float(value) < 0:
        raise ValueError("switch_off_price.value must be >= 0 (a multiplier, or £ per MWh paid to the farm)")
    if sop.get("mode") == "flat":
        return float(cut_mwh.sum()) * float(value)
    return float((cut_mwh * -inp.bid_gbp_mwh[m]).sum()) * float(value)


def _totals(inp: Inputs, r: dict, m: np.ndarray, sc: dict | None = None) -> dict:
    sc = sc or {}
    cut_mwh = r["cut_mw"][m] * H
    mwh = float(cut_mwh.sum())
    years = max(m.sum(), 1) * H / (365.25 * 24)
    pick = lambda k, d: d if sc.get(k) is None else float(sc[k])   # noqa: E731 - 0 is a real price, not "unset"
    gas = pick("gas_price_p_therm", contract.DEFAULT_GAS_P_THERM)
    carbon = pick("carbon_price_gbp_t", contract.DEFAULT_CARBON_GBP_T)
    intensity = pick("carbon_intensity_t_mwh", contract.DEFAULT_CARBON_INTENSITY_T_MWH)
    return {
        "curtailed_twh": round(mwh / 1e6, 9),
        "by_boundary_twh": {"B4": round(float(r["ex4"][m].sum()) * H / 1e6, 9),
                            "B6": round(float(r["ex6"][m].sum()) * H / 1e6, 9)},
        "payments_gbp": round(_payments(inp, cut_mwh, m, sc.get("switch_off_price")), 1),
        "replacement_gas_gbp": round(mwh * contract.replacement_gbp_mwh(gas, carbon, intensity), 1),
        "co2_t": round(mwh * intensity, 1),
        "homes_equiv": round(mwh / contract.MWH_PER_HOME_PER_YEAR / years, 1),
    }


def _farm_mwh(inp: Inputs, r: dict, m: np.ndarray) -> pd.Series:
    return pd.Series((r["cut_mw"][m] * H).sum(axis=0), index=inp.farm).groupby(level=0).sum()


_baseline_cache: dict = {}


def _baseline(inp: Inputs) -> dict:
    # compare by identity while holding a reference, so a recycled id() can never match
    if _baseline_cache.get("inp") is not inp:
        _baseline_cache.update(inp=inp, res=run_core(inp, with_defaults({})))
    return _baseline_cache["res"]


def _is_baseline(sc: dict) -> bool:
    d = with_defaults({})
    return (sc["b4"] == d["b4"] or sc["b4"].get("mode") == "historical") and \
           (sc["b6"] == d["b6"] or sc["b6"].get("mode") == "historical") and \
           not sc["batteries"] and float(sc["weather"]["wind_scale"]) == 1.0 and \
           not (sc.get("subsea_link") and float(sc["subsea_link"].get("gw", 0)) > 0) and \
           _is_default_full("demand_growth", sc.get("demand_growth") or {})


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

    s_tot, b_tot = _totals(inp, res, m, sc), _totals(inp, base, m, sc)   # same prices: deltas are physical only
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
            "site": b.get("site", "grid"),
            "farm_ids": p.get("farm_ids", []),
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
        "dispatchers": {b["battery_id"]: planner_for(b)[1] for b in sc["batteries"]},
        "data_source": input_source(),
        "input_quality": {"gaps": inp.data_gaps, "dropped_bmus": inp.dropped_bmus,
                          "dropped_curtailed_mwh": round(inp.dropped_curtailed_mwh, 1)},
        "levers_not_yet_applied": ignored,
    }


def _is_default_full(key: str, value) -> bool:
    """Full-tier lever dicts sent with all-default values count as not set."""
    if key == "demand_growth":
        return all(not any((z or {}).values()) for z in value.values()) if isinstance(value, dict) else False
    if key == "switch_off_price":
        return isinstance(value, dict) and value.get("mode", "multiplier") == "multiplier" and value.get("value", 1.0) == 1.0
    return False
