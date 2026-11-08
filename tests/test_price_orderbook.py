"""Tests for #51: order-book signals (pipeline.orderbook) and the price forecast (pipeline.price)."""
import numpy as np
import pandas as pd
import pytest

from pipeline import orderbook as ob
from pipeline import price as pr

D = pd.Timestamp("2025-03-01T00:00Z")


def bod_rows(day, unit, bid, mw, n_periods=2):
    t = pd.date_range(day, periods=n_periods, freq="30min")
    return pd.DataFrame({"bmUnit": unit, "timeFrom": t, "pairId": -1, "levelFrom": -mw, "bid": bid})


# ---------- order book ----------

def test_daily_orderbook_medians_and_cheap_depth():
    farms = pd.DataFrame({"bmu_id": ["N1", "N2", "S1"], "zone": ["north_scotland", "north_scotland", "south_scotland"]})
    bod = pd.concat([bod_rows(D, "N1", -5.0, 100), bod_rows(D, "N2", -40.0, 300), bod_rows(D, "S1", -30.0, 50),
                     bod_rows(D, "E1", -100.0, 400)])                     # E1: not Scottish, ignored
    out = ob.daily_orderbook(bod, farms).loc[D]
    assert out.ob_bid_median_north_gbp_mwh == pytest.approx(-22.5)
    assert out.ob_bid_median_south_gbp_mwh == pytest.approx(-30.0)
    assert out.ob_cheap_bid_mw_north == pytest.approx(100.0)            # only N1 bids above -10


def test_daily_orderbook_ignores_placeholders_and_offer_side():
    farms = pd.DataFrame({"bmu_id": ["N1"], "zone": ["north_scotland"]})
    bod = pd.concat([bod_rows(D, "N1", -20.0, 100),
                     bod_rows(D, "N1", -9999.0, 100).assign(pairId=-2),
                     bod_rows(D, "N1", 50.0, 100).assign(pairId=1)])
    out = ob.daily_orderbook(bod, farms).loc[D]
    assert out.ob_bid_median_north_gbp_mwh == pytest.approx(-20.0)


def test_orderbook_signals_come_from_two_days_before():
    # Bids for day D are only final an hour before each half hour, so at the D-1 10:00 issue
    # time the latest complete day of bids is D-2.
    daily = pd.DataFrame({"ob_bid_median_north_gbp_mwh": [-10.0, -20.0, -30.0]},
                         index=pd.DatetimeIndex([D, D + pd.Timedelta(days=1), D + pd.Timedelta(days=2)]))
    hours = pd.Series(pd.to_datetime(["2025-03-03T05:00Z", "2025-03-03T23:00Z"]))
    got = ob.for_hours(daily, hours)
    assert got.ob_bid_median_north_gbp_mwh.tolist() == [-10.0, -10.0]
    assert (got.asof_orderbook <= hours.dt.normalize() - pd.Timedelta(hours=14)).all()


# ---------- price forecast ----------

def price_series(days=20, seed=0):
    idx = pd.date_range("2025-01-01T00:00Z", periods=48 * days, freq="30min")
    rng = np.random.default_rng(seed)
    return pd.Series(60 + 20 * np.sin(np.arange(len(idx)) / 48 * 2 * np.pi) + rng.normal(0, 3, len(idx)), index=idx)


def test_price_lags_use_only_data_known_at_issue():
    p = price_series()
    feats = pr.lag_features(p)
    t = pd.Timestamp("2025-01-15T12:30Z")
    assert feats.loc[t, "price_lag2d_gbp_mwh"] == pytest.approx(p[t - pd.Timedelta(days=2)])
    week = [p[t - pd.Timedelta(days=k)] for k in range(2, 9)]
    assert feats.loc[t, "price_7d_mean_gbp_mwh"] == pytest.approx(np.mean(week))
    assert (feats.asof_price <= feats.index.normalize() - pd.Timedelta(hours=14)).all()


def test_seven_day_baseline_matches_the_dispatcher_expectation():
    # The baseline must be exactly what dispatch.scorecard plans with today (D-8 .. D-2).
    from dispatch import scorecard as sc
    p = price_series()
    feats = pr.lag_features(p)
    d = 15
    day = p.index[0].normalize() + pd.Timedelta(days=d)
    assert np.allclose(feats.loc[day:day + pd.Timedelta(hours=23, minutes=30), "price_7d_mean_gbp_mwh"].to_numpy(),
                       sc.expected_price(p.to_numpy(), d))


