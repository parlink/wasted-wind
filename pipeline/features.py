"""Part 2 signals: one row per farm per hour, every signal known by the forecast issue time.

A forecast for day D is issued at D-1 10:00 UTC (CONTRACT §4). Every signal carries an `asof_*`
timestamp (when it became known) and `check_no_leakage` asserts asof <= issued_at for every row.

| Signal                   | Source                                         | Known at                       |
| ------------------------ | ---------------------------------------------- | ------------------------------ |
| wind_fc_ms               | Open-Meteo previous runs, `previous_day2`      | valid time - 48 h              |
| pot_*_mw, b4/b6 excess   | generic power curve on wind_fc x capacity      | same as wind                   |
| b4/b6_limit_mw           | raw NESO day-ahead limits, ffill <= 2 h only   | assumed D-1 10:00 (see below)  |
| gb_demand_fc_mw          | Elexon NDF, latest publish <= issue time       | that publishTime               |
| lag48_mwh                | Part 1 waste in the same hour 2 days earlier   | end of that hour               |
| trailing_waste_rate      | Part 1 waste over the 30 days ending D-2 00:00 | D-2 00:00                      |
| bid_median_gbp_mwh       | median accepted bid price, same window         | D-2 00:00                      |

Leads (#49): a forecast issued k days ahead (k = 1..6, issued D-k 10:00) uses the same signals with
every cut-off moved back k-1 days: weather run previous_day(k+1), the same hour k+1 days earlier
instead of lag48, the 30-day window ending D-(k+1), and the demand forecast published by D-k 10:00.
NESO limits are only published the day before, so leads 2+ have none (NaN). Lead 7 would need a
run 8 days old, which Open-Meteo doesn't keep, so the week view stops at 6 days ahead.

NESO's day-ahead file has no publication timestamp. "Day ahead" limits are treated as known
by D-1 10:00; if NESO publishes later than that, this is the one place the backtest is optimistic.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

ISSUE_OFFSET = pd.Timedelta(hours=14)        # D 00:00 - 14 h = D-1 10:00
WIND_LEAD = pd.Timedelta(hours=48)           # previous_day2
LEADS = range(1, 7)                          # days ahead; lead 1 = the day-ahead forecast
CUT_IN_MS, RATED_MS, CUT_OUT_MS = 3.0, 12.0, 25.0

FEATURES = ["farm_code", "capacity_mw", "wind_fc_ms", "pot_farm_mw", "pot_north_mw", "pot_south_mw",
            "b4_limit_mw", "b6_limit_mw", "b4_excess_potential_mw", "b6_excess_potential_mw",
            "gb_demand_fc_mw", "lag48_mwh", "trailing_waste_rate", "bid_median_gbp_mwh",
            "hour", "weekday", "month"]
# Order-book signals (#51) are built for every row but NOT in the main model: tested walk-forward
# they didn't help (same MAE, lower recall, worse monthly totals); see ablations in model_metrics.
OB_FEATURES = ["ob_bid_median_north_gbp_mwh", "ob_bid_median_south_gbp_mwh", "ob_cheap_bid_mw_north"]


# ---------- small, tested pieces ----------

def issue_time(hour_start, lead_days: int = 1) -> pd.DatetimeIndex:
    """Issue time of the forecast covering each hour: D-1 10:00 UTC, or D-k 10:00 for lead k."""
    return pd.DatetimeIndex(hour_start).normalize() - ISSUE_OFFSET - pd.Timedelta(days=lead_days - 1)


def weather_run_days(lead_days: int) -> int:
    """Open-Meteo previous_dayN to use for lead k: the freshest run issued by D-k 10:00 for all of D."""
    return lead_days + 1


def generic_cf(wind_ms: np.ndarray) -> np.ndarray:
    """Normalised power curve: cubic from cut-in to rated, flat to cut-out, then 0."""
    v = np.asarray(wind_ms, float)
    cf = np.clip((v ** 3 - CUT_IN_MS ** 3) / (RATED_MS ** 3 - CUT_IN_MS ** 3), 0, 1)
    return np.where(v > CUT_OUT_MS, 0.0, cf)


def demand_forecast(ndf: pd.DataFrame, issued_at: pd.Timestamp | None = None, lead_days: int = 1) -> pd.DataFrame:
    """Latest national demand forecast published by each half hour's issue time.

    issued_at: one issue time for every row (live mode); default = each row's D-k 10:00.
    Returns a frame indexed by startTime with gb_demand_fc_mw and publish_time (the publishTime used).
    """
    cutoff = issue_time(ndf.startTime, lead_days) if issued_at is None else issued_at
    d = ndf[ndf.publishTime <= cutoff]
    d = d.loc[d.groupby("startTime").publishTime.idxmax()]
    return pd.DataFrame({"gb_demand_fc_mw": d.demand.to_numpy(float), "publish_time": d.publishTime.to_numpy()},
                        index=pd.DatetimeIndex(d.startTime, name="startTime"))


def lag_hours(y: pd.DataFrame, hours: int) -> pd.DataFrame:
    """Value `hours` earlier, indexed by (farm_id, hour_start)."""
    s = y.assign(hour_start=y.hour_start + pd.Timedelta(hours=hours))
    return s.set_index(["farm_id", "hour_start"])[["curtailed_mwh"]].rename(
        columns={"curtailed_mwh": f"lag{hours}"})


def trailing_daily(df: pd.DataFrame, value: str, days: int, how: str, gap_days: int = 2) -> pd.DataFrame:
    """Per farm, aggregate of daily values over [D - days - gap, D - gap), indexed by (farm_id, day D).

    how="sum": sum of hourly values; how="median": median of the daily medians (NaN-aware).
    """
    day = df.hour_start.dt.floor("D")
    daily = df.groupby(["farm_id", day])[value].agg("sum" if how == "sum" else "median")
    end = day.max() + pd.Timedelta(days=gap_days)   # same end for every farm, so recent days exist
    out = []
    for farm, s in daily.groupby(level=0):
        s = s.droplevel(0)
        idx = pd.date_range(s.index.min(), end, freq="D")
        s = s.reindex(idx)
        if how == "sum":
            r = s.fillna(0.0).rolling(days, min_periods=days).sum()
        else:
            r = s.rolling(days, min_periods=1).median()
        r = r.shift(gap_days + 1)   # gap 2: value for D = days D-32 .. D-3, i.e. data before D-2 00:00
        out.append(pd.DataFrame({"farm_id": farm, "day": idx, "trailing": r.to_numpy()}))
    return pd.concat(out).set_index(["farm_id", "day"])


def check_no_leakage(df: pd.DataFrame) -> None:
    """Every asof_* column must be <= issued_at on every row (nulls allowed)."""
    for c in [c for c in df.columns if c.startswith("asof_")]:
        asof = df[c] if df[c].notna().any() else pd.Series(pd.NaT, index=df.index, dtype=df.issued_at.dtype)
        bad = asof.notna() & (asof > df.issued_at)    # an all-missing column comes back as float NaN
        assert not bad.any(), f"{c}: {int(bad.sum())} rows use data from after issued_at"


# ---------- loading ----------

def _concat_parquet(files, columns=None) -> pd.DataFrame:
    files = [f for f in files if pq.read_schema(f).names]
    return pd.concat([pd.read_parquet(f, columns=columns) for f in files], ignore_index=True)


def load_weather_forecast(lead_days: int = 1) -> pd.DataFrame:
    col = f"wind_speed_100m_previous_day{weather_run_days(lead_days)}"
    folder = "previous_runs" if lead_days == 1 else "previous_runs_week"
    w = _concat_parquet(sorted((RAW / "openmeteo" / folder).glob("*.parquet")), ["farm_id", "time", col])
    return (w.drop_duplicates(["farm_id", "time"], keep="last")
             .rename(columns={"time": "hour_start", col: "wind_fc_ms"}))


def hourly_target(curt: pd.DataFrame) -> pd.DataFrame:
    """Σ curtailed_mwh_a per farm per hour (only hours where the farm was operating)."""
    g = curt.groupby(["farm_id", "zone", curt.period_start.dt.floor("h").rename("hour_start")])
    return g.curtailed_mwh_a.sum().rename("curtailed_mwh").reset_index()


# ---------- assemble ----------

LIMIT_CARRY_PERIODS = 4          # carry a missing NESO limit forward at most 2 h
MIN_PLAUSIBLE_LIMIT_MW = 100.0   # NESO occasionally publishes 0


def neso_limits_hourly(wide: pd.DataFrame) -> pd.DataFrame:
    """Hourly B4 / B6 limits (MW) from NESO's half-hourly day-ahead series.

    `wide` is residual.boundary_series output (index period_start; b4_limit_mw, b6_limit_mw).
    Gaps are only ever filled from EARLIER values (at most 2 h), never interpolated or
    back-filled, so no limit depends on data published later. Longer gaps stay NaN.
    """
    out = {}
    for b in ("b4", "b6"):
        s = wide[f"{b}_limit_mw"].where(wide[f"{b}_limit_mw"] >= MIN_PLAUSIBLE_LIMIT_MW)
        s = s.ffill(limit=LIMIT_CARRY_PERIODS)
        out[b.upper()] = s.groupby(s.index.floor("h")).mean()
    return pd.DataFrame(out).rename_axis("hour_start")


def commissioning(curt: pd.DataFrame) -> pd.Series:
    """First half hour each farm appears in Part 1 (its first PN): a public, ex-ante fact."""
    return curt.groupby("farm_id").period_start.min()


def zone_potential(weather: pd.DataFrame, farms: pd.DataFrame, commissioned: pd.Series) -> pd.DataFrame:
    """Forecast potential (MW) per zone per hour over every farm in the list that is built.

    Uses the farm list, not which farms happened to report PN that hour (ex-post information).
    """
    cap = farms.groupby("farm_id").capacity_mw.sum()
    zone = farms.drop_duplicates("farm_id").set_index("farm_id").zone
    w = weather[weather.farm_id.isin(cap.index)].copy()
    built = w.hour_start >= w.farm_id.map(commissioned)
    w["pot"] = np.where(built, w.farm_id.map(cap) * generic_cf(w.wind_fc_ms.to_numpy()), 0.0)
    w["zone"] = w.farm_id.map(zone)
    z = w.pivot_table(index="hour_start", columns="zone", values="pot", aggfunc="sum")
    return z.reindex(columns=["north_scotland", "south_scotland"]).fillna(0.0)


def hourly_demand(ndf: pd.DataFrame, issued_at: pd.Timestamp | None = None, lead_days: int = 1) -> pd.DataFrame:
    dem = demand_forecast(ndf, issued_at, lead_days)
    return dem.groupby(dem.index.floor("h")).agg(gb_demand_fc_mw=("gb_demand_fc_mw", "mean"),
                                                 publish_time=("publish_time", "max"))


def build_panel(curt: pd.DataFrame, farms: pd.DataFrame, weather: pd.DataFrame,
                limits: pd.DataFrame, ndf: pd.DataFrame, ob_daily: pd.DataFrame | None = None,
                lead_days: int = 1) -> pd.DataFrame:
    """All signals plus the target, one row per farm-hour with a weather forecast.

    limits: neso_limits_hourly output. weather: the run for this lead (load_weather_forecast(lead_days)).
    """
    y = hourly_target(curt)
    p = y.merge(weather, on=["farm_id", "hour_start"], how="inner")
    p = p[p.wind_fc_ms.notna()].copy()
    p["issued_at"] = issue_time(p.hour_start, lead_days)
    p["asof_wind"] = p.hour_start - pd.Timedelta(days=weather_run_days(lead_days))
    zone_pot = zone_potential(weather, farms, commissioning(curt))
    return add_signals(p, curt, y, farms, limits, hourly_demand(ndf, lead_days=lead_days), zone_pot, ob_daily,
                       lead_days)


def add_signals(p: pd.DataFrame, curt: pd.DataFrame, y: pd.DataFrame, farms: pd.DataFrame,
                lim: pd.DataFrame, dem_h: pd.DataFrame, zone_pot: pd.DataFrame, ob_daily: pd.DataFrame | None = None,
                lead_days: int = 1) -> pd.DataFrame:
    """Add every signal to p (farm_id, zone, hour_start, wind_fc_ms, issued_at, asof_wind).

    Shared by the backtest panel and the live "tomorrow" rows, so both see identical features.
    `y` is the hourly Part 1 history used for the lag and trailing signals.
    lead_days k > 1: the lag and trailing windows end k-1 days earlier and there are no NESO limits.
    lag48_mwh keeps its name (it is the model's column) but holds the same hour k+1 days earlier.
    """
    gap = lead_days + 1                               # latest whole day known at D-k 10:00 is D-(k+1)
    p = p.copy()
    cap = farms.groupby("farm_id").capacity_mw.sum()
    p["capacity_mw"] = p.farm_id.map(cap)
    p["pot_farm_mw"] = p.capacity_mw * generic_cf(p.wind_fc_ms.to_numpy())

    p["pot_north_mw"] = p.hour_start.map(zone_pot["north_scotland"]).fillna(0.0)
    p["pot_south_mw"] = p.hour_start.map(zone_pot["south_scotland"]).fillna(0.0)

    if lead_days == 1:
        p["b4_limit_mw"] = p.hour_start.map(lim["B4"])
        p["b6_limit_mw"] = p.hour_start.map(lim["B6"])
        if "asof_limits" not in p:
            p["asof_limits"] = p.issued_at            # assumption documented in the module docstring
    else:                                             # day-ahead limits don't exist yet at D-k 10:00
        p["b4_limit_mw"] = p["b6_limit_mw"] = np.nan
    p["b4_excess_potential_mw"] = p.pot_north_mw - p.b4_limit_mw
    p["b6_excess_potential_mw"] = p.pot_north_mw + p.pot_south_mw - p.b6_limit_mw

    p["gb_demand_fc_mw"] = p.hour_start.map(dem_h.gb_demand_fc_mw)
    p["asof_demand"] = p.hour_start.map(dem_h.publish_time)

    lag = lag_hours(y, 24 * gap)
    p["lag48_mwh"] = pd.MultiIndex.from_frame(p[["farm_id", "hour_start"]]).map(lag[f"lag{24 * gap}"])
    p["asof_lag48"] = p.hour_start - pd.Timedelta(hours=24 * gap - 1)

    day = p.hour_start.dt.floor("D")
    key = pd.MultiIndex.from_arrays([p.farm_id, day])
    trail = trailing_daily(y, "curtailed_mwh", 30, "sum", gap)
    p["trailing_waste_rate"] = key.map(trail.trailing) / (p.capacity_mw * 24 * 30)
    bids = curt[(curt.curtailed_mwh_a > 0) & curt.bid_price_gbp_mwh.notna()]
    bids = bids.assign(hour_start=bids.period_start)
    p["bid_median_gbp_mwh"] = key.map(trailing_daily(bids, "bid_price_gbp_mwh", 30, "median", gap).trailing)
    p["asof_trailing"] = day - pd.Timedelta(days=gap)

    # order book (#51): bid signals from D-2, the latest complete day at the D-1 10:00 issue.
    # Day-ahead backtest only (the ablation); not in the main model, so not needed elsewhere.
    if ob_daily is not None and lead_days == 1:
        from pipeline import orderbook as ob
        obh = ob.for_hours(ob_daily, p.hour_start)
        for c in ob.SIGNALS:
            p[c] = obh[c].to_numpy()
        p["asof_orderbook"] = obh.asof_orderbook.to_numpy()

    p["hour"], p["weekday"], p["month"] = p.hour_start.dt.hour, p.hour_start.dt.weekday, p.hour_start.dt.month
    p["farm_code"] = p.farm_id.map({f: i for i, f in enumerate(sorted(cap.index))})  # stable codes
    return p.sort_values(["hour_start", "farm_id"]).reset_index(drop=True)


def load_and_build() -> pd.DataFrame:
    curt = pd.read_parquet(PROCESSED / "curtailment.parquet",
                           columns=["farm_id", "zone", "period_start", "curtailed_mwh_a", "bid_price_gbp_mwh"])
    farms = pd.read_csv(ROOT / "reference" / "farms.csv")
    ndf = _concat_parquet(sorted((RAW / "elexon" / "NDF").glob("*.parquet")),
                          ["publishTime", "startTime", "demand"])
    from pipeline import orderbook as ob
    return build_panel(curt, farms, load_weather_forecast(), load_limits(), ndf, ob.load_daily(farms))


def load_limits() -> pd.DataFrame:
    """Raw NESO day-ahead limits (not residual.parquet, whose gaps are interpolated)."""
    from pipeline import residual as rs
    neso = rs.parse_neso(pd.read_csv(RAW / "neso" / "day_ahead_constraints.csv", encoding="utf-8-sig"))
    return neso_limits_hourly(rs.boundary_series(neso))
