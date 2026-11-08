"""Part 2: day-ahead waste forecast per farm-hour, scored walk-forward against three baselines.

    python -m pipeline.train   -> data/processed/forecast.parquet, data/processed/model_metrics.json

Walk-forward by month from BACKTEST_START (#50: the time machine) to the last complete month; the
headline metrics ("models") score only the last 6 months, so they're unchanged, and the full range is
in "extended_backtest". For each test month, everything is fitted
(model, climatology, wind-rule threshold) on hours before the day BEFORE that month: the 1st's
forecast is issued at 10:00 on the last day of the previous month, which isn't complete yet.
Never shuffled. LightGBM settings and the Tweedie power were fixed up front (common defaults),
not tuned on the test months.

The model is biased low (~18% on the backtest): typical of Tweedie on a heavy-tailed target.

Uncertainty band (#14): two LightGBM quantile models (alpha 0.1 and 0.9), fitted walk-forward
like the point model, give curtailed_mwh_p10 / p90; their empirical coverage on the backtest is
reported in model_metrics.json -> interval. The fitted models are saved to data/processed/models/
so pipeline.live_forecast can refresh "tomorrow" hourly without retraining.

Ablation: the same model without the four NESO-limit signals, because NESO's day-ahead file has
no publication time (see features.py); reported under "ablations" in model_metrics.json.

Baselines (CONTRACT §4):
  persistence  same hour two days earlier (known at the issue time)
  climatology  the farm's mean for that hour of day and month, from training data
  wind_rule    if Scotland-wide forecast potential > T then the farm's mean waste above T, else 0;
               T is fitted on training data to maximise F1 on >10 MWh events
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import lightgbm as lgb
import numpy as np
import pandas as pd

from pipeline import features as ft

log = logging.getLogger("train")

N_TEST_MONTHS = 6
BACKTEST_START = "2024-07-01"      # archived day-ahead weather starts 2024-03-07: ~4 months to train on
EVENT_MWH = 10.0
TWEEDIE_POWER = 1.3
LGB_PARAMS = dict(objective="tweedie", tweedie_variance_power=TWEEDIE_POWER, n_estimators=500,
                  learning_rate=0.05, num_leaves=63, min_child_samples=50, subsample=0.8,
                  subsample_freq=1, colsample_bytree=0.8, verbose=-1, random_state=0)
FC_COLS = ["farm_id", "zone", "hour_start", "issued_at", "split", "curtailed_mwh_pred",
           "curtailed_mwh_p10", "curtailed_mwh_p90", "curtailed_mwh_actual",
           "baseline_persistence_mwh", "baseline_climatology_mwh", "baseline_wind_rule_mwh",
           "model_version", "training_days", "lead_days"]
LIMIT_FEATURES = ["b4_limit_mw", "b6_limit_mw", "b4_excess_potential_mw", "b6_excess_potential_mw"]
NO_LIMIT_FEATURES = [f for f in ft.FEATURES if f not in LIMIT_FEATURES]
WITH_OB_FEATURES = ft.FEATURES + ft.OB_FEATURES     # #51 ablation: the model plus order-book signals
QUANTILES = {"curtailed_mwh_p10": 0.1, "curtailed_mwh_p90": 0.9}
BAND_EDGES = (1.0, 10.0)               # groups on the point forecast, MWh per farm-hour
BAND_LABELS = ("<1", "1-10", ">10")
MODEL_DIR = ft.PROCESSED / "models"
MODEL_NAMES = {"lightgbm": "curtailed_mwh_pred", "persistence": "baseline_persistence_mwh",
               "climatology": "baseline_climatology_mwh", "wind_rule": "baseline_wind_rule_mwh"}


# ---------- tested pieces ----------

def test_months(hour_start: pd.Series, n: int = N_TEST_MONTHS) -> list[pd.Timestamp]:
    """Starts of the last n calendar months fully covered by the data."""
    last = hour_start.max() + pd.Timedelta(hours=1)          # exclusive end of the data
    end = last.normalize().replace(day=1) if last != last.normalize().replace(day=1) else last
    starts = pd.date_range(end=end - pd.Timedelta(days=1), periods=n, freq="MS", tz="UTC")
    return [s.normalize() for s in starts]


def backtest_months(hour_start: pd.Series, start: str = BACKTEST_START) -> list[pd.Timestamp]:
    """Every complete month from `start` to the end of the data; the last N_TEST_MONTHS are the headline."""
    last = test_months(hour_start)[-1]
    return list(pd.date_range(pd.Timestamp(start, tz="UTC"), last, freq="MS"))


def training_days(train_hours: pd.Series) -> int:
    """Days of history a fold was trained on (shown by the time machine: "trained on N days")."""
    return int(pd.DatetimeIndex(train_hours).normalize().nunique())


def headline_rows(bt: pd.DataFrame, months: list[pd.Timestamp]) -> pd.DataFrame:
    """Backtest rows in the headline months only."""
    return bt[bt.hour_start >= months[0]]


def score(actual, pred, hour_start, threshold: float = EVENT_MWH) -> dict:
    actual, pred = np.asarray(actual, float), np.asarray(pred, float)
    a_ev, p_ev = actual > threshold, pred > threshold
    tp = (a_ev & p_ev).sum()
    month = pd.DatetimeIndex(hour_start).strftime("%Y-%m")
    m = pd.DataFrame({"m": month, "a": actual, "p": pred}).groupby("m")[["a", "p"]].sum()
    m = m[m.a > 0]
    return {"mae_mwh": float(np.mean(np.abs(actual - pred))),
            "precision": float(tp / p_ev.sum()) if p_ev.sum() else float("nan"),
            "recall": float(tp / a_ev.sum()) if a_ev.sum() else float("nan"),
            "monthly_total_error_pct": float((np.abs(m.p - m.a) / m.a).mean() * 100)}


def fit_wind_rule(pot: np.ndarray, y: np.ndarray, threshold: float = EVENT_MWH) -> dict:
    """Threshold on forecast potential maximising F1 on y > threshold; level = mean y above it."""
    pot, y = np.asarray(pot, float), np.asarray(y, float)
    ev = y > threshold
    cands = np.unique(np.quantile(pot, np.linspace(0.0, 0.995, 200)))
    best_t, best_f1 = cands[-1], -1.0
    for t in cands:
        p = pot > t
        tp = (p & ev).sum()
        f1 = 2 * tp / (p.sum() + ev.sum()) if (p.sum() + ev.sum()) else 0.0
        if f1 > best_f1:
            best_t, best_f1 = t, f1
    above = pot > best_t
    return {"threshold_mw": float(best_t), "level_mwh": float(y[above].mean()) if above.any() else 0.0}


def apply_wind_rule(rule: dict, pot: np.ndarray) -> np.ndarray:
    return np.where(np.asarray(pot) > rule["threshold_mw"], rule["level_mwh"], 0.0)


# ---------- one walk-forward fold ----------

def _fit(train: pd.DataFrame, features=ft.FEATURES) -> lgb.LGBMRegressor:
    model = lgb.LGBMRegressor(**LGB_PARAMS)
    model.fit(train[features], train.curtailed_mwh, categorical_feature=["farm_code"])
    return model


def _fit_quantile(train: pd.DataFrame, alpha: float, features=ft.FEATURES) -> lgb.LGBMRegressor:
    params = {k: v for k, v in LGB_PARAMS.items() if k not in ("objective", "tweedie_variance_power")}
    model = lgb.LGBMRegressor(objective="quantile", alpha=alpha, **params)
    model.fit(train[features], train.curtailed_mwh, categorical_feature=["farm_code"])
    return model


def fit_all(train: pd.DataFrame, features=ft.FEATURES) -> dict:
    """Point (Tweedie) and quantile models on the same training rows."""
    return {"curtailed_mwh_pred": _fit(train, features),
            **{col: _fit_quantile(train, a, features) for col, a in QUANTILES.items()}}


def order_band(p10: np.ndarray, p90: np.ndarray, pred: np.ndarray | None = None):
    """Separately fitted quantiles can cross, and the point model is fitted separately too.
    Return a non-negative, ordered (low, high) that also contains `pred` when given, so the
    Predict tab never shows a point outside its own band."""
    lo, hi = np.clip(p10, 0, None), np.clip(p90, 0, None)
    lo, hi = np.minimum(lo, hi), np.maximum(lo, hi)
    if pred is not None:
        lo, hi = np.minimum(lo, pred), np.maximum(hi, pred)
    return lo, hi


def predict_all(models: dict, rows: pd.DataFrame, features=ft.FEATURES) -> pd.DataFrame:
    rows = rows.copy()
    X = rows[features]
    rows["curtailed_mwh_pred"] = np.clip(models["curtailed_mwh_pred"].predict(X), 0, None)
    if not all(c in models for c in QUANTILES):        # leads 2+: no band (#49, see main)
        rows["curtailed_mwh_p10"] = rows["curtailed_mwh_p90"] = np.nan
        return rows
    rows["curtailed_mwh_p10"], rows["curtailed_mwh_p90"] = order_band(
        models["curtailed_mwh_p10"].predict(X), models["curtailed_mwh_p90"].predict(X),
        rows["curtailed_mwh_pred"].to_numpy())
    return rows


def train_mask(hour_start: pd.Series, month: pd.Timestamp, lead_days: int = 1) -> pd.Series:
    """Training rows for a test month: hours before day 1-k, so every target is known when the
    month's first forecast is issued (D-k 10:00; k = 1 for the day-ahead)."""
    return hour_start < month - pd.Timedelta(days=lead_days)


