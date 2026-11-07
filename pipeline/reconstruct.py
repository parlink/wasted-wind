"""Part 1: rebuild wasted wind per BMU per half hour -> data/processed/curtailment.parquet.

Method A (market data): put planned output (PN) and accepted levels (BOALF) on a 1-minute
grid. Where acceptances overlap, the latest-issued one wins for that minute (NESO re-issues
turn-down instructions every few minutes, each superseding the last). Waste for a minute is
max(0, PN - accepted) when the winning acceptance is constraint-flagged (`soFlag`), else 0.
Sum per half hour and divide by 60 to turn MW-minutes into MWh.

Method B (physics cross-check): an empirical power curve per BMU, fitted on half hours with no
acceptance at all (metered / capacity vs Open-Meteo 100 m wind at the farm). Potential =
capacity x curve(wind); waste = max(0, potential - metered). It never sees acceptance volumes.

    python -m pipeline.reconstruct          # build and print validation vs Modo
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed" / "curtailment.parquet"
ORIGIN = pd.Timestamp("2024-01-01T00:00Z")

COLUMNS = ["bmu_id", "farm_id", "zone", "period_start", "settlement_date", "settlement_period",
           "planned_mwh", "accepted_mwh", "curtailed_mwh_a", "potential_mwh_b", "curtailed_mwh_b",
           "metered_mwh", "bid_price_gbp_mwh", "wind_speed_100m_ms"]

# Modo Energy 2025 curtailment per farm (docs/BRIEF.md, Validation targets).
MODO_2025_TWH = {"seagreen": 2.7, "moray_east": 1.7, "moray_west": 1.4}

MIN_CURVE_SAMPLES = 500   # clean half hours needed to fit a BMU's power curve
CURVE_BIN_MS = 0.5
NON_DECREASING_UNTIL_MS = 20.0

log = logging.getLogger("reconstruct")


# ---------- 1-minute grid ----------

def rasterize(t0: np.ndarray, t1: np.ndarray, l0: np.ndarray, l1: np.ndarray):
    """Expand linear MW segments [t0, t1) (integer minutes) into per-minute values.

    The value for minute m is the level at the start of that minute.
    Returns (minute_index, value) arrays; segments with t1 <= t0 are dropped.
    """
    n = np.maximum(t1 - t0, 0).astype(np.int64)
    total = int(n.sum())
    if total == 0:
        return np.empty(0, np.int64), np.empty(0, float)
    seg = np.repeat(np.arange(len(n)), n)
    k = np.arange(total) - np.repeat(np.cumsum(n) - n, n)
    frac = k / n[seg]
    return t0[seg] + k, l0[seg] + (l1[seg] - l0[seg]) * frac


def _minutes(t: pd.Series, origin: pd.Timestamp) -> np.ndarray:
    return ((t - origin) // pd.Timedelta(minutes=1)).to_numpy(np.int64)


def minute_profiles(pn: pd.DataFrame, boa: pd.DataFrame, origin: pd.Timestamp, n: int):
    """One BMU's PN, winning accepted level and its soFlag on a grid of n minutes from origin."""
    pn_min = np.full(n, np.nan)
    if len(pn):
        pn = pn.drop_duplicates("timeFrom", keep="last")
        idx, val = rasterize(_minutes(pn.timeFrom, origin), _minutes(pn.timeTo, origin),
                             pn.levelFrom.to_numpy(float), pn.levelTo.to_numpy(float))
        ok = (idx >= 0) & (idx < n)
        pn_min[idx[ok]] = val[ok]

    boal = np.full(n, np.nan)
    so = np.zeros(n, bool)
    if len(boa):
        boa = boa.sort_values(["acceptanceTime", "acceptanceNumber"])
        rank = boa.groupby(["acceptanceTime", "acceptanceNumber"], sort=False).ngroup().to_numpy()
        seg_n = np.maximum(_minutes(boa.timeTo, origin) - _minutes(boa.timeFrom, origin), 0)
        idx, val = rasterize(_minutes(boa.timeFrom, origin), _minutes(boa.timeTo, origin),
                             boa.levelFrom.to_numpy(float), boa.levelTo.to_numpy(float))
        rk = np.repeat(rank, seg_n)
        flag = np.repeat(boa.soFlag.to_numpy(bool), seg_n)
        ok = (idx >= 0) & (idx < n)
        idx, val, rk, flag = idx[ok], val[ok], rk[ok], flag[ok]
        best = np.full(n, -1)
        np.maximum.at(best, idx, rk)          # latest acceptance covering each minute
        win = rk == best[idx]
        boal[idx[win]] = val[win]
        so[idx[win]] = flag[win]
    return pn_min, boal, so


