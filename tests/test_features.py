"""Tests for pipeline.features and pipeline.train. Hand-made data; real-output checks skip until built."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pipeline import features as ft
from pipeline import train as tr

ROOT = Path(__file__).resolve().parent.parent


def test_issue_time_is_previous_day_10_utc():
    h = pd.DatetimeIndex(["2025-03-02T00:00Z", "2025-03-02T23:00Z"])
    assert (ft.issue_time(h) == pd.Timestamp("2025-03-01T10:00Z")).all()


def test_generic_power_curve_shape():
    cf = ft.generic_cf(np.array([0, 3, 7.5, 12, 20, 26]))
    assert cf[0] == 0 and cf[1] == 0 and 0 < cf[2] < 1 and cf[3] == 1 and cf[4] == 1 and cf[5] == 0


def test_latest_demand_forecast_before_issue():
    ndf = pd.DataFrame({
        "publishTime": pd.to_datetime(["2025-03-01T09:30Z", "2025-03-01T11:00Z", "2025-02-28T20:00Z"]),
        "startTime": pd.to_datetime(["2025-03-02T12:00Z"] * 3),
        "demand": [30000.0, 99999.0, 28000.0]})
    out = ft.demand_forecast(ndf)
    row = out.loc[pd.Timestamp("2025-03-02T12:00Z")]
    assert row.gb_demand_fc_mw == 30000.0                  # 11:00 issue is after D-1 10:00
    assert row.publish_time == pd.Timestamp("2025-03-01T09:30Z")


def test_lag_and_trailing_use_only_past_data():
    hours = pd.date_range("2025-03-01T00:00Z", periods=24 * 40, freq="h")
    y = pd.DataFrame({"farm_id": "x", "hour_start": hours, "curtailed_mwh": np.arange(len(hours), dtype=float)})
    lag = ft.lag_hours(y, 48)
    t = pd.Timestamp("2025-03-05T07:00Z")
    assert lag.loc[("x", t), "lag48"] == y.set_index("hour_start").loc[t - pd.Timedelta(hours=48), "curtailed_mwh"]
    trail = ft.trailing_daily(y, value="curtailed_mwh", days=30, how="sum")
    d = pd.Timestamp("2025-04-05T00:00Z")
    window = y[(y.hour_start >= d - pd.Timedelta(days=32)) & (y.hour_start < d - pd.Timedelta(days=2))]
    assert trail.loc[("x", d), "trailing"] == pytest.approx(window.curtailed_mwh.sum())


def test_trailing_reaches_the_common_end_for_farms_with_older_data():
    # Farm "a" stopped having bids early; its trailing value must still exist for the latest day.
    hours = pd.date_range("2025-03-01T00:00Z", periods=24 * 20, freq="h")
    df = pd.concat([pd.DataFrame({"farm_id": "a", "hour_start": hours[:24 * 5], "v": 1.0}),
                    pd.DataFrame({"farm_id": "b", "hour_start": hours, "v": 2.0})])
    t = ft.trailing_daily(df, value="v", days=30, how="median")
    last = hours.max().floor("D") + pd.Timedelta(days=2)
    assert ("a", last) in t.index and t.loc[("a", last), "trailing"] == 1.0


def test_check_no_leakage_raises_on_future_signal():
    df = pd.DataFrame({"issued_at": [pd.Timestamp("2025-03-01T10:00Z")],
                       "asof_wind": [pd.Timestamp("2025-03-01T09:00Z")],
                       "asof_lag": [pd.Timestamp("2025-03-01T10:30Z")]})
    with pytest.raises(AssertionError, match="asof_lag"):
        ft.check_no_leakage(df)
    ft.check_no_leakage(df.drop(columns="asof_lag"))


# ---------- train / metrics ----------

def test_walk_forward_months_are_the_last_n_complete_months():
    hours = pd.date_range("2025-01-01T00:00Z", "2025-08-14T23:00Z", freq="h")
    months = tr.test_months(pd.Series(hours), n=3)
    assert [m.strftime("%Y-%m") for m in months] == ["2025-05", "2025-06", "2025-07"]


def test_metrics_event_precision_recall_and_monthly_error():
    df = pd.DataFrame({"hour_start": pd.to_datetime(["2025-05-01T00:00Z"] * 4),
                       "actual": [0.0, 20.0, 15.0, 0.0], "pred": [0.0, 30.0, 0.0, 12.0]})
    m = tr.score(df.actual, df.pred, df.hour_start, threshold=10)
    assert m["mae_mwh"] == pytest.approx((0 + 10 + 15 + 12) / 4)
    assert m["precision"] == pytest.approx(0.5) and m["recall"] == pytest.approx(0.5)
    assert m["monthly_total_error_pct"] == pytest.approx(abs(42 - 35) / 35 * 100)


def test_wind_rule_baseline_threshold_and_level():
    pot = np.array([100, 200, 900, 1000, 1100])
    y = np.array([0, 0, 50, 60, 70])
    rule = tr.fit_wind_rule(pot, y, threshold=10)
    assert 200 <= rule["threshold_mw"] <= 900
    pred = tr.apply_wind_rule(rule, np.array([150, 950]))
    assert pred[0] == 0 and pred[1] == pytest.approx(60)


# ---------- real output (skips until built) ----------

FC = ROOT / "data" / "processed" / "forecast.parquet"
METRICS = ROOT / "data" / "processed" / "model_metrics.json"
FC_COLS = ["farm_id", "zone", "hour_start", "issued_at", "split", "curtailed_mwh_pred",
           "curtailed_mwh_p10", "curtailed_mwh_p90", "curtailed_mwh_actual",
           "baseline_persistence_mwh", "baseline_climatology_mwh", "baseline_wind_rule_mwh",
           "model_version"]


@pytest.mark.skipif(not FC.exists(), reason="forecast.parquet not built")
def test_forecast_output_matches_contract():
    fc = pd.read_parquet(FC)
    assert list(fc.columns) == FC_COLS
    assert set(fc.split) <= {"backtest", "live"}
    assert (fc.curtailed_mwh_pred >= 0).all()
    bt, live = fc[fc.split == "backtest"], fc[fc.split == "live"]
    assert (bt.issued_at <= bt.hour_start - pd.Timedelta(hours=14)).all()   # D-1 10:00
    assert (live.issued_at < live.hour_start).all() and live.curtailed_mwh_actual.isna().all()
    assert not fc.duplicated(["farm_id", "hour_start", "issued_at"]).any()
    m = json.loads(METRICS.read_text())
    assert set(m["models"]) == {"lightgbm", "persistence", "climatology", "wind_rule"}