def lead_features(lead_days: int) -> list[str]:
    """The model's signals for a lead: no NESO limits beyond the day ahead (not yet published)."""
    return ft.FEATURES if lead_days == 1 else NO_LIMIT_FEATURES


def _scot_pot(d: pd.DataFrame) -> np.ndarray:
    return (d.pot_north_mw + d.pot_south_mw).to_numpy()


def fit_baselines(train: pd.DataFrame) -> dict:
    """Climatology tables and the wind rule, from training rows only."""
    rule = fit_wind_rule(_scot_pot(train), train.curtailed_mwh.to_numpy())
    above = train[_scot_pot(train) > rule["threshold_mw"]]
    return {"clim": train.groupby(["farm_id", "hour", "month"]).curtailed_mwh.mean(),
            "clim_fb": train.groupby(["farm_id", "hour"]).curtailed_mwh.mean(),
            "rule": rule, "rule_level": above.groupby("farm_id").curtailed_mwh.mean()}


def apply_baselines(rows: pd.DataFrame, b: dict) -> pd.DataFrame:
    rows = rows.copy()
    k = pd.MultiIndex.from_frame(rows[["farm_id", "hour", "month"]])
    k_fb = pd.MultiIndex.from_frame(rows[["farm_id", "hour"]])
    rows["baseline_climatology_mwh"] = (k.map(b["clim"]).to_series(index=rows.index)
                                        .fillna(k_fb.map(b["clim_fb"]).to_series(index=rows.index))
                                        .fillna(0.0))
    rows["baseline_wind_rule_mwh"] = np.where(_scot_pot(rows) > b["rule"]["threshold_mw"],
                                              rows.farm_id.map(b["rule_level"]).fillna(0.0), 0.0)
    rows["baseline_persistence_mwh"] = rows.lag48_mwh.fillna(0.0)
    return rows


