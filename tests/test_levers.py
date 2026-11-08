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
    # the baseline is priced the same way, so a price-only change shows no physical delta (#43 review)
    assert r["baseline"]["replacement_gas_gbp"] == pytest.approx(r["scenario"]["replacement_gas_gbp"], rel=1e-9)
    assert r["delta"]["replacement_gas_gbp"] == pytest.approx(0.0, abs=1e-6)


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


def test_negative_switch_off_price_is_rejected():
    with pytest.raises(ValueError):
        simulate({"switch_off_price": {"mode": "flat", "value": -50.0}})


def test_north_link_matches_an_equal_b4_upgrade_on_b4():
    """Same maths on B4: a north link of X MW takes X off the surplus before B4, like X more MW of B4."""
    link = simulate({"subsea_link": {"zone": "north_scotland", "gw": 2.0, "availability": 0.95}})
    b4 = simulate({"b4": {"mode": "offset", "mw": 1900.0}})
    assert link["scenario"]["by_boundary_twh"]["B4"] == pytest.approx(b4["scenario"]["by_boundary_twh"]["B4"], abs=1e-9)
    # but the link also skips B6, so it can only do better there
    assert link["scenario"]["by_boundary_twh"]["B6"] <= b4["scenario"]["by_boundary_twh"]["B6"] + 1e-9


BATTERY_N = {"battery_id": "bn", "lat": 57.5, "lon": -4.2, "zone": "north_scotland",
             "power_mw": 300, "energy_mwh": 600, "efficiency": 0.88, "dispatcher": "rule"}


@pytest.mark.parametrize("extra", [
    {"subsea_link": {"zone": "north_scotland", "gw": 0.5, "availability": 1.0}},
    {"subsea_link": {"zone": "north_scotland", "gw": 3.0, "availability": 1.0}},
    {"demand_growth": {"north_scotland": {"data_centres_gw": 0.5}}},
    # the case from the #43 review: north power all fits down the link while the south has B6 waste.
    # The old room (ex4 + ex6) let the battery 'capture' link flow: 2,727 MWh captured vs 2,467 removed.
    {"subsea_link": {"zone": "north_scotland", "gw": 3.0, "availability": 1.0}, "b6": {"mode": "fixed", "mw": 50.0}},
])
def test_battery_capture_equals_waste_removed_with_new_levers(extra):
    """No free energy: every MWh a battery 'captures' is a MWh less wasted (#43 review, blocker 1)."""
    without = simulate(extra)
    with_batt = simulate({**extra, "batteries": [BATTERY_N]})
    removed = (without["scenario"]["curtailed_twh"] - with_batt["scenario"]["curtailed_twh"]) * 1e6
    captured = with_batt["batteries"][0]["captured_curtailment_mwh"]
    assert removed == pytest.approx(captured, rel=1e-6, abs=1e-3)
    if extra.get("subsea_link", {}).get("gw") == 0.5:
        assert captured > 0   # a small link leaves waste to catch, so the check above is not trivially 0 = 0


def test_north_room_caps_b6_relief_at_the_b4_flow_when_there_is_a_link():
    from sim.engine import _north_room
    assert _north_room(0.0, 500.0, 0.0, 1000.0) == 0.0        # everything goes down the link: nothing to relieve
    assert _north_room(100.0, 500.0, 200.0, 1000.0) == 300.0  # B4 overflow + only the flow that crosses B4
    assert _north_room(100.0, 500.0, 0.0, 0.0) == 600.0       # no link: unchanged from before


def test_heat_pumps_follow_the_real_temperature():
    """#45: on a cold spell heat pumps draw more than the monthly average says; missing values fall back."""
    temp = np.full(len(YEAR), 8.0)
    cold = (YEAR.month == 3) & (YEAR.day <= 7)
    temp[cold] = -5.0
    mw = levers.demand_growth_mw(YEAR, {"heat_pumps_gw": 1.0}, temp)
    assert mw.mean() == pytest.approx(1000.0, rel=1e-9)          # still averages to the GW asked for
    assert mw[cold].mean() > 2 * mw[(YEAR.month == 3) & (YEAR.day > 7)].mean()
    gaps = np.full(len(YEAR), np.nan)
    assert np.allclose(levers.demand_growth_mw(YEAR, {"heat_pumps_gw": 1.0}, gaps),
                       levers.demand_growth_mw(YEAR, {"heat_pumps_gw": 1.0}))   # all missing = monthly table


def test_engine_reads_the_fixture_temperature():
    from sim import engine
    inp = engine.load_inputs()
    assert inp.temp_n is not None and np.isfinite(inp.temp_n).mean() > 0.9