def method_a(pn: pd.DataFrame, boa: pd.DataFrame, origin: pd.Timestamp, n: int) -> pd.DataFrame:
    """Half-hourly planned, accepted and Method A curtailed MWh for one BMU.

    Only half hours with at least one minute of PN get a row.
    """
    pn_min, boal, so = minute_profiles(pn, boa, origin, n)
    # Generation only: a negative PN (e.g. Viking at -1 MW) is the site's own power demand
    # while idle, not planned output, so it can't be curtailed. Keeps 0 <= waste <= planned.
    pn_min = np.maximum(pn_min, 0.0)
    boal = np.maximum(boal, 0.0)
    accepted = np.where(np.isnan(boal), pn_min, boal)
    cut = np.where(so & ~np.isnan(boal), np.maximum(pn_min - boal, 0.0), 0.0)
    cut[np.isnan(pn_min)] = np.nan

    pad = (-n) % 30
    def per_period(x):
        return np.pad(x, (0, pad), constant_values=np.nan).reshape(-1, 30)
    P, A, C = per_period(pn_min), per_period(accepted), per_period(cut)
    has = (~np.isnan(P)).any(axis=1)
    acc = np.where(np.isnan(A), P, A)
    df = pd.DataFrame({
        "period_start": origin + pd.to_timedelta(np.arange(P.shape[0]) * 30, unit="min"),
        "planned_mwh": np.nansum(P, axis=1) / 60,
        "accepted_mwh": np.nansum(acc, axis=1) / 60,
        "curtailed_mwh_a": np.nansum(C, axis=1) / 60,
        # internal: any acceptance (either flag) in force during the half hour
        "any_acceptance": (~np.isnan(per_period(boal))).any(axis=1),
    })
    return df[has].reset_index(drop=True)


# ---------- settlement fields ----------

