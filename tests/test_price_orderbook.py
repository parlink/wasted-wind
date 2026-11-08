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
