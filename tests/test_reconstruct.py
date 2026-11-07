"""Tests for pipeline.reconstruct. Unit tests use hand-made data; the checks on the real
output (invariants, Method A vs B, Modo totals) skip when curtailment.parquet isn't built."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pipeline import reconstruct as rc

ROOT = Path(__file__).resolve().parent.parent
T0 = pd.Timestamp("2025-03-01T00:00Z")


def ts(minutes):
    return T0 + pd.Timedelta(minutes=minutes)


def pn_rows(*segs, bmu="T_X-1"):
    """segs: (from_min, to_min, level_from, level_to)"""
    return pd.DataFrame([{"bmUnit": bmu, "timeFrom": ts(a), "timeTo": ts(b),
                          "levelFrom": l0, "levelTo": l1} for a, b, l0, l1 in segs])


def boa_rows(*accs, bmu="T_X-1"):
    """accs: (acceptance_number, from_min, to_min, level_from, level_to, so_flag)"""
    return pd.DataFrame([{"bmUnit": bmu, "acceptanceNumber": n, "acceptanceTime": ts(a - 30),
                          "timeFrom": ts(a), "timeTo": ts(b), "levelFrom": l0, "levelTo": l1,
                          "soFlag": so} for n, a, b, l0, l1, so in accs])


def periods(pn, boa, n_minutes=60):
    return rc.method_a(pn, boa, T0, n_minutes)


# ---------- rasterising MW profiles onto a 1-minute grid ----------

def test_rasterize_flat_and_ramp():
    idx, val = rc.rasterize(np.array([0, 10]), np.array([10, 14]),
                            np.array([100.0, 0.0]), np.array([100.0, 40.0]))
    assert idx.tolist() == list(range(14))
    assert val[:10].tolist() == [100.0] * 10
    assert val[10:].tolist() == [0.0, 10.0, 20.0, 30.0]  # linear ramp, value at minute start


def test_rasterize_skips_zero_length_segments():
    idx, _ = rc.rasterize(np.array([5]), np.array([5]), np.array([1.0]), np.array([1.0]))
    assert len(idx) == 0


# ---------- Method A ----------

def test_toy_case_100_to_40_for_15_minutes_is_15_mwh():
    # The brief's toy case: planned flat 100 MW, turned down to 40 MW for 15 minutes.
    out = periods(pn_rows((0, 60, 100, 100)), boa_rows((1, 5, 20, 40, 40, True)))
    p1 = out.iloc[0]
    assert p1.curtailed_mwh_a == pytest.approx(15.0)
    assert p1.planned_mwh == pytest.approx(50.0)
    assert p1.accepted_mwh == pytest.approx((15 * 40 + 15 * 100) / 60)
    assert out.iloc[1].curtailed_mwh_a == 0.0


def test_non_constraint_acceptance_lowers_accepted_but_is_not_curtailment():
    out = periods(pn_rows((0, 60, 100, 100)), boa_rows((1, 0, 30, 40, 40, False)))
    assert out.iloc[0].curtailed_mwh_a == 0.0
    assert out.iloc[0].accepted_mwh == pytest.approx(20.0)


def test_latest_acceptance_wins_where_they_overlap():
    boa = boa_rows((1, 0, 30, 0, 0, True), (2, 10, 20, 80, 80, True))
    out = periods(pn_rows((0, 60, 100, 100)), boa)
    assert out.iloc[0].curtailed_mwh_a == pytest.approx((20 * 100 + 10 * 20) / 60)


def test_offer_above_plan_is_never_negative_waste():
    out = periods(pn_rows((0, 60, 100, 100)), boa_rows((1, 0, 30, 150, 150, True)))
    assert out.iloc[0].curtailed_mwh_a == 0.0


def test_negative_pn_is_site_demand_not_planned_output():
    # Viking notifies -1 MW when idle; that is not generation and can't be curtailed.
    out = periods(pn_rows((0, 30, -1, -1)), boa_rows())
    assert out.iloc[0].planned_mwh == 0.0 and out.iloc[0].curtailed_mwh_a == 0.0


def test_bid_price_non_cumulative_levels_do_not_divide_by_zero():
    bands = pd.DataFrame({"pairId": [-1, -2], "levelFrom": [-300, -100], "bid": [-50.0, -20.0]})
    assert rc.bid_price(bands, cut_mw=50) == pytest.approx(-50.0)
    assert np.isfinite(rc.bid_price(bands, cut_mw=400))


def test_minutes_without_pn_are_missing_not_zero():
    out = periods(pn_rows((0, 30, 100, 100)), boa_rows())
    assert out.iloc[0].planned_mwh == pytest.approx(50.0)
    assert len(out) == 1  # the second half hour has no PN, so no row


# ---------- settlement date / period from UTC period_start ----------

@pytest.mark.parametrize("utc, date, sp", [
    ("2025-03-01T00:00Z", "2025-03-01", 1),    # GMT
    ("2025-06-30T23:00Z", "2025-07-01", 1),    # BST: local midnight is 23:00 UTC
    ("2025-07-01T22:30Z", "2025-07-01", 48),
    ("2025-03-30T22:30Z", "2025-03-30", 46),   # spring forward: 46 periods
    ("2025-10-26T23:30Z", "2025-10-26", 50),   # fall back: 50 periods
])
def test_settlement_fields(utc, date, sp):
    d, p = rc.settlement_fields(pd.DatetimeIndex([pd.Timestamp(utc)]))
    assert str(d[0]) == date and p[0] == sp


# ---------- bid price ----------

def test_bid_price_single_band():
    bands = pd.DataFrame({"pairId": [-1, 1], "levelFrom": [-300, 300], "bid": [-41.19, 999.0]})
    assert rc.bid_price(bands, cut_mw=120) == pytest.approx(-41.19)


def test_bid_price_volume_weighted_across_bands():
    # Band -1 covers the first 100 MW of turn-down, band -2 the next 200 MW (levels cumulative).
    bands = pd.DataFrame({"pairId": [-1, -2], "levelFrom": [-100, -300], "bid": [-50.0, -20.0]})
    assert rc.bid_price(bands, cut_mw=150) == pytest.approx((100 * -50 + 50 * -20) / 150)


def test_bid_price_null_without_bands():
    assert np.isnan(rc.bid_price(pd.DataFrame(columns=["pairId", "levelFrom", "bid"]), 50))


# ---------- Method B power curve ----------

def test_power_curve_fit_is_monotone_and_zero_below_cut_in():
    rng = np.random.default_rng(0)
    wind = rng.uniform(0, 25, 5000)
    true = np.clip((wind - 3) / 9, 0, 1) ** 2
    cf = np.clip(true + rng.normal(0, 0.03, wind.size), 0, 1)
    curve = rc.fit_power_curve(wind, cf)
    grid = np.linspace(0, 20, 41)
    y = rc.apply_power_curve(curve, grid)
    assert (np.diff(y[grid <= 15]) >= -1e-9).all()
    assert y[grid <= 2].max() < 0.05
    assert y[(grid >= 13) & (grid <= 20)].min() > 0.9


# ---------- checks on the real output (skip until built) ----------

OUT = ROOT / "data" / "processed" / "curtailment.parquet"
needs_data = pytest.mark.skipif(not OUT.exists(), reason="curtailment.parquet not built")


@pytest.fixture(scope="module")
def built():
    return pd.read_parquet(OUT)


@needs_data
def test_output_matches_contract_and_invariants(built):
    farms = pd.read_csv(ROOT / "reference" / "farms.csv")
    rc.check_output(built, farms)  # raises on any violation


@needs_data
def test_method_a_and_b_agree_on_daily_totals_for_top_5_farms(built):
    corr = rc.method_ab_daily_correlation(built, top_n=5)
    assert (corr > 0.6).all(), corr.to_dict()


@needs_data
def test_2025_totals_within_20pct_of_modo(built):
    totals = rc.annual_totals_twh(built, 2025)
    for farm, modo_twh in rc.MODO_2025_TWH.items():
        assert totals[farm] == pytest.approx(modo_twh, rel=0.20), (farm, totals[farm])
