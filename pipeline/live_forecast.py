"""Live "tomorrow" forecast, refreshed from the newest weather run without retraining.

    python -m pipeline.live_forecast              # refresh once
    python -m pipeline.live_forecast --every 60   # refresh every 60 minutes (judging day)

Uses the models and baselines saved by `pipeline.train` (data/processed/models/). Each refresh
fetches the newest Open-Meteo forecast, NESO day-ahead limits and Elexon demand forecast, then
replaces the `split = "live"` rows in forecast.parquet. Backtest rows are never touched.

Offline (or any fetch failure): the cached live rows are kept unchanged. Their `issued_at` still
shows when they were made, so the dashboard labels them stale (> 36 h) rather than presenting
an old forecast as new.
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path
from typing import Callable

import lightgbm as lgb
import pandas as pd
import requests

from pipeline import features as ft
from pipeline import train as tr

log = logging.getLogger("live_forecast")
FORECAST = ft.PROCESSED / "forecast.parquet"
CURT_COLS = ["farm_id", "zone", "period_start", "curtailed_mwh_a", "bid_price_gbp_mwh"]


class _Booster:
    """Saved LightGBM booster with the sklearn-style predict the training code uses."""

    def __init__(self, path: Path):
        self.booster = lgb.Booster(model_file=str(path))

    def predict(self, X: pd.DataFrame):
        return self.booster.predict(X[ft.FEATURES])


def load_models(model_dir: Path = tr.MODEL_DIR):
    cols = ["curtailed_mwh_pred", *tr.QUANTILES]
    missing = [c for c in cols if not (model_dir / f"{c}.txt").exists()]
    if missing:
        raise FileNotFoundError(f"run `python -m pipeline.train` first; missing {missing} in {model_dir}")
    return {c: _Booster(model_dir / f"{c}.txt") for c in cols}, pd.read_pickle(model_dir / "baselines.pkl")


def build_live(now: pd.Timestamp) -> pd.DataFrame:
    """Tomorrow's rows (CONTRACT §4 columns) from the saved models. Needs the network."""
    models, baselines = load_models()
    curt = pd.read_parquet(ft.PROCESSED / "curtailment.parquet", columns=CURT_COLS)
    rows = tr.live_rows(models, baselines, curt, now)
    version = (pd.read_parquet(FORECAST, columns=["model_version"]).model_version.iloc[0]
               if FORECAST.exists() else "unknown")
    rows["split"], rows["model_version"] = "live", version
    return rows[tr.FC_COLS]


def replace_live(fc: pd.DataFrame, live: pd.DataFrame) -> pd.DataFrame:
    """Keep every non-live row; swap the live rows for the new ones."""
    return pd.concat([fc[fc.split != "live"], live[tr.FC_COLS]], ignore_index=True)[tr.FC_COLS]


OFFLINE_ERRORS = (OSError, requests.RequestException, RuntimeError)   # network, API, gave-up retries


def refresh(build_live: Callable[[pd.Timestamp], pd.DataFrame] = build_live,
            forecast_path: Path = FORECAST, now: pd.Timestamp | None = None) -> bool:
    """One refresh. Returns True if the live rows were replaced, False if the cache was kept.

    Never raises: a failed fetch or a failed write (e.g. Windows refusing to replace a file
    another process has open) keeps the cached forecast and the loop tries again next time.
    """
    now = now or pd.Timestamp.now(tz="UTC").floor("min")
    cached_when = None
    if forecast_path.exists():
        cached = pd.read_parquet(forecast_path, columns=["split", "issued_at"])
        cached_when = cached.loc[cached.split == "live", "issued_at"].max()
    try:
        live = build_live(now)
    except OFFLINE_ERRORS as e:
        log.warning("live refresh: offline or API error (%s: %s); keeping cached forecast issued %s",
                    type(e).__name__, e, cached_when)
        return False
    except Exception:  # noqa: BLE001 - a real bug: say so loudly, but keep serving the cache
        log.exception("live refresh: unexpected error; keeping cached forecast issued %s", cached_when)
        return False
    tmp = forecast_path.with_suffix(".tmp")
    try:
        out = replace_live(pd.read_parquet(forecast_path), live)
        out.to_parquet(tmp, index=False)
        tmp.replace(forecast_path)           # atomic, so the server never reads half a file
    except OSError as e:
        log.warning("live refresh: could not write %s (%s: %s); keeping cached forecast, retry next time",
                    forecast_path.name, type(e).__name__, e)
        tmp.unlink(missing_ok=True)
        return False
    log.info("live forecast for %s issued %s: %.0f MWh in total (hourly p10/p90 summed: %.0f-%.0f, "
             "not a band for the daily total)", f"{live.hour_start.min():%Y-%m-%d}", now,
             live.curtailed_mwh_pred.sum(), live.curtailed_mwh_p10.sum(), live.curtailed_mwh_p90.sum())
    return True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=float, default=None, help="minutes between refreshes")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    while True:
        refresh()
        if not a.every:
            break
        time.sleep(a.every * 60)


if __name__ == "__main__":
    main()
