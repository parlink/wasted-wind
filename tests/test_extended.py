"""#50: walk-forward backtest extended back to mid-2024 for the time machine.

The headline numbers stay on the last 6 months; the extra months are reported separately."""
import numpy as np
import pandas as pd
import pytest

from pipeline import train as tr
from dispatch import scorecard as sc


def test_backtest_months_run_from_the_start_month_to_the_last_complete_month():
    hours = pd.Series(pd.date_range("2024-03-07T00:00Z", "2026-10-31T23:00Z", freq="h"))
    months = tr.backtest_months(hours, start="2024-07-01")
    assert months[0] == pd.Timestamp("2024-07-01T00:00Z") and months[-1] == pd.Timestamp("2026-10-01T00:00Z")
    assert len(months) == 28
    assert months[-tr.N_TEST_MONTHS:] == tr.test_months(hours)        # the headline months are the last 6


def test_training_days_counts_the_days_of_history_behind_each_fold():
    hours = pd.Series(pd.date_range("2024-03-07T00:00Z", "2024-07-30T23:00Z", freq="h"))
    m = pd.Timestamp("2024-07-01T00:00Z")
    # trained on 2024-03-07 .. 2024-06-29 (stops the day before the month): 115 days
    assert tr.training_days(hours[tr.train_mask(hours, m)]) == 115


def test_headline_metrics_only_score_the_headline_months():
    h = pd.to_datetime(["2026-04-15T00:00Z", "2026-05-15T00:00Z"])
    bt = pd.DataFrame({"hour_start": h, "curtailed_mwh_actual": [100.0, 1.0], "p": [0.0, 1.0]})
    head = tr.headline_rows(bt, [pd.Timestamp("2026-05-01T00:00Z")])
    assert len(head) == 1 and tr.score(head.curtailed_mwh_actual, head.p, head.hour_start)["mae_mwh"] == 0.0


def test_scorecard_headline_window_is_the_tuning_and_holdout_months():
    start, end = sc.headline_window()
    assert start == pd.Timestamp("2026-05-01T00:00Z") and end == pd.Timestamp("2026-11-01T00:00Z")
    assert all(start <= pd.Timestamp(f"{m}-01T00:00Z") < end for m in sc.TUNE_MONTHS + sc.HOLDOUT_MONTHS)


def test_replay_window_clips_the_forecast_to_the_requested_months():
    hours = pd.Series(pd.date_range("2024-07-01T00:00Z", "2026-10-31T23:00Z", freq="h"))
    a, b = pd.Timestamp("2024-07-01T00:00Z"), pd.Timestamp("2026-05-01T00:00Z")
    periods = sc.replay_periods(hours, a, b)
    assert periods[0] == a and periods[-1] == b - pd.Timedelta(minutes=30)
    periods = sc.replay_periods(hours, *sc.headline_window())
    assert periods[0] == pd.Timestamp("2026-05-01T00:00Z") and len(periods) == 184 * 48