def run_fold(panel: pd.DataFrame, month: pd.Timestamp, headline: bool = True):
    """headline=False (months before the headline six): the forecast and baselines only, no
    ablations or SHAP, which are reported for the headline months."""
    nxt = month + pd.offsets.MonthBegin(1)
    train = panel[train_mask(panel.hour_start, month)]
    test = panel[(panel.hour_start >= month) & (panel.hour_start < nxt)].copy()
    models = fit_all(train)
    model = models["curtailed_mwh_pred"]
    test = predict_all(models, test)
    test["training_days"] = training_days(train.hour_start)
    if not headline:
        test = apply_baselines(test, fit_baselines(train))
        log.info("%s: train %d rows (%d days), test %d rows", month.strftime("%Y-%m"), len(train),
                 test.training_days.iloc[0] if len(test) else 0, len(test))
        return test, model
    ablation = _fit(train, NO_LIMIT_FEATURES)
    test["pred_no_limits"] = np.clip(ablation.predict(test[NO_LIMIT_FEATURES]), 0, None)
    with_ob = _fit(train, WITH_OB_FEATURES)           # #51: what the order-book signals add
    test["pred_with_orderbook"] = np.clip(with_ob.predict(test[WITH_OB_FEATURES]), 0, None)
    test.attrs["shap"] = shap_values(model, test[ft.FEATURES])     # out-of-sample, this fold
    b = fit_baselines(train)
    test = apply_baselines(test, b)
    log.info("%s: train %d rows, test %d rows, wind rule T=%.0f MW", month.strftime("%Y-%m"),
             len(train), len(test), b["rule"]["threshold_mw"])
    return test, model


