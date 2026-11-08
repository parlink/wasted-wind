"""#49 small version: forecasts issued 1-6 days ahead. Every signal must be known at D-k 10:00."""
import numpy as np
import pandas as pd
import pytest

from pipeline import features as ft
from pipeline import fetch
from pipeline import train as tr

D = pd.Timestamp("2025-03-10T00:00Z")


def test_issue_time_moves_back_one_day_per_lead():
    h = pd.DatetimeIndex([D, D + pd.Timedelta(hours=23)])
    for k in range(1, 7):
        assert (ft.issue_time(h, lead_days=k) == D - pd.Timedelta(days=k) + pd.Timedelta(hours=10)).all()
    assert (ft.issue_time(h) == ft.issue_time(h, lead_days=1)).all()


def test_weather_run_for_each_lead_is_issued_before_the_forecast():
    # previous_dayN for hour h of D comes from the run N days before h. For lead k that must be
    # at or before D-k 10:00 for every hour of D, so lead k uses previous_day(k+1).
    for k in ft.LEADS:
        n = ft.weather_run_days(k)
        latest_run = D + pd.Timedelta(hours=23) - pd.Timedelta(days=n)
        assert latest_run <= ft.issue_time(pd.DatetimeIndex([D]), lead_days=k)[0]
        # and one day fresher would leak
        assert D + pd.Timedelta(hours=23) - pd.Timedelta(days=n - 1) > ft.issue_time(pd.DatetimeIndex([D]), k)[0]
    assert ft.LEADS == range(1, 7)          # Open-Meteo keeps runs up to 7 days old, so lead 6 is the last


def test_fetch_downloads_every_weather_run_the_leads_need():
    url, hourly = fetch.OPENMETEO["previous_runs_week"]
    names = hourly.split(",")
    for k in ft.LEADS:
        if k > 1:
            assert f"wind_speed_100m_previous_day{ft.weather_run_days(k)}" in names
    assert not any(n.endswith("previous_day1") for n in names)      # day1 leaks even for lead 1


def test_latest_demand_forecast_before_the_lead_issue_time():
    ndf = pd.DataFrame({
        "publishTime": pd.to_datetime(["2025-03-06T09:00Z", "2025-03-07T09:00Z"]),
        "startTime": [D + pd.Timedelta(hours=12)] * 2, "demand": [1.0, 2.0]})
    at = lambda k: ft.demand_forecast(ndf, lead_days=k)  # noqa: E731
    assert at(3).loc[D + pd.Timedelta(hours=12)].gb_demand_fc_mw == 2.0      # issued D-3 = 03-07 10:00
    assert at(4).loc[D + pd.Timedelta(hours=12)].gb_demand_fc_mw == 1.0      # 03-07 09:00 is too late
    assert at(5).empty                                                       # nothing by 03-05 10:00


def test_trailing_window_ends_before_the_lead_issue_day():
    hours = pd.date_range("2025-02-01T00:00Z", periods=24 * 60, freq="h")
    y = pd.DataFrame({"farm_id": "x", "hour_start": hours, "v": np.arange(len(hours), dtype=float)})
    for gap in (2, 4, 7):
        t = ft.trailing_daily(y, "v", 30, "sum", gap_days=gap)
        window = y[(y.hour_start >= D - pd.Timedelta(days=30 + gap)) & (y.hour_start < D - pd.Timedelta(days=gap))]
        assert t.loc[("x", D), "trailing"] == pytest.approx(window.v.sum())
    assert ft.trailing_daily(y, "v", 30, "sum").equals(ft.trailing_daily(y, "v", 30, "sum", gap_days=2))


def _panel(lead):
    hours = pd.date_range("2025-02-01T00:00Z", periods=24 * 40, freq="h")
    farms = pd.DataFrame({"farm_id": ["a", "b"], "bmu_id": ["A-1", "B-1"], "zone": ["north_scotland", "south_scotland"],
                          "capacity_mw": [100.0, 50.0]})
    curt = pd.concat([pd.DataFrame({"farm_id": f, "zone": z, "period_start": pd.date_range(hours[0], hours[-1], freq="30min"),
                                    "curtailed_mwh_a": 1.0, "bid_price_gbp_mwh": -20.0})
                      for f, z in [("a", "north_scotland"), ("b", "south_scotland")]], ignore_index=True)
    weather = pd.concat([pd.DataFrame({"farm_id": f, "hour_start": hours, "wind_fc_ms": 10.0}) for f in "ab"])
    lim = pd.DataFrame({"B4": 2000.0, "B6": 5000.0}, index=pd.DatetimeIndex(hours, name="hour_start"))
    ndf = pd.DataFrame({"publishTime": hours - pd.Timedelta(days=8), "startTime": hours, "demand": 30000.0})
    return ft.build_panel(curt, farms, weather, lim, ndf, lead_days=lead)


