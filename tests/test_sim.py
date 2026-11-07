"""Simulator sanity checks from BRIEF.md Part 4 / Validation, run against app/fixtures."""
import os
import time

import numpy as np
import pytest

os.environ["WW_DATA_SOURCE"] = "fixtures"

from sim import engine  # noqa: E402
from sim.calibrate import calibration_report  # noqa: E402
from sim.engine import Inputs, run_core, simulate, waterfall  # noqa: E402


def total(res, key="curtailed_twh"):
    return res["scenario"][key]


@pytest.fixture(scope="module")
def base():
    return simulate({})


def test_historical_settings_reproduce_part1():
    rep = calibration_report()
    assert rep["historical_twh"] > 0
    # fixtures are generated with the same zone physics, so the match should be near exact
    assert abs(rep["error_pct"]) < 2.0, rep


def test_no_levers_is_exactly_baseline(base):
    assert base["scenario"] == base["baseline"]
    assert all(v == 0 for k, v in base["delta"].items() if not isinstance(v, dict))
    # forecast quality only matters when there are batteries
    assert simulate({"forecast_quality": "perfect"})["scenario"] == base["scenario"]


def test_infinite_boundaries_waste_near_zero(base):
    r = simulate({"b4": {"mode": "offset", "mw": 20000}, "b6": {"mode": "offset", "mw": 20000}})
    assert total(r) <= 1e-3 * total(base)


def test_zero_wind_zero_waste():
    r = simulate({"weather": {"wind_scale": 0.0}})
    assert total(r) == 0
    assert r["scenario"]["payments_gbp"] == 0


@pytest.mark.parametrize("which", ["b4", "b6"])
def test_raising_a_boundary_never_increases_waste(which):
    wastes = [total(simulate({which: {"mode": "offset", "mw": mw}})) for mw in (-800, -300, 0, 300, 800, 2000)]
    assert all(b <= a + 1e-12 for a, b in zip(wastes, wastes[1:])), wastes


def test_more_wind_more_waste(base):
    assert total(simulate({"weather": {"wind_scale": 1.2}})) > total(base) > total(simulate({"weather": {"wind_scale": 0.8}}))


def test_window_slices_results(base):
    day = simulate({"window": {"start": "2025-11-06", "end": "2025-11-07"}})   # windiest fixture day
    assert 0 < total(day) < total(base)
    assert day["window"]["start"].startswith("2025-11-06")
    calm = simulate({"window": {"start": "2025-11-03", "end": "2025-11-04"}})  # no waste in the fixtures
    assert total(calm) == 0


BATTERY = {"battery_id": "b1", "lat": 57.5, "lon": -4.2, "zone": "north_scotland",
           "power_mw": 200, "energy_mwh": 400, "efficiency": 0.88, "dispatcher": "rule"}


def test_battery_captures_waste_and_reduces_it(base):
    r = simulate({"batteries": [BATTERY], "forecast_quality": "perfect"})
    b = r["batteries"][0]
    assert b["captured_curtailment_mwh"] > 0 and b["revenue_gbp"] > 0 and b["cycles"] > 0
    reduction_mwh = (total(base) - total(r)) * 1e6
    assert reduction_mwh == pytest.approx(b["captured_curtailment_mwh"], rel=1e-6)
    assert r["baseline"] == base["baseline"]


def test_forecast_quality_ordering():
    cap = {q: simulate({"batteries": [BATTERY], "forecast_quality": q})["batteries"][0]["captured_curtailment_mwh"]
           for q in ("none", "ours", "perfect")}
    assert cap["perfect"] >= cap["none"] - 1e-6, cap


def test_south_battery_cannot_absorb_b4_waste():
    south = dict(BATTERY, battery_id="s1", zone="south_scotland")
    r = simulate({"batteries": [south], "forecast_quality": "perfect"})
    base = simulate({})
    # B4 waste is behind B4: a battery south of it can't take it
    assert r["scenario"]["by_boundary_twh"]["B4"] == pytest.approx(base["scenario"]["by_boundary_twh"]["B4"])


def test_waterfall_cheapest_first():
    m = np.array([[100.0, 100.0, 100.0]])          # columns already in priority order
    np.testing.assert_allclose(waterfall(m, np.array([150.0])), [[100.0, 50.0, 0.0]])
    np.testing.assert_allclose(waterfall(m, np.array([0.0])), [[0.0, 0.0, 0.0]])
    np.testing.assert_allclose(waterfall(m, np.array([999.0])), m)


def test_full_size_run_is_fast():
    """~22 months of half hours x 50 units, as with real data. Target from #9: under 1 s."""
    rng = np.random.default_rng(0)
    T, U = 33_000, 50
    inp = Inputs.synthetic(rng, T, U)
    t0 = time.perf_counter()
    run_core(inp, engine.with_defaults({}))
    no_batt = time.perf_counter() - t0
    t0 = time.perf_counter()
    run_core(inp, engine.with_defaults({"batteries": [BATTERY]}))
    with_batt = time.perf_counter() - t0
    print(f"\nfull-size run: {no_batt*1000:.0f} ms without batteries, {with_batt*1000:.0f} ms with one")
    assert no_batt < 1.0
    assert with_batt < 3.0   # generous for slow CI machines; locally it should be well under 1 s