# ---------- live "tomorrow" forecast ----------

OPENMETEO_FORECAST = "https://api.open-meteo.com/v1/forecast"


def live_inputs(now: pd.Timestamp, day: pd.Timestamp | None = None, n_days: int = 1):
    """Weather, limits and demand for `day` (default tomorrow, UTC), as known right now.
    Needs the network."""
    from pipeline import fetch, residual as rs
    day = (now + pd.Timedelta(days=1)).normalize() if day is None else day.normalize()
    farms = pd.read_csv(ft.ROOT / "reference" / "farms.csv").drop_duplicates("farm_id")
    rows = []
    for f in farms.itertuples():
        resp = fetch.get_json(OPENMETEO_FORECAST, {
            "latitude": f.lat, "longitude": f.lon, "hourly": "wind_speed_100m", "wind_speed_unit": "ms",
            "timezone": "GMT", "start_date": f"{day:%Y-%m-%d}",
            "end_date": f"{day + pd.Timedelta(days=n_days - 1):%Y-%m-%d}"})
        w = fetch.openmeteo_to_frame(resp, f.farm_id).rename(
            columns={"time": "hour_start", "wind_speed_100m": "wind_fc_ms"})
        rows.append(w.assign(zone=f.zone))
    weather = pd.concat(rows, ignore_index=True)

    fetch.fetch_neso(refresh=True)
    neso = rs.boundary_series(rs.parse_neso(pd.read_csv(
        fetch.RAW_ROOT / "neso" / "day_ahead_constraints.csv", encoding="utf-8-sig")))
    lim = _live_limits(neso, day, n_days)

    ndf = fetch.records_to_frame(fetch.get_json(f"{fetch.BASE}/datasets/NDF/stream", {
        "publishDateTimeFrom": f"{now - pd.Timedelta(hours=6):%Y-%m-%dT%H:%MZ}",
        "publishDateTimeTo": f"{now:%Y-%m-%dT%H:%MZ}"}))
    return weather, lim, ft.hourly_demand(ndf[["publishTime", "startTime", "demand"]], issued_at=now)


def _live_limits(neso_wide: pd.DataFrame, day: pd.Timestamp, n_days: int = 1) -> pd.DataFrame:
    """Hourly limits up to the end of `day`: NESO's published values (forward-fill only); hours
    not yet published take the latest known value for the same hour of day."""
    lim = ft.neso_limits_hourly(neso_wide)
    lim = lim[lim.index < day + pd.Timedelta(days=n_days)]
    hours = pd.date_range(day, periods=24 * n_days, freq="h")
    have = lim.reindex(hours)
    if have.isna().any().any():
        known = lim.dropna()
        last = known.groupby(known.index.hour).last()
        have = have.fillna(last.reindex(hours.hour).set_axis(hours))
        log.warning("live: some B4/B6 limits for %s not yet published; using latest same-hour values",
                    f"{day:%Y-%m-%d}")
    return pd.concat([lim[lim.index < day], have])


