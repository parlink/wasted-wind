"""#52: the uncertainty-aware (band) planner."""
import numpy as np
import pandas as pd
import pytest

from dispatch import band, optimiser
from dispatch import scorecard as sc


def test_band_signal_between_point_and_p90():
    point = np.array([0.0, 10.0, 20.0, 5.0])
    p90 = np.array([4.0, 30.0, np.nan, 2.0])          # missing, and one p90 below the point
    assert np.allclose(band.band_signal(point, p90, 0.0), point)
    assert np.allclose(band.band_signal(point, p90, 1.0), [4.0, 30.0, 20.0, 5.0])
    assert np.allclose(band.band_signal(point, p90, 0.5), [2.0, 20.0, 20.0, 5.0])
    assert (band.band_signal(point, None, 0.75) == point).all()


def _synthetic(seed=3):
    rng = np.random.default_rng(seed)
    days = sc.PRE_DAYS + 20
    hist = pd.date_range("2026-05-01T00:00Z", periods=48 * days, freq="30min")
    price = rng.normal(70, 35, len(hist))
    waste = np.where(rng.random(len(hist)) < 0.25, rng.uniform(0, 120, len(hist)), 0.0)
    head = np.where(waste > 0, 0.0, rng.uniform(0, 400, len(hist)))
    fc = waste * 0.7                                   # under-forecasts, like the real model
    f = {"waste": waste, "fc": fc, "fc_p90": waste * 1.3, "avoided_price": np.full(len(hist), 30.0)}
    return hist, price, head, f


def test_weight_zero_is_exactly_the_lp_planner():
    hist, price, head, f = _synthetic()
    b = sc.BATTERIES[0]
    lp = sc.replay(b, "forecast", hist, price, head, f, "lp")
    bd = sc.replay(dict(b, band_weight=0.0), "forecast", hist, price, head, f, band.NAME)
    cols = ["action", "power_mw", "soc_mwh", "captured_curtailment_mwh", "revenue_gbp"]
    pd.testing.assert_frame_equal(lp[cols], bd[cols])
    assert bd["reason"].str.contains("planning for the high end").any()


@pytest.mark.parametrize("w", band.WEIGHT_GRID)
def test_band_planner_never_beats_the_hindsight_ceiling(w):
    hist, price, head, f = _synthetic()
    b = sc.BATTERIES[0]
    rev = sc.replay(dict(b, band_weight=w), "forecast", hist, price, head, f, band.NAME).revenue_gbp.sum()
    top = optimiser.hindsight_bound(b, f["waste"][sc.PRE_DAYS * 48:], price[sc.PRE_DAYS * 48:],
                                    head[sc.PRE_DAYS * 48:], 0.0)["revenue_gbp"].sum()
    assert rev <= top + 1e-6