def test_price_forecast_by_day_returns_48_values_or_none():
    idx = pd.date_range("2025-01-10T00:00Z", periods=48, freq="30min")
    fc = pd.DataFrame({"period_start": idx, "price_pred_gbp_mwh": np.arange(48.0)})
    lookup = pr.by_day(fc)
    assert np.allclose(lookup(pd.Timestamp("2025-01-10T00:00Z")), np.arange(48.0))
    assert lookup(pd.Timestamp("2025-01-11T00:00Z")) is None


def test_price_forecast_by_day_fills_a_few_missing_half_hours():
    """#57: a day with 3 half hours missing (no market price, so no backtest row) still uses the forecast."""
    idx = pd.date_range("2026-09-11T00:00Z", periods=48, freq="30min")
    vals = np.arange(48.0)
    keep = np.ones(48, bool); keep[[10, 11, 30]] = False
    lookup = pr.by_day(pd.DataFrame({"period_start": idx[keep], "price_pred_gbp_mwh": vals[keep]}))
    got = lookup(idx[0])
    assert got is not None and np.allclose(got, vals)           # linear values interpolate exactly
    assert lookup.filled == {idx[0]: 3}
    many = np.ones(48, bool); many[:5] = False                   # 5 missing: more than allowed
    lookup = pr.by_day(pd.DataFrame({"period_start": idx[many], "price_pred_gbp_mwh": vals[many]}))
    assert lookup(idx[0]) is None


# ---------- #51 review: honest uncertainty and a leak check that can fail ----------

def test_day_block_bootstrap_interval():
    from pipeline import bootstrap as bs
    days = pd.date_range("2026-08-01", periods=60, freq="D")
    const = pd.Series(2.0, index=days)
    lo, hi = bs.ci95(lambda s: s.mean(), const, days)
    assert lo == pytest.approx(2.0) and hi == pytest.approx(2.0)
    noise = pd.Series(np.random.default_rng(0).normal(0, 1, 60), index=days)
    lo, hi = bs.ci95(lambda s: s.mean(), noise, days)
    assert lo < 0 < hi                                   # pure noise: the interval spans zero


def test_capture_ratio_difference_interval_resamples_whole_days():
    from pipeline import bootstrap as bs
    days = pd.date_range("2026-08-01", periods=30, freq="D")
    a, b = pd.Series(10.0, index=days), pd.Series(12.0, index=days)
    ceiling = pd.Series(100.0, index=days)
    lo, hi = bs.capture_diff_ci95(b, a, ceiling)
    assert lo == pytest.approx(0.02) and hi == pytest.approx(0.02)


def test_price_asof_follows_the_lags_actually_used(monkeypatch):
    # If someone shortened the lags to include D-1, the leak check must fail, not pass silently.
    p = price_series()
    from pipeline import features as ft
    ok = pr.lag_features(p).assign(issued_at=lambda d: ft.issue_time(d.index))
    ft.check_no_leakage(ok)
    monkeypatch.setattr(pr, "LAG_DAYS", range(1, 8))
    bad = pr.lag_features(p).assign(issued_at=lambda d: ft.issue_time(d.index))
    with pytest.raises(AssertionError, match="asof_price"):
        ft.check_no_leakage(bad)


def test_holdout_gap_interval_uses_only_holdout_days():
    from dispatch import scorecard as sc
    t = pd.date_range("2026-07-25T00:00Z", "2026-08-10T00:00Z", freq="30min", inclusive="left")
    rows = []
    for key, rev in [("forecast/lp", 2.0), ("reactive/lp", 1.0), ("forecast/rule", 1.0), ("reactive/rule", 1.0),
                     ("perfect/lp", 10.0)]:
        r = np.full(len(t), rev)
        r[t.strftime("%Y-%m") == "2026-07"] = 99.0           # July isn't hold-out: must not count
        rows.append(pd.DataFrame({"period_start": t, "key": key, "revenue_gbp": r}))
    ci = sc._holdout_gap_ci(pd.concat(rows))
    assert ci["lp"] == [pytest.approx(0.1), pytest.approx(0.1)] and ci["rule"] == [0.0, 0.0]