def live_rows(models: dict, baselines: dict, curt: pd.DataFrame, now: pd.Timestamp,
              day: pd.Timestamp | None = None, n_days: int = 1) -> pd.DataFrame:
    """Forecast rows for `day` (default tomorrow) issued now. dispatch.live uses day = today."""
    day = (now + pd.Timedelta(days=1)).normalize() if day is None else day.normalize()
    weather, lim, dem_h = live_inputs(now, day, n_days)
    farms = pd.read_csv(ft.ROOT / "reference" / "farms.csv")
    if curt.period_start.max() < day - pd.Timedelta(hours=25):     # lag48 needs D-2 complete
        log.warning("live: Part 1 data ends %s; lag48 / persistence and trailing signals for %s "
                    "are incomplete", curt.period_start.max(), f"{day:%Y-%m-%d}")
    p = weather.assign(issued_at=now, asof_wind=now, asof_limits=now)
    zone_pot = ft.zone_potential(weather, farms, ft.commissioning(curt))
    # order-book signals aren't in the live model (see features.OB_FEATURES), so none are loaded here
    rows = ft.add_signals(p, curt, ft.hourly_target(curt), farms, lim, dem_h, zone_pot)
    ft.check_no_leakage(rows)
    rows = predict_all(models, rows)
    rows = apply_baselines(rows, baselines)
    rows["curtailed_mwh_actual"] = np.nan
    rows["training_days"] = training_days(curt.period_start)     # live models are fitted on everything
    return rows


def week_days(now: pd.Timestamp) -> list[tuple[int, pd.Timestamp]]:
    """(lead_days, day) for the live week: tomorrow is lead 1, then one lead per day after."""
    return [(k, now.normalize() + pd.Timedelta(days=k)) for k in ft.LEADS]


