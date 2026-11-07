"""Part 2 signals: one row per farm per hour, every signal known by the forecast issue time.

A forecast for day D is issued at D-1 10:00 UTC (CONTRACT §4). Every signal carries an `asof_*`
timestamp (when it became known) and `check_no_leakage` asserts asof <= issued_at for every row.

| Signal                   | Source                                         | Known at                       |
| ------------------------ | ---------------------------------------------- | ------------------------------ |
| wind_fc_ms               | Open-Meteo previous runs, `previous_day2`      | valid time - 48 h              |
| pot_*_mw, b4/b6 excess   | generic power curve on wind_fc x capacity      | same as wind                   |
| b4/b6_limit_mw           | NESO day-ahead constraint limits               | assumed D-1 10:00 (see below)  |
| gb_demand_fc_mw          | Elexon NDF, latest publish <= issue time       | that publishTime               |
| lag48_mwh                | Part 1 waste in the same hour 2 days earlier   | end of that hour               |
| trailing_waste_rate      | Part 1 waste over the 30 days ending D-2 00:00 | D-2 00:00                      |
| bid_median_gbp_mwh       | median accepted bid price, same window         | D-2 00:00                      |

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
CUT_IN_MS, RATED_MS, CUT_OUT_MS = 3.0, 12.0, 25.0

FEATURES = ["farm_code", "capacity_mw", "wind_fc_ms", "pot_farm_mw", "pot_north_mw", "pot_south_mw",
            "b4_limit_mw", "b6_limit_mw", "b4_excess_potential_mw", "b6_excess_potential_mw",
            "gb_demand_fc_mw", "lag48_mwh", "trailing_waste_rate", "bid_median_gbp_mwh",
            "hour", "weekday", "month"]


# ---------- small, tested pieces ----------

def issue_time(hour_start) -> pd.DatetimeIndex:
    """Issue time of the day-ahead forecast covering each hour: D-1 10:00 UTC."""
    return pd.DatetimeIndex(hour_start).normalize() - ISSUE_OFFSET


def generic_cf(wind_ms: np.ndarray) -> np.ndarray:
    """Normalised power curve: cubic from cut-in to rated, flat to cut-out, then 0."""
    v = np.asarray(wind_ms, float)
    cf = np.clip((v ** 3 - CUT_IN_MS ** 3) / (RATED_MS ** 3 - CUT_IN_MS ** 3), 0, 1)
    return np.where(v > CUT_OUT_MS, 0.0, cf)


def demand_forecast(ndf: pd.DataFrame, issued_at: pd.Timestamp | None = None) -> pd.DataFrame:
    """Latest national demand forecast published by each half hour's issue time.

    issued_at: one issue time for every row (live mode); default = each row's D-1 10:00.
    Returns a frame indexed by startTime with gb_demand_fc_mw and publish_time (the publishTime used).
    """
    cutoff = issue_time(ndf.startTime) if issued_at is None else issued_at
    d = ndf[ndf.publishTime <= cutoff]
    d = d.loc[d.groupby("startTime").publishTime.idxmax()]
    return pd.DataFrame({"gb_demand_fc_mw": d.demand.to_numpy(float), "publish_time": d.publishTime.to_numpy()},
                        index=pd.DatetimeIndex(d.startTime, name="startTime"))


def lag_hours(y: pd.DataFrame, hours: int) -> pd.DataFrame:
    """Value `hours` earlier, indexed by (farm_id, hour_start)."""
    s = y.assign(hour_start=y.hour_start + pd.Timedelta(hours=hours))
    return s.set_index(["farm_id", "hour_start"])[["curtailed_mwh"]].rename(
        columns={"curtailed_mwh": f"lag{hours}"})


def trailing_daily(df: pd.DataFrame, value: str, days: int, how: str) -> pd.DataFrame:
    """Per farm, aggregate of daily values over [D - days - 2, D - 2), indexed by (farm_id, day D).

    how="sum": sum of hourly values; how="median": median of the daily medians (NaN-aware).
    """
    day = df.hour_start.dt.floor("D")
    daily = df.groupby(["farm_id", day])[value].agg("sum" if how == "sum" else "median")
    end = day.max() + pd.Timedelta(days=2)       # same end for every farm, so recent days exist
    out = []
    for farm, s in daily.groupby(level=0):
        s = s.droplevel(0)
        idx = pd.date_range(s.index.min(), end, freq="D")
        s = s.reindex(idx)
        if how == "sum":
            r = s.fillna(0.0).rolling(days, min_periods=days).sum()
        else:
            r = s.rolling(days, min_periods=1).median()
        r = r.shift(3)              # value for D = days D-32 .. D-3, i.e. data before D-2 00:00
        out.append(pd.DataFrame({"farm_id": farm, "day": idx, "trailing": r.to_numpy()}))
    return pd.concat(out).set_index(["farm_id", "day"])


def check_no_leakage(df: pd.DataFrame) -> None:
    """Every asof_* column must be <= issued_at on every row (nulls allowed)."""
    for c in [c for c in df.columns if c.startswith("asof_")]:
        bad = df[c].notna() & (df[c] > df.issued_at)
        assert not bad.any(), f"{c}: {int(bad.sum())} rows use data from after issued_at"


# ---------- loading ----------

def _concat_parquet(files, columns=None) -> pd.DataFrame:
    files = [f for f in files if pq.read_schema(f).names]
    return pd.concat([pd.read_parquet(f, columns=columns) for f in files], ignore_index=True)


def load_weather_forecast() -> pd.DataFrame:
    w = _concat_parquet(sorted((RAW / "openmeteo" / "previous_runs").glob("*.parquet")),
                        ["farm_id", "time", "wind_speed_100m_previous_day2"])
    return (w.drop_duplicates(["farm_id", "time"], keep="last")
             .rename(columns={"time": "hour_start", "wind_speed_100m_previous_day2": "wind_fc_ms"}))


def hourly_target(curt: pd.DataFrame) -> pd.DataFrame:
    """Σ curtailed_mwh_a per farm per hour (only hours where the farm was operating)."""
    g = curt.groupby(["farm_id", "zone", curt.period_start.dt.floor("h").rename("hour_start")])
    return g.curtailed_mwh_a.sum().rename("curtailed_mwh").reset_index()


# ---------- assemble ----------

def hourly_limits(residual: pd.DataFrame) -> pd.DataFrame:
    """Hourly mean B4 / B6 limit (MW) from rows with period_start, boundary, boundary_limit_mw."""
    return (residual.assign(hour_start=residual.period_start.dt.floor("h"))
            .pivot_table(index="hour_start", columns="boundary", values="boundary_limit_mw", aggfunc="mean"))


def hourly_demand(ndf: pd.DataFrame, issued_at: pd.Timestamp | None = None) -> pd.DataFrame:
    dem = demand_forecast(ndf, issued_at)
    return dem.groupby(dem.index.floor("h")).agg(gb_demand_fc_mw=("gb_demand_fc_mw", "mean"),
                                                 publish_time=("publish_time", "max"))


def build_panel(curt: pd.DataFrame, farms: pd.DataFrame, weather: pd.DataFrame,
                residual: pd.DataFrame, ndf: pd.DataFrame) -> pd.DataFrame:
    """All signals plus the target, one row per farm-hour with a day-ahead weather forecast."""
    y = hourly_target(curt)
    p = y.merge(weather, on=["farm_id", "hour_start"], how="inner")
    p = p[p.wind_fc_ms.notna()].copy()
    p["issued_at"] = issue_time(p.hour_start)
    p["asof_wind"] = p.hour_start - WIND_LEAD
    return add_signals(p, curt, y, farms, hourly_limits(residual), hourly_demand(ndf))


def add_signals(p: pd.DataFrame, curt: pd.DataFrame, y: pd.DataFrame, farms: pd.DataFrame,
                lim: pd.DataFrame, dem_h: pd.DataFrame) -> pd.DataFrame:
    """Add every signal to p (farm_id, zone, hour_start, wind_fc_ms, issued_at, asof_wind).

    Shared by the backtest panel and the live "tomorrow" rows, so both see identical features.
    `y` is the hourly Part 1 history used for the lag and trailing signals.
    """
    p = p.copy()
    cap = farms.groupby("farm_id").capacity_mw.sum()
    p["capacity_mw"] = p.farm_id.map(cap)
    p["pot_farm_mw"] = p.capacity_mw * generic_cf(p.wind_fc_ms.to_numpy())

    zone_pot = p.pivot_table(index="hour_start", columns="zone", values="pot_farm_mw", aggfunc="sum")
    p["pot_north_mw"] = p.hour_start.map(zone_pot.get("north_scotland")).fillna(0.0)
    p["pot_south_mw"] = p.hour_start.map(zone_pot.get("south_scotland")).fillna(0.0)

    p["b4_limit_mw"] = p.hour_start.map(lim["B4"])
    p["b6_limit_mw"] = p.hour_start.map(lim["B6"])
    if "asof_limits" not in p:
        p["asof_limits"] = p.issued_at                # assumption documented in the module docstring
    p["b4_excess_potential_mw"] = p.pot_north_mw - p.b4_limit_mw
    p["b6_excess_potential_mw"] = p.pot_north_mw + p.pot_south_mw - p.b6_limit_mw

    p["gb_demand_fc_mw"] = p.hour_start.map(dem_h.gb_demand_fc_mw)
    p["asof_demand"] = p.hour_start.map(dem_h.publish_time)

    lag = lag_hours(y, 48)
    p["lag48_mwh"] = pd.MultiIndex.from_frame(p[["farm_id", "hour_start"]]).map(lag.lag48)
    p["asof_lag48"] = p.hour_start - pd.Timedelta(hours=47)

    day = p.hour_start.dt.floor("D")
    key = pd.MultiIndex.from_arrays([p.farm_id, day])
    trail = trailing_daily(y, "curtailed_mwh", 30, "sum")
    p["trailing_waste_rate"] = key.map(trail.trailing) / (p.capacity_mw * 24 * 30)
    bids = curt[(curt.curtailed_mwh_a > 0) & curt.bid_price_gbp_mwh.notna()]
    bids = bids.assign(hour_start=bids.period_start)
    p["bid_median_gbp_mwh"] = key.map(trailing_daily(bids, "bid_price_gbp_mwh", 30, "median").trailing)
    p["asof_trailing"] = day - pd.Timedelta(days=2)

    p["hour"], p["weekday"], p["month"] = p.hour_start.dt.hour, p.hour_start.dt.weekday, p.hour_start.dt.month
    p["farm_code"] = p.farm_id.map({f: i for i, f in enumerate(sorted(cap.index))})  # stable codes
    return p.sort_values(["hour_start", "farm_id"]).reset_index(drop=True)


def load_and_build() -> pd.DataFrame:
    curt = pd.read_parquet(PROCESSED / "curtailment.parquet",
                           columns=["farm_id", "zone", "period_start", "curtailed_mwh_a", "bid_price_gbp_mwh"])
    farms = pd.read_csv(ROOT / "reference" / "farms.csv")
    residual = pd.read_parquet(PROCESSED / "residual.parquet",
                               columns=["period_start", "boundary", "boundary_limit_mw"])
    ndf = _concat_parquet(sorted((RAW / "elexon" / "NDF").glob("*.parquet")),
                          ["publishTime", "startTime", "demand"])
    return build_panel(curt, farms, load_weather_forecast(), residual, ndf)
