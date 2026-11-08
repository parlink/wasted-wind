"""Batteries sited at a wind farm (#15 follow-up): they catch only their own farms' waste. Run on app/fixtures."""
import os

import numpy as np
import pytest

os.environ["WW_DATA_SOURCE"] = "fixtures"

from sim import engine  # noqa: E402
from sim.engine import farm_units, simulate  # noqa: E402


def farm_waste(res, farm_ids):
    by = {f["farm_id"]: f for f in res["farms"]}
    return sum(by[f]["curtailed_mwh"] for f in farm_ids)


@pytest.fixture(scope="module")
def inp():
    return engine.load_inputs()


def battery_at(inp, farm_id, site="farm", **kw):
    u = np.flatnonzero(inp.farm == farm_id)[0]
    zone = "north_scotland" if inp.north[u] else "south_scotland"
    return {"battery_id": f"b_{farm_id}", "lat": float(inp.unit_lat[u]), "lon": float(inp.unit_lon[u]), "zone": zone,
            "power_mw": 150, "energy_mwh": 300, "efficiency": 0.88, "dispatcher": "rule", "site": site,
            "radius_km": kw.get("radius_km", 5.0)}


def farms_by_waste(base):
    return [f["farm_id"] for f in sorted(base["farms"], key=lambda f: -f["baseline_curtailed_mwh"])]


@pytest.fixture(scope="module")
def base():
    return simulate({})


def test_grid_battery_has_no_farm_units(inp):
    b = {"lat": 57.5, "lon": -4.2, "zone": "north_scotland", "site": "grid"}
    assert not farm_units(inp, b).any()


def test_farm_battery_links_to_nearest_farm_in_its_zone(inp, base):
    top = farms_by_waste(base)[0]
    b = battery_at(inp, top)
    u = farm_units(inp, b)
    assert u.any() and set(inp.farm[u]) == {top}
    # far from every farm: still tied to the nearest one in its zone, never a farm in the other zone
    far = {**b, "lat": b["lat"] + 0.9, "radius_km": 1.0}
    u = farm_units(inp, far)
    assert u.sum() >= 1 and len(set(inp.farm[u])) == 1
    assert (inp.north[u] == (b["zone"] == "north_scotland")).all()


def test_farm_battery_catches_no_more_than_a_grid_battery_at_the_same_spot(inp, base):
    top = farms_by_waste(base)[0]
    farm = simulate({"batteries": [battery_at(inp, top)]})
    grid = simulate({"batteries": [battery_at(inp, top, site="grid")]})
    assert farm["batteries"][0]["captured_curtailment_mwh"] <= grid["batteries"][0]["captured_curtailment_mwh"] + 1e-6
    assert farm["batteries"][0]["farm_ids"] == [top]


def test_farm_battery_waste_removed_equals_captured_and_lands_on_its_farm(inp, base):
    top = farms_by_waste(base)[0]
    r = simulate({"batteries": [battery_at(inp, top)]})
    captured = r["batteries"][0]["captured_curtailment_mwh"]
    assert captured > 0
    removed = (base["scenario"]["curtailed_twh"] - r["scenario"]["curtailed_twh"]) * 1e6
    assert removed == pytest.approx(captured, rel=1e-6, abs=1e-3)
    # the reduction is on its own farm; everyone else's total is unchanged
    own = farm_waste(base, [top]) - farm_waste(r, [top])
    assert own == pytest.approx(captured, rel=1e-6, abs=1e-3)
    others = [f["farm_id"] for f in base["farms"] if f["farm_id"] != top]
    assert farm_waste(r, others) == pytest.approx(farm_waste(base, others), rel=1e-6, abs=1e-3)


def test_placement_matters(inp, base):
    order = [f for f in farms_by_waste(base) if farm_waste(base, [f]) > 0]
    big, small = order[0], order[-1]
    rb = simulate({"batteries": [battery_at(inp, big)]})
    rs = simulate({"batteries": [battery_at(inp, small)]})
    assert rb["batteries"][0]["captured_curtailment_mwh"] > rs["batteries"][0]["captured_curtailment_mwh"]
    # and it can never catch more than its farm wasted
    assert rs["batteries"][0]["captured_curtailment_mwh"] <= farm_waste(base, [small]) + 1e-6


@pytest.mark.parametrize("quality", ["none", "ours", "perfect"])
def test_farm_battery_runs_with_every_forecast_quality(inp, base, quality):
    top = farms_by_waste(base)[0]
    r = simulate({"batteries": [battery_at(inp, top)], "forecast_quality": quality})
    assert r["batteries"][0]["captured_curtailment_mwh"] >= 0
    assert all(f["curtailed_mwh"] >= -1e-9 for f in r["farms"])


# ---------- #47 review: several batteries, mixed sites, other levers ----------

def _grid_north(bid="g"):
    return {"battery_id": bid, "lat": 57.5, "lon": -4.2, "zone": "north_scotland", "power_mw": 150,
            "energy_mwh": 300, "efficiency": 0.88, "dispatcher": "rule", "site": "grid"}


def _mixes(inp, base):
    order = farms_by_waste(base)
    top = order[0]
    other_north = next(f for f in order[1:] if inp.north[np.flatnonzero(inp.farm == f)[0]]
                       == inp.north[np.flatnonzero(inp.farm == top)[0]])
    farm_a, farm_b = battery_at(inp, top), {**battery_at(inp, top), "battery_id": "b2"}
    return {
        "grid then farm": [_grid_north(), farm_a],
        "farm then grid": [farm_a, _grid_north()],
        "two sharing a farm": [farm_a, farm_b],
        "two farms, one zone": [farm_a, battery_at(inp, other_north)],
    }


@pytest.mark.parametrize("mix", ["grid then farm", "farm then grid", "two sharing a farm", "two farms, one zone"])
@pytest.mark.parametrize("extra", [{}, {"subsea_link": {"zone": "north_scotland", "gw": 0.5, "availability": 1.0}},
                                   {"demand_growth": {"north_scotland": {"data_centres_gw": 0.3}}}])
def test_many_batteries_conserve_energy_and_rows(inp, base, mix, extra):
    bats = _mixes(inp, base)[mix]
    without = simulate(extra)
    r = simulate({**extra, "batteries": bats})
    captured = sum(b["captured_curtailment_mwh"] for b in r["batteries"])
    removed = (without["scenario"]["curtailed_twh"] - r["scenario"]["curtailed_twh"]) * 1e6
    assert removed == pytest.approx(captured, rel=1e-6, abs=1e-3)
    # per-unit waste adds up to the zone waste in every half hour
    core = engine.run_core(inp, engine.with_defaults({**extra, "batteries": bats}))
    np.testing.assert_allclose(core["cut_mw"].sum(axis=1), core["ex4"] + core["ex6"], atol=1e-6)
    assert (core["cut_mw"] >= -1e-9).all() and (core["cut_mw"] <= core["pot"] + 1e-6).all()


def test_two_batteries_at_one_farm_take_it_down_together(inp, base):
    top = farms_by_waste(base)[0]
    a, b = battery_at(inp, top), {**battery_at(inp, top), "battery_id": "b2"}
    r = simulate({"batteries": [a, b]})
    captured = sum(x["captured_curtailment_mwh"] for x in r["batteries"])
    assert farm_waste(base, [top]) - farm_waste(r, [top]) == pytest.approx(captured, rel=1e-6, abs=1e-3)
    assert farm_waste(r, [top]) >= -1e-6