def live_week_rows(models: dict, baselines: dict, curt: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    """#49: forecast rows for the next 6 days issued now, day k from lead k's models (models[k],
    baselines[k]); leads without saved models are skipped. Lead 1 is exactly live_rows' tomorrow."""
    days = [(k, d) for k, d in week_days(now) if k in models]
    weather, lim, dem_h = live_inputs(now, days[0][1], n_days=len(ft.LEADS))
    farms = pd.read_csv(ft.ROOT / "reference" / "farms.csv")
    zone_pot = ft.zone_potential(weather, farms, ft.commissioning(curt))
    y = ft.hourly_target(curt)
    out = []
    for k, day in days:
        w = weather[(weather.hour_start >= day) & (weather.hour_start < day + pd.Timedelta(days=1))]
        p = w.assign(issued_at=now, asof_wind=now, **({"asof_limits": now} if k == 1 else {}))
        rows = ft.add_signals(p, curt, y, farms, lim, dem_h, zone_pot, lead_days=k)
        ft.check_no_leakage(rows)
        rows = apply_baselines(predict_all(models[k], rows, lead_features(k)), baselines[k])
        rows["curtailed_mwh_actual"], rows["lead_days"] = np.nan, k
        rows["training_days"] = training_days(curt.period_start)
        out.append(rows)
    return pd.concat(out, ignore_index=True)


def interval_stats(bt: pd.DataFrame) -> dict:
    """Empirical coverage of the p10-p90 band, overall and on hours that really had waste.

    Most farm-hours have no waste, and a 0-0 band "covers" them trivially, so the overall figure
    flatters the band; the waste-hours figure is the honest one.
    """
    a, lo, hi = bt.curtailed_mwh_actual, bt.curtailed_mwh_p10, bt.curtailed_mwh_p90
    inside, waste = (a >= lo) & (a <= hi), a > 0
    # Grouped by the forecast itself (known ex ante): this is the calibration the band can be held
    # to. Misses on waste hours are mostly hours the model called calm (a point-forecast problem;
    # a conformal correction by these groups was tried and gave ~0 offsets, so it isn't used).
    group = pd.Series(np.asarray(BAND_LABELS)[np.digitize(bt.curtailed_mwh_pred, BAND_EDGES)], index=bt.index)
    by_group = {g: {"rows": int((group == g).sum()), "coverage": round(float(inside[group == g].mean()), 3),
                    "coverage_on_waste_hours": round(float(inside[(group == g) & waste].mean()), 3)
                    if ((group == g) & waste).any() else None}
                for g in BAND_LABELS}
    return {"nominal": 0.8, "by_predicted_size_mwh": by_group,
            "coverage_p10_p90": round(float(inside.mean()), 3),
            "coverage_on_waste_hours": round(float(inside[waste].mean()), 3),
            "above_p90_on_waste_hours": round(float((a > hi)[waste].mean()), 3),
            "below_p10_on_waste_hours": round(float((a < lo)[waste].mean()), 3),
            "mean_width_mwh": round(float((hi - lo).mean()), 2)}


def lead_dir(lead_days: int):
    """Saved models for a lead: lead 1 where they always were, lead k in models/lead{k}/."""
    return MODEL_DIR if lead_days == 1 else MODEL_DIR / f"lead{lead_days}"


def save_models(models: dict, baselines: dict, lead_days: int = 1) -> None:
    """Persist the final models and baselines for pipeline.live_forecast."""
    d = lead_dir(lead_days)
    d.mkdir(parents=True, exist_ok=True)
    for col, m in models.items():
        m.booster_.save_model(str(d / f"{col}.txt"))
    pd.to_pickle(baselines, d / "baselines.pkl")


def shap_values(model: lgb.LGBMRegressor, X: pd.DataFrame) -> pd.Series:
    """Mean |SHAP| per signal on up to 3,000 of this fold's TEST rows (out of sample)."""
    import shap
    sample = X.sample(min(len(X), 3000), random_state=0)
    vals = shap.TreeExplainer(model).shap_values(sample)
    return pd.Series(np.abs(vals).mean(axis=0), index=X.columns)


def shap_top(per_fold: list[pd.Series], n: int = 10) -> list[dict]:
    imp = pd.concat(per_fold, axis=1).mean(axis=1).sort_values(ascending=False)
    return [{"signal": k, "mean_abs_shap": round(float(v), 4)} for k, v in imp.head(n).items()]


# ---------- CLI ----------

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    panel = ft.load_and_build()
    ft.check_no_leakage(panel)
    log.info("panel: %d farm-hours, %s to %s, %d farms", len(panel), panel.hour_start.min(),
             panel.hour_start.max(), panel.farm_id.nunique())
    version = f"lgbm-{datetime.now(timezone.utc):%Y-%m-%d}a"
    months = test_months(panel.hour_start)                 # headline: the last 6 months
    all_months = backtest_months(panel.hour_start)
    folds, shaps = [], []
    for m in all_months:
        out, _ = run_fold(panel, m, headline=m in months)
        if "shap" in out.attrs:
            shaps.append(out.attrs.pop("shap"))
        folds.append(out)
    full_bt = pd.concat(folds, ignore_index=True)
    full_bt["split"], full_bt["model_version"], full_bt["lead_days"] = "backtest", version, 1
    full_bt["curtailed_mwh_actual"] = full_bt.curtailed_mwh
    fc = full_bt[FC_COLS]
    bt = headline_rows(full_bt, months)                     # every headline number below

    # Live: the next 6 days (#49), each lead's models fitted on everything for that lead (leads 2+
    # rebuild the panel with their own cut-offs). Leads 2+ get the point model only: their quantile
    # band was never calibrated and contradicted the point forecast (day 4: 3 MWh expected, summed
    # p90 26 GWh), so no band is published for them. Network problems keep the backtest.
    finals, bases = {1: fit_all(panel)}, {1: fit_baselines(panel)}
    save_models(finals[1], bases[1])
    del panel
    for k in ft.LEADS[1:]:
        pk = ft.load_and_build(k)
        ft.check_no_leakage(pk)
        finals[k], bases[k] = {"curtailed_mwh_pred": _fit(pk, lead_features(k))}, fit_baselines(pk)
        save_models(finals[k], bases[k], k)
        log.info("lead %d: final models on %d farm-hours", k, len(pk))
        del pk
    try:
        now = pd.Timestamp.now(tz="UTC").floor("min")
        curt = pd.read_parquet(ft.PROCESSED / "curtailment.parquet",
                               columns=["farm_id", "zone", "period_start", "curtailed_mwh_a",
                                        "bid_price_gbp_mwh"])
        live = live_week_rows(finals, bases, curt, now)
        live["split"], live["model_version"] = "live", version
        fc = pd.concat([fc, live[FC_COLS]], ignore_index=True)
        log.info("live: %d farm-hours, %s to %s, issued %s; MWh by lead %s", len(live),
                 f"{live.hour_start.min():%Y-%m-%d}", f"{live.hour_start.max():%Y-%m-%d}", now,
                 live.groupby("lead_days").curtailed_mwh_pred.sum().round(0).to_dict())
    except Exception as e:  # noqa: BLE001 - the backtest is still valid without live rows
        log.warning("live forecast skipped: %s", e)
    fc.to_parquet(ft.PROCESSED / "forecast.parquet", index=False)

    metrics = {name: score(bt.curtailed_mwh_actual, bt[col], bt.hour_start)
               for name, col in MODEL_NAMES.items()}
    out = {"model_version": version,
           "validation": f"walk-forward by month, last {N_TEST_MONTHS} months "
                         f"({months[0]:%Y-%m} to {months[-1]:%Y-%m})",
           "event_threshold_mwh": EVENT_MWH,
           "models": {k: {kk: round(vv, 3) for kk, vv in v.items()} for k, v in metrics.items()},
           "ablations": {"lightgbm_with_orderbook": {
               k: round(v, 3) for k, v in score(bt.curtailed_mwh_actual, bt.pred_with_orderbook,
                                                bt.hour_start).items()},
               "lightgbm_without_neso_limits": {
               k: round(v, 3) for k, v in score(bt.curtailed_mwh_actual, bt.pred_no_limits,
                                                bt.hour_start).items()}},
           "interval": interval_stats(bt),
           "extended_backtest": {
               "validation": f"walk-forward by month, {all_months[0]:%Y-%m} to {all_months[-1]:%Y-%m}; the "
                             "headline above is the last 6 months of it",
               "models": {name: {k: round(v, 3) for k, v in score(full_bt.curtailed_mwh_actual, full_bt[col],
                                                                  full_bt.hour_start).items()}
                          for name, col in MODEL_NAMES.items()},
               "by_month_mae_mwh": {m: round(float(np.mean(np.abs(g.curtailed_mwh_actual - g.curtailed_mwh_pred))), 3)
                                    for m, g in full_bt.groupby(full_bt.hour_start.dt.strftime("%Y-%m"))},
               "training_days_by_month": {m: int(g.training_days.iloc[0])
                                          for m, g in full_bt.groupby(full_bt.hour_start.dt.strftime("%Y-%m"))}},
           "shap_top_signals": shap_top(shaps),
           "notes": ["SHAP: mean |SHAP| on each fold's held-out month, averaged over folds",
                     "lightgbm is biased low (~18% on the backtest); the p10-p90 band is fitted separately",
                     "interval: by predicted size the band covers >= 80% in every group (calibrated "
                     "on what is knowable at issue time); on hours with waste it covers less, because "
                     "most misses are hours the model called calm or minor (surprises)",
                     "the band is widened where needed to contain the point forecast",
                     "live rows: issued_at = refresh time, so their lead time is shorter than the "
                     "backtest's D-1 10:00 issue",
                     "NESO day-ahead limits are assumed known by D-1 10:00 (no publication time in "
                     "the file); see ablations for the model without them"]}
    (ft.PROCESSED / "model_metrics.json").write_text(json.dumps(out, indent=2))
    log.info("wrote forecast.parquet (%d rows) and model_metrics.json", len(fc))
    for k, v in {**out["models"], **out["ablations"]}.items():
        log.info("%-28s %s", k, v)
    per_month = bt.groupby(bt.hour_start.dt.strftime("%Y-%m")).apply(
        lambda g: pd.Series({k: np.mean(np.abs(g.curtailed_mwh_actual - g[c])) for k, c in MODEL_NAMES.items()}))
    log.info("MAE by month:\n%s", per_month.round(2).to_string())


if __name__ == "__main__":
    main()