@pytest.mark.parametrize("lead", [1, 2, 6])
def test_panel_for_each_lead_uses_only_what_is_known_at_its_issue_time(lead):
    p = _panel(lead)
    ft.check_no_leakage(p)
    assert (p.issued_at == ft.issue_time(p.hour_start, lead)).all()
    assert (p.asof_wind == p.hour_start - pd.Timedelta(days=lead + 1)).all()
    # persistence = same hour (k+1) days earlier, the latest whole day known at D-k 10:00
    assert (p.asof_lag48 <= p.issued_at).all()
    if lead > 1:          # NESO limits are only published the day before
        assert p.b4_limit_mw.isna().all() and p.b6_excess_potential_mw.isna().all()
    else:
        assert p.b4_limit_mw.notna().any()


def test_lead_features_drop_the_neso_limits_after_day_one():
    assert tr.lead_features(1) == ft.FEATURES
    for k in range(2, 7):
        assert not set(tr.LIMIT_FEATURES) & set(tr.lead_features(k))


def test_training_stops_before_the_first_issue_of_the_test_month():
    m = pd.Timestamp("2025-06-01T00:00Z")
    hours = pd.Series(pd.date_range("2025-05-20T00:00Z", "2025-06-02T00:00Z", freq="h"))
    for k in (1, 3, 6):
        last = hours[tr.train_mask(hours, m, lead_days=k)].max()
        assert last + pd.Timedelta(hours=1) <= ft.issue_time(pd.DatetimeIndex([m]), k)[0]
    assert tr.train_mask(hours, m).equals(tr.train_mask(hours, m, lead_days=1))


def test_every_lead_is_scored_on_the_same_farm_hours():
    from pipeline import lead_skill as ls
    h = pd.date_range(D, periods=4, freq="h")
    a = pd.DataFrame({"farm_id": "x", "hour_start": h, "v": 1})
    b = pd.DataFrame({"farm_id": "x", "hour_start": h[1:], "v": 2})        # lead 2 lacks the first hour
    out = ls.common_rows({1: a, 2: b})
    assert all(len(d) == 3 and (d.hour_start == h[1:]).all() for d in out.values())


def test_leak_check_accepts_a_signal_that_is_missing_on_every_row():
    # Elexon's demand forecast reaches only ~2 days ahead, so at lead 6 asof_demand is all
    # missing, which pandas stores as float NaN, not NaT. Missing is allowed; it must not crash.
    df = pd.DataFrame({"issued_at": [D, D], "asof_demand": [np.nan, np.nan]})
    ft.check_no_leakage(df)
    with pytest.raises(AssertionError, match="asof_demand"):
        ft.check_no_leakage(df.assign(asof_demand=[pd.NaT, D + pd.Timedelta(hours=1)]))


def test_chart_rows_are_one_flat_row_per_lead():
    # parlink asked for [{lead_days, mae_mwh, baseline_mae_mwh, n}] to chart on the Forecast tab
    from pipeline import lead_skill as ls
    s = {"mae_mwh": 1.0, "precision": 0.5, "recall": 0.5, "monthly_total_error_pct": 10.0}
    table = [{"lead_days": 1, "neso_limits": True, "lightgbm": s, "climatology": {**s, "mae_mwh": 3.0},
              "wind_rule": {**s, "mae_mwh": 2.0}},
             {"lead_days": 1, "neso_limits": False, "lightgbm": s, "climatology": s, "wind_rule": s},
             {"lead_days": 2, "neso_limits": False, "lightgbm": {**s, "mae_mwh": 1.5}, "climatology": {**s, "mae_mwh": 3.0},
              "wind_rule": {**s, "mae_mwh": 2.5}}]
    rows = ls.chart_rows(table, n=100)
    assert [r["lead_days"] for r in rows] == [1, 2]            # the no-limits reference row is left out
    assert rows[1] == {"lead_days": 2, "mae_mwh": 1.5, "baseline_mae_mwh": 3.0, "wind_rule_mae_mwh": 2.5,
                       "recall": 0.5, "n": 100}