def settlement_fields(period_start: pd.DatetimeIndex):
    """UK settlement date and period (1-46/48/50) for UTC period starts."""
    local = period_start.tz_convert("Europe/London")
    day = local.normalize()                        # local midnight, tz-aware
    sp = ((period_start - day.tz_convert("UTC")) // pd.Timedelta(minutes=30)).astype(int) + 1
    return np.array(local.date), np.asarray(sp)


# ---------- bid price ----------

def bid_price(bands: pd.DataFrame, cut_mw: float) -> float:
    """Volume-weighted bid price for turning down cut_mw, filling bid bands -1, -2, ... in order.

    BOD levels are cumulative MW relative to the notified level, so band -k covers turn-down
    between |level(-k+1)| and |level(-k)|. Volume beyond the last band is priced at the last band.
    """
    neg = bands[bands.pairId < 0].sort_values("pairId", ascending=False)
    if neg.empty or not cut_mw > 0:
        return np.nan
    upper = np.maximum.accumulate(np.abs(neg.levelFrom.to_numpy(float)))  # guard odd submissions
    lower = np.concatenate([[0.0], upper[:-1]])
    vol = np.clip(cut_mw - lower, 0, upper - lower)
    vol[-1] += max(cut_mw - upper[-1], 0)
    return float((vol * neg.bid.to_numpy(float)).sum() / vol.sum())


def period_bid_prices(bod: pd.DataFrame, cuts: pd.DataFrame) -> pd.Series:
    """bid_price_gbp_mwh per period_start for periods with Method A waste (one BMU)."""
    cuts = cuts[cuts.curtailed_mwh_a > 0]
    if cuts.empty or bod.empty:
        return pd.Series(dtype=float)
    bod = bod[bod.pairId < 0].assign(period_start=bod.timeFrom.dt.floor("30min"))
    bod = bod.drop_duplicates(["period_start", "pairId"], keep="last")
    bod = bod[bod.period_start.isin(cuts.period_start)]
    cut_mw = cuts.set_index("period_start").curtailed_mwh_a * 2   # mean MW over the half hour
    single = bod.groupby("period_start").pairId.transform("size") == 1
    out = bod[single].set_index("period_start").bid
    multi = {p: bid_price(g, cut_mw[p]) for p, g in bod[~single].groupby("period_start")}
    return pd.concat([out, pd.Series(multi, dtype=float)]).sort_index()


# ---------- Method B ----------

def fit_power_curve(wind: np.ndarray, cf: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Median capacity factor per 0.5 m/s wind bin, made non-decreasing up to its peak."""
    edges = np.arange(0, 35 + CURVE_BIN_MS, CURVE_BIN_MS)
    centers = edges[:-1] + CURVE_BIN_MS / 2
    b = np.digitize(wind, edges) - 1
    med = np.full(len(centers), np.nan)
    for i in range(len(centers)):
        v = cf[b == i]
        if len(v) >= 20:
            med[i] = np.median(v)
    ok = ~np.isnan(med)
    if ok.sum() < 3:
        raise ValueError("too few samples to fit a power curve")
    med = np.interp(centers, centers[ok], med[ok])
    med[centers < centers[ok][0]] = 0.0               # below the lowest observed bin: no output
    # Output never falls with rising wind until storm cut-out, which starts around 20-25 m/s.
    flat = centers < NON_DECREASING_UNTIL_MS
    med[flat] = np.maximum.accumulate(med[flat])
    return centers, np.clip(med, 0, 1)


def apply_power_curve(curve: tuple[np.ndarray, np.ndarray], wind: np.ndarray) -> np.ndarray:
    centers, cf = curve
    return np.interp(wind, centers, cf, left=0.0, right=cf[-1])


def method_b(df: pd.DataFrame, capacity_mw: float, rows_with_any_acceptance: np.ndarray):
    """Add potential_mwh_b and curtailed_mwh_b to one BMU's half-hourly frame (in place)."""
    df["potential_mwh_b"] = np.nan
    df["curtailed_mwh_b"] = np.nan
    cap_mwh = capacity_mw * 0.5
    clean = (~rows_with_any_acceptance & df.metered_mwh.notna() & df.wind_speed_100m_ms.notna()
             & (df.planned_mwh > 0))
    if clean.sum() < MIN_CURVE_SAMPLES:
        return None
    curve = fit_power_curve(df.wind_speed_100m_ms[clean].to_numpy(),
                            np.clip(df.metered_mwh[clean].to_numpy() / cap_mwh, 0, 1))
    has_wind = df.wind_speed_100m_ms.notna()
    pot = cap_mwh * apply_power_curve(curve, df.wind_speed_100m_ms[has_wind].to_numpy())
    df.loc[has_wind, "potential_mwh_b"] = pot
    df["curtailed_mwh_b"] = np.maximum(df.potential_mwh_b - df.metered_mwh, 0)
    return curve


# ---------- loading raw data ----------

def load_raw(ds: str, bmus: set[str], columns: list[str]) -> pd.DataFrame:
    files = sorted((RAW / "elexon" / ds).rglob("*.parquet"))
    parts = []
    for f in files:
        if "bmUnit" not in pq.read_schema(f).names:   # empty response: saved with no columns
            continue
        d = pd.read_parquet(f, columns=columns)
        parts.append(d[d.bmUnit.isin(bmus)])
    return pd.concat(parts, ignore_index=True)


def load_wind(farm_ids) -> pd.DataFrame:
    files = [f for fid in farm_ids for f in (RAW / "openmeteo" / "archive").glob(f"{fid}_*.parquet")]
    w = pd.concat([pd.read_parquet(f, columns=["farm_id", "time", "wind_speed_100m"]) for f in files])
    return w.drop_duplicates(["farm_id", "time"], keep="last")


# ---------- build ----------

def build(end: pd.Timestamp | None = None) -> pd.DataFrame:
    farms = pd.read_csv(ROOT / "reference" / "farms.csv")
    bmus = set(farms.bmu_id)
    end = end or pd.Timestamp(datetime.now(timezone.utc).date(), tz="UTC")
    n = int((end - ORIGIN) // pd.Timedelta(minutes=1))

    log.info("loading raw Elexon data for %d BMUs", len(bmus))
    pn = load_raw("PN", bmus, ["bmUnit", "timeFrom", "timeTo", "levelFrom", "levelTo"])
    boa = load_raw("BOALF", bmus, ["bmUnit", "acceptanceNumber", "acceptanceTime", "timeFrom",
                                   "timeTo", "levelFrom", "levelTo", "soFlag"])
    bod = load_raw("BOD", bmus, ["bmUnit", "timeFrom", "pairId", "levelFrom", "bid"])
    met = load_raw("B1610", bmus, ["bmUnit", "halfHourEndTime", "quantity"])
    met = met.assign(period_start=met.halfHourEndTime - pd.Timedelta(minutes=30))
    wind = load_wind(farms.farm_id.unique())

    pn_g, boa_g, bod_g, met_g = (d.groupby("bmUnit") for d in (pn, boa, bod, met))
    empty = {k: d.iloc[:0] for k, d in (("pn", pn), ("boa", boa), ("bod", bod), ("met", met))}
    frames = []
    for f in farms.itertuples():
        b = f.bmu_id
        get = lambda g, k: g.get_group(b) if b in g.groups else empty[k]  # noqa: E731
        p, a = get(pn_g, "pn"), get(boa_g, "boa")
        df = method_a(p, a, ORIGIN, n)
        if df.empty:
            log.warning("%s: no PN in range", b)
            continue
        m = get(met_g, "met").drop_duplicates("period_start", keep="last")
        df["metered_mwh"] = df.period_start.map(m.set_index("period_start").quantity)
        w = wind[wind.farm_id == f.farm_id].set_index("time").wind_speed_100m
        df["wind_speed_100m_ms"] = df.period_start.dt.floor("h").map(w)
        df["bid_price_gbp_mwh"] = df.period_start.map(period_bid_prices(get(bod_g, "bod"), df))

        # Half hours with any acceptance (either flag) are excluded from curve fitting.
        curve = method_b(df, f.capacity_mw, df.pop("any_acceptance").to_numpy())
        if curve is None:
            log.warning("%s: too few clean half hours for Method B", b)

        df["bmu_id"], df["farm_id"], df["zone"] = b, f.farm_id, f.zone
        frames.append(df)
        log.info("%s: %d periods, %.0f MWh curtailed (A)", b, len(df), df.curtailed_mwh_a.sum())

    out = pd.concat(frames, ignore_index=True)
    out["settlement_date"], out["settlement_period"] = settlement_fields(
        pd.DatetimeIndex(out.period_start))
    out["settlement_period"] = out.settlement_period.astype("int64")
    return out[COLUMNS]


# ---------- checks and validation (used by tests and the CLI) ----------

def check_output(df: pd.DataFrame, farms: pd.DataFrame) -> None:
    """Raise AssertionError if df breaks CONTRACT.md section 2."""
    assert list(df.columns) == COLUMNS, list(df.columns)
    assert str(df.period_start.dt.tz) == "UTC"
    assert (df.period_start.dt.minute % 30 == 0).all() and (df.period_start.dt.second == 0).all()
    assert not df.duplicated(["bmu_id", "period_start"]).any(), "duplicate primary key"
    assert set(df.zone) <= {"north_scotland", "south_scotland", "england_wales"}
    assert set(df.bmu_id) <= set(farms.bmu_id)
    assert (df.curtailed_mwh_a >= 0).all(), "negative waste"
    assert (df.curtailed_mwh_a <= df.planned_mwh + 1e-9).all(), "waste above planned"
    cap = df.bmu_id.map(farms.set_index("bmu_id").capacity_mw)
    assert (df.planned_mwh <= cap * 0.5 * 1.05 + 1e-9).all(), "planned above capacity x 0.5 h"
    b = df.curtailed_mwh_b.dropna()
    assert (b >= 0).all()
    assert df.settlement_period.between(1, 50).all()


def method_ab_daily_correlation(df: pd.DataFrame, top_n: int = 5) -> pd.Series:
    """Pearson correlation of daily Method A vs Method B totals for the top_n farms by A."""
    has_b = df[df.curtailed_mwh_b.notna()]
    top = has_b.groupby("farm_id").curtailed_mwh_a.sum().nlargest(top_n).index
    d = (has_b[has_b.farm_id.isin(top)]
         .groupby(["farm_id", has_b.period_start.dt.floor("D")])[["curtailed_mwh_a", "curtailed_mwh_b"]]
         .sum())
    return d.groupby(level=0).apply(lambda g: g.curtailed_mwh_a.corr(g.curtailed_mwh_b))


def annual_totals_twh(df: pd.DataFrame, year: int) -> pd.Series:
    y = df[df.period_start.dt.year == year]
    return y.groupby("farm_id").curtailed_mwh_a.sum() / 1e6


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    df = build()
    check_output(df, pd.read_csv(ROOT / "reference" / "farms.csv"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    log.info("wrote %s: %d rows", OUT, len(df))
    t = annual_totals_twh(df, 2025)
    for farm, modo in MODO_2025_TWH.items():
        log.info("2025 %-11s A=%.2f TWh  Modo=%.1f  (%+.0f%%)", farm, t[farm], modo,
                 100 * (t[farm] / modo - 1))
    log.info("all 33 farms 2025: %.2f TWh", t.sum())
    log.info("A vs B daily correlation, top 5: %s", method_ab_daily_correlation(df).round(2).to_dict())


if __name__ == "__main__":
    main()
