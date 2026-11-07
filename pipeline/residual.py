"""Part 1 bonus: Scotland's hidden net position per zone -> data/processed/residual.parquet.

residual_mw = non-wind generation minus local demand in the zone (CONTRACT §3), inferred:

  B4 binding (north farms curtailed):  flow_B4 = B4 limit
  otherwise:                           flow_B4 = NESO day-ahead flow, capped at +/- the limit
  residual_N = flow_B4 - wind delivered north

  B6 the same way, then (#21) the B4 inflow is taken out, so the south residual is "in the zone":
  residual_S = flow_B6 - wind delivered south - flow_B4

"Wind delivered" is Σ accepted_mwh of the farms in reference/farms.csv, converted to MW (x2
for a half hour). Wind from farms outside that list lands in the residual, which keeps the
simulator consistent: it only simulates those farms.

NESO's day-ahead "flow" is the unconstrained forecast (it is above the limit ~45% of the time on
B4), so it is used only when nothing was curtailed, and capped at the limit. Missing or zero
limits are filled (short gaps carried forward, then NESO's weekly limits, then interpolation)
and those rows are marked `fallback`. The weekly file's B4 value is "Min of B4 B5", a weekly
*minimum*, so fallback limits err low.

    python -m pipeline.residual
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
OUT = PROCESSED / "residual.parquet"

COLUMNS = ["zone", "period_start", "residual_mw", "residual_method", "boundary", "boundary_limit_mw",
           "boundary_flow_mw", "wind_delivered_mwh", "market_price_gbp_mwh", "gb_demand_mw", "temperature_2m_c"]
METHODS = {"binding", "flow_estimate", "fallback"}

B4_GROUPS = ["SSE_SP", "SSE_SP2"]   # SSE_SP until 2024-04-20, then renamed SSE_SP2
B6_GROUP = "SCOTEX"
BINDING_MWH = 1.0          # zone counts as constrained if its farms lost >= 1 MWh in the half hour
MIN_PLAUSIBLE_LIMIT_MW = 100.0   # NESO occasionally publishes 0; a boundary is never fully shut
CARRY_PERIODS = 4          # carry a missing value forward at most 2 hours before falling back

log = logging.getLogger("residual")


# ---------- NESO ----------

def parse_neso(raw: pd.DataFrame) -> pd.DataFrame:
    """NESO day-ahead CSV (UK local times) -> group, period_start (UTC), limit_mw, flow_mw.

    On the autumn clock change the 01:00 and 01:30 rows appear twice: the first is BST, the
    second GMT. The spring change simply has no 01:00-01:59 rows.
    """
    df = raw.rename(columns={"Constraint Group": "group", "Date_ Time GMT_BST": "local",
                             "Limit_MW": "limit_mw", "Flow_MW": "flow_mw"})
    local = pd.to_datetime(df["local"], format="ISO8601")
    # Rows per (group, local time): 2 legitimately on the autumn clock change (BST then GMT),
    # 1 otherwise. Extra rows are republications and are dropped. Rows are never compared by
    # value: the genuine BST and GMT rows are often identical (flat limits, repeated flows).
    ambiguous = pd.DatetimeIndex(local).tz_localize(
        "Europe/London", ambiguous="NaT", nonexistent="shift_forward").isna()
    n = df.assign(local=local).groupby(["group", "local"]).cumcount().to_numpy()
    keep = n < np.where(ambiguous, 2, 1)
    df, local = df[keep].reset_index(drop=True), local[keep].reset_index(drop=True)
    first = df.assign(local=local).groupby(["group", "local"]).cumcount().to_numpy() == 0
    utc = (pd.DatetimeIndex(local).tz_localize("Europe/London", ambiguous=first,
                                                nonexistent="shift_forward").tz_convert("UTC"))
    return pd.DataFrame({"group": df["group"].to_numpy(), "period_start": utc,
                         "limit_mw": df["limit_mw"].to_numpy(float),
                         "flow_mw": df["flow_mw"].to_numpy(float)})


def boundary_series(neso: pd.DataFrame) -> pd.DataFrame:
    """Wide frame indexed by period_start: b4_limit_mw, b4_flow_mw, b6_limit_mw, b6_flow_mw."""
    def one(groups, prefix):
        g = neso[neso.group.isin(groups)].copy()
        g["order"] = g.group.map({k: i for i, k in enumerate(groups)})
        g = g.sort_values(["period_start", "order"]).drop_duplicates("period_start", keep="last")
        return g.set_index("period_start")[["limit_mw", "flow_mw"]].add_prefix(prefix)
    return one(B4_GROUPS, "b4_").join(one([B6_GROUP], "b6_"), how="outer")


def parse_weekly(raw: pd.DataFrame) -> pd.DataFrame:
    """NESO weekly congestion file -> daily b4/b6 limits (latest publication per date)."""
    w = raw.sort_values("Publication Date").drop_duplicates("Data Date", keep="last")
    w = w.rename(columns={"Data Date": "date", "Min of B4 B5_Actual": "b4_limit_mw",
                          "B6_Actual": "b6_limit_mw"})
    w["date"] = pd.to_datetime(w["date"]).dt.tz_localize("UTC")
    return w.set_index("date")[["b4_limit_mw", "b6_limit_mw"]].dropna(how="all").sort_index()


def fill_boundaries(df: pd.DataFrame, weekly: pd.DataFrame | None) -> pd.DataFrame:
    """Fill missing or implausible limits and flows; add b4_filled / b6_filled flags.

    Keeps the raw NESO flow in b4_flow_raw_mw / b6_flow_raw_mw for the output's nullable column.
    """
    df = df.copy()
    for b in ("b4", "b6"):
        lim, flow = f"{b}_limit_mw", f"{b}_flow_mw"
        df[f"{b}_flow_raw_mw"] = df[flow]
        df.loc[df[lim] < MIN_PLAUSIBLE_LIMIT_MW, lim] = np.nan
        missing = df[lim].isna() | df[flow].isna()
        df[lim] = df[lim].ffill(limit=CARRY_PERIODS)
        if weekly is not None and lim in weekly and df[lim].isna().any():
            wk = weekly[lim].dropna()
            if len(wk):
                pos = wk.index.searchsorted(df.index, side="right") - 1
                vals = np.where(pos >= 0, wk.to_numpy()[np.clip(pos, 0, None)], np.nan)
                df[lim] = df[lim].fillna(pd.Series(vals, index=df.index))
        df[lim] = df[lim].interpolate(method="time").ffill().bfill()
        df[flow] = df[flow].ffill(limit=CARRY_PERIODS).interpolate(method="time").ffill().bfill()
        df[f"{b}_filled"] = missing.to_numpy()
    return df


def compute_residual(df: pd.DataFrame) -> pd.DataFrame:
    """Long frame (one row per zone per period) from a wide, gap-free frame indexed by period_start."""
    wind_n, wind_s = df.wind_n_mwh * 2, df.wind_s_mwh * 2          # MWh per half hour -> MW
    bind_n, bind_s = df.curt_n_mwh >= BINDING_MWH, df.curt_s_mwh >= BINDING_MWH
    l4, l6 = df.b4_limit_mw, df.b6_limit_mw
    flow4 = np.where(bind_n, l4, df.b4_flow_mw.clip(-l4, l4))
    flow6 = np.where(bind_s, l6, df.b6_flow_mw.clip(-l6, l6))
    f4 = df.get("b4_filled", pd.Series(False, index=df.index)).to_numpy(bool)
    f6 = df.get("b6_filled", pd.Series(False, index=df.index)).to_numpy(bool)
    raw4 = df.get("b4_flow_raw_mw", df.b4_flow_mw)
    raw6 = df.get("b6_flow_raw_mw", df.b6_flow_mw)

    def method(binding, filled):
        return np.where(filled, "fallback", np.where(binding, "binding", "flow_estimate"))

    north = pd.DataFrame({
        "zone": "north_scotland", "period_start": df.index, "residual_mw": flow4 - wind_n,
        "residual_method": method(bind_n, f4), "boundary": "B4", "boundary_limit_mw": l4.to_numpy(),
        "boundary_flow_mw": raw4.to_numpy(), "wind_delivered_mwh": df.wind_n_mwh.to_numpy()})
    south = pd.DataFrame({
        "zone": "south_scotland", "period_start": df.index, "residual_mw": flow6 - wind_s - flow4,
        "residual_method": method(bind_s, f4 | f6), "boundary": "B6",
        "boundary_limit_mw": l6.to_numpy(), "boundary_flow_mw": raw6.to_numpy(),
        "wind_delivered_mwh": df.wind_s_mwh.to_numpy()})
    return pd.concat([north, south], ignore_index=True)


# ---------- prices and demand ----------

def market_price(mid: pd.DataFrame) -> pd.Series:
    """GB market index price per period: APX where it traded, else any provider that traded."""
    m = mid[mid.volume > 0].assign(rank=lambda x: (x.dataProvider != "APXMIDP").astype(int))
    m = m.sort_values(["startTime", "rank"]).drop_duplicates("startTime", keep="first")
    return m.set_index("startTime").price.rename("market_price_gbp_mwh")


def demand_outturn(dem: pd.DataFrame) -> pd.Series:
    d = dem.sort_values("publishTime").drop_duplicates("startTime", keep="last")
    return d.set_index("startTime").initialDemandOutturn.astype(float).rename("gb_demand_mw")


# ---------- assemble ----------

def zone_inputs(curt: pd.DataFrame) -> pd.DataFrame:
    """Per-period wind delivered and Method A waste per Scottish zone, on a gap-free grid."""
    g = curt.groupby(["period_start", "zone"])[["accepted_mwh", "curtailed_mwh_a"]].sum().unstack("zone")
    idx = pd.date_range(curt.period_start.min(), curt.period_start.max(), freq="30min",
                        name="period_start")
    g = g.reindex(idx).fillna(0.0)
    return pd.DataFrame({
        "wind_n_mwh": g[("accepted_mwh", "north_scotland")], "wind_s_mwh": g[("accepted_mwh", "south_scotland")],
        "curt_n_mwh": g[("curtailed_mwh_a", "north_scotland")],
        "curt_s_mwh": g[("curtailed_mwh_a", "south_scotland")]}, index=idx)


def zone_temperature(weather: pd.DataFrame, towns: pd.DataFrame) -> pd.DataFrame:
    """Hourly 2 m air temperature (°C) per zone, population-weighted over the zone's main towns.

    `weather`: Open-Meteo archive rows (farm_id = town id, time, temperature_2m); `towns`:
    reference/towns.csv (town, zone, population). Heat-pump demand follows where people live, so
    towns are used rather than wind-farm sites (hilltops run colder, offshore sites milder).
    This is REALISED weather (reanalysis archive), right for historical replays; it is not a
    forecast. Index hour_start (UTC), one column per zone.
    """
    t = towns.set_index("town")
    w = weather[weather.farm_id.isin(t.index)].dropna(subset=["temperature_2m"])
    w = w.assign(zone=w.farm_id.map(t.zone), pop=w.farm_id.map(t.population).astype(float))
    w = w.assign(wt=w.temperature_2m * w["pop"])
    g = w.groupby(["time", "zone"])[["wt", "pop"]].sum()
    out = (g.wt / g["pop"]).unstack("zone")
    return out.rename_axis("hour_start").rename_axis(None, axis=1)


def half_hourly(hourly: pd.Series, periods: pd.Series) -> np.ndarray:
    """Hourly spot values onto half hours: :00 takes the hour, :30 the mean of the hours either
    side (null if either is missing)."""
    hour = periods.dt.floor("h")
    now = hour.map(hourly).to_numpy(float)
    nxt = (hour + pd.Timedelta(hours=1)).map(hourly).to_numpy(float)
    return np.where(periods.dt.minute.to_numpy() == 30, (now + nxt) / 2, now)


def assemble(zone: pd.DataFrame, bounds: pd.DataFrame, price: pd.Series, demand: pd.Series,
             weekly: pd.DataFrame | None, temperature: pd.DataFrame | None = None) -> pd.DataFrame:
    df = zone.join(bounds.reindex(zone.index))
    df = fill_boundaries(df, weekly)
    out = compute_residual(df)
    p = price.reindex(zone.index)
    n_price_gaps = int(p.isna().sum())
    p = p.ffill(limit=CARRY_PERIODS).interpolate(method="time").ffill().bfill()
    out["market_price_gbp_mwh"] = out.period_start.map(p)
    out["gb_demand_mw"] = out.period_start.map(demand)
    # hourly spot temperature interpolated to half hours; null where the archive has no value yet
    # (it lags real time by ~5 days)
    out["temperature_2m_c"] = np.nan
    if temperature is not None and len(temperature):
        for z in ("north_scotland", "south_scotland"):
            if z in temperature:
                m = out.zone == z
                out.loc[m, "temperature_2m_c"] = half_hourly(temperature[z], out.loc[m, "period_start"])
    if n_price_gaps:
        log.info("market price: filled %d missing half hours", n_price_gaps)
    return out.sort_values(["period_start", "zone"]).reset_index(drop=True)[COLUMNS]


def check_output(df: pd.DataFrame) -> None:
    """Raise AssertionError if df breaks CONTRACT.md section 3 or has gaps."""
    assert list(df.columns) == COLUMNS, list(df.columns)
    assert str(df.period_start.dt.tz) == "UTC"
    assert set(df.zone) == {"north_scotland", "south_scotland"}
    assert not df.duplicated(["zone", "period_start"]).any(), "duplicate primary key"
    for z, g in df.groupby("zone"):
        steps = g.period_start.sort_values().diff().dropna()
        assert (steps == pd.Timedelta(minutes=30)).all(), f"{z}: gap in half hours"
    assert set(df.residual_method) <= METHODS
    assert (df.boundary == df.zone.map({"north_scotland": "B4", "south_scotland": "B6"})).all()
    for c in ("residual_mw", "boundary_limit_mw", "wind_delivered_mwh", "market_price_gbp_mwh"):
        assert df[c].notna().all(), f"{c} has nulls"
    assert (df.boundary_limit_mw >= MIN_PLAUSIBLE_LIMIT_MW).all()
    per = df.groupby("period_start").market_price_gbp_mwh.nunique()
    assert (per == 1).all(), "market price must be identical in both zone rows"


# ---------- CLI ----------

def _load_elexon(ds: str) -> pd.DataFrame:
    files = [f for f in sorted((RAW / "elexon" / ds).glob("*.parquet")) if pq.read_schema(f).names]
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    curt = pd.read_parquet(PROCESSED / "curtailment.parquet",
                           columns=["zone", "period_start", "accepted_mwh", "curtailed_mwh_a"])
    zone = zone_inputs(curt)
    bounds = boundary_series(parse_neso(pd.read_csv(RAW / "neso" / "day_ahead_constraints.csv",
                                                    encoding="utf-8-sig")))
    weekly = parse_weekly(pd.read_csv(RAW / "neso" / "congestion_weekly.csv"))
    towns = pd.read_csv(ROOT / "reference" / "towns.csv")
    files = sorted((RAW / "openmeteo" / "archive_towns").glob("*.parquet"))
    if not files:
        raise SystemExit("no town weather: run `python -m pipeline.fetch --sources towns` first")
    weather = pd.concat([pd.read_parquet(f, columns=["farm_id", "time", "temperature_2m"]) for f in files])
    weather = weather.drop_duplicates(["farm_id", "time"], keep="last")
    out = assemble(zone, bounds, market_price(_load_elexon("MID")),
                   demand_outturn(_load_elexon("DEMAND_OUTTURN")), weekly,
                   zone_temperature(weather, towns))
    check_output(out)
    out.to_parquet(OUT, index=False)
    log.info("wrote %s: %d rows, %s to %s", OUT, len(out), out.period_start.min(), out.period_start.max())
    for z, g in out.groupby("zone"):
        share = g.residual_method.value_counts(normalize=True).round(3).to_dict()
        y = g[g.period_start.dt.year == 2025]
        log.info("%s: methods %s | 2025 binding share %.0f%% | residual median %.0f MW",
                 z, share, 100 * (y.residual_method == "binding").mean(), g.residual_mw.median())


if __name__ == "__main__":
    main()
