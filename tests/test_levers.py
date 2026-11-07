"""Full-tier levers (#15): subsea link (3), Scottish demand growth (6), economics (9, 10, 11). Run on app/fixtures."""
import os

import numpy as np
import pandas as pd
import pytest

os.environ["WW_DATA_SOURCE"] = "fixtures"

from app import contract  # noqa: E402
from sim import levers  # noqa: E402
from sim.engine import _boundaries, simulate  # noqa: E402

YEAR = pd.date_range("2025-01-01", "2026-01-01", freq="30min", tz="UTC", inclusive="left")


def wasted(res):
    return res["scenario"]["curtailed_twh"]


@pytest.fixture(scope="module")
def base():
    return simulate({})


# ---------- lever 6: demand growth ----------

def test_demand_growth_averages_to_the_gw_asked_for():
    for kind in ("heat_pumps_gw", "evs_gw", "data_centres_gw"):
        mw = levers.demand_growth_mw(YEAR, {kind: 2.0})
        assert mw.mean() == pytest.approx(2000.0, rel=1e-9), kind
        assert (mw >= 0).all()


def test_heat_pumps_peak_in_winter_and_evs_overnight():
    hp = levers.demand_growth_mw(YEAR, {"heat_pumps_gw": 1.0})
    jan, jul = hp[YEAR.month == 1].mean(), hp[YEAR.month == 7].mean()
    assert jan > 3 * jul
    ev = levers.demand_growth_mw(YEAR, {"evs_gw": 1.0})
    assert ev[YEAR.hour == 2].mean() > 2 * ev[YEAR.hour == 13].mean()
    dc = levers.demand_growth_mw(YEAR, {"data_centres_gw": 1.0})
    assert np.ptp(dc) == 0


def test_no_demand_growth_is_zero():
    assert not levers.demand_growth_mw(YEAR, {}).any()
    assert not levers.demand_growth_mw(YEAR, None).any()


def test_more_scottish_demand_means_less_waste(base):
    north = simulate({"demand_growth": {"north_scotland": {"data_centres_gw": 2.0}}})
    assert wasted(north) < wasted(base)
    more = simulate({"demand_growth": {"north_scotland": {"data_centres_gw": 4.0}}})
    assert wasted(more) <= wasted(north)


def test_south_demand_cannot_help_b4_waste(base):
    south = simulate({"demand_growth": {"south_scotland": {"data_centres_gw": 5.0}}})
    # demand south of B4 doesn't make room on B4 itself
    assert south["scenario"]["by_boundary_twh"]["B4"] == pytest.approx(base["scenario"]["by_boundary_twh"]["B4"], abs=1e-9)
    assert wasted(south) <= wasted(base)


# ---------- lever 3: subsea link ----------

def test_link_physics_takes_surplus_before_the_boundaries():
    pot_n, pot_s = np.array([3000.0]), np.array([500.0])
    res_n, res_s = np.array([0.0]), np.array([0.0])
    l4, l6 = np.array([1000.0]), np.array([5000.0])
    ex4, *_ = _boundaries(pot_n, pot_s, res_n, res_s, l4, l6)
    assert ex4[0] == pytest.approx(2000.0)
    ex4, ex6, flow4, flow6 = _boundaries(pot_n, pot_s, res_n, res_s, l4, l6, lk_n=1500.0)
    assert ex4[0] == pytest.approx(500.0)          # 3000 - 1500 on the link - 1000 over B4
    assert flow4[0] == pytest.approx(1000.0)
    # a link never creates waste and never carries an importing zone's deficit
    ex4, ex6, flow4, _ = _boundaries(np.array([0.0]), pot_s, np.array([-800.0]), res_s, l4, l6, lk_n=1500.0)
    assert ex4[0] == 0 and flow4[0] == pytest.approx(-800.0)


def test_north_link_cuts_waste_more_than_south_link(base):
    north = simulate({"subsea_link": {"zone": "north_scotland", "gw": 2.0, "availability": 1.0}})
    south = simulate({"subsea_link": {"zone": "south_scotland", "gw": 2.0, "availability": 1.0}})
    assert wasted(north) < wasted(base)
    assert wasted(north) <= wasted(south) <= wasted(base)
    # a south link can't relieve B4
    assert south["scenario"]["by_boundary_twh"]["B4"] == pytest.approx(base["scenario"]["by_boundary_twh"]["B4"], abs=1e-9)


def test_link_availability_and_zero_gw():
    assert levers.subsea_link_mw({"zone": "north_scotland", "gw": 2.0, "availability": 0.5}) == (1000.0, 0.0)
    assert levers.subsea_link_mw({"zone": "south_scotland", "gw": 1.0, "availability": 1.0}) == (0.0, 1000.0)
    assert levers.subsea_link_mw(None) == (0.0, 0.0)
    off = simulate({"subsea_link": {"zone": "north_scotland", "gw": 0.0, "availability": 1.0}})
    assert off["delta"]["curtailed_twh"] == pytest.approx(0.0, abs=1e-12)


# ---------- levers 9, 10, 11: economics ----------

def test_gas_and_carbon_prices_change_only_the_money(base):
    r = simulate({"gas_price_p_therm": 160.0, "carbon_price_gbp_t": 100.0})
    assert wasted(r) == wasted(base)
    mwh = wasted(base) * 1e6
    assert r["scenario"]["replacement_gas_gbp"] == pytest.approx(mwh * contract.replacement_gbp_mwh(160.0, 100.0), rel=1e-6)
    assert r["delta"]["replacement_gas_gbp"] > 0


def test_carbon_intensity_scales_co2(base):
    r = simulate({"carbon_intensity_t_mwh": 0.74})
    assert r["scenario"]["co2_t"] == pytest.approx(2 * base["scenario"]["co2_t"], rel=1e-6)


def test_switch_off_price_multiplier_and_flat(base):
    double = simulate({"switch_off_price": {"mode": "multiplier", "value": 2.0}})
    assert double["scenario"]["payments_gbp"] == pytest.approx(2 * base["scenario"]["payments_gbp"], rel=1e-6)
    flat = simulate({"switch_off_price": {"mode": "flat", "value": 50.0}})
    assert flat["scenario"]["payments_gbp"] == pytest.approx(wasted(base) * 1e6 * 50.0, rel=1e-6)
    assert wasted(flat) == wasted(base)


def test_every_lever_is_applied():
    r = simulate({"subsea_link": {"zone": "north_scotland", "gw": 1.0, "availability": 0.9},
                  "demand_growth": {"north_scotland": {"heat_pumps_gw": 1.0}}, "gas_price_p_therm": 100.0})
    assert r["levers_not_yet_applied"] == []


def test_zero_gas_price_is_zero_not_default(base):
    r = simulate({"gas_price_p_therm": 0.0, "carbon_price_gbp_t": 0.0})
    assert r["scenario"]["replacement_gas_gbp"] == 0.0


def test_odd_inputs_fail_loudly_or_read_as_unset(base):
    with pytest.raises(ValueError):
        levers.subsea_link_mw({"zone": "north", "gw": 1.0})
    r = simulate({"demand_growth": {"north_scotland": None, "south_scotland": {"evs_gw": None}},
                  "switch_off_price": {"mode": "multiplier", "value": None}})
    assert r["delta"]["curtailed_twh"] == pytest.approx(0.0, abs=1e-12)
    assert r["scenario"]["payments_gbp"] == pytest.approx(base["scenario"]["payments_gbp"])
