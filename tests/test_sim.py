"""Simulator sanity checks from BRIEF.md Part 4 / Validation, run against app/fixtures."""
import os
import time

import pandas as pd

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


# ---------- review of #22 (Aradhya) ----------

def _record_plan_signals(monkeypatch, quality):
    """Run a battery scenario and capture what each day's plan_day call was told."""
    seen = []
    real = engine.plan_day

    def spy(battery, curtailment_mwh, price, headroom, soc):
        seen.append(np.array(curtailment_mwh, dtype=float))
        return real(battery, curtailment_mwh, price, headroom, soc)
    monkeypatch.setattr(engine, "plan_day", spy)
    inp = engine.load_inputs()
    engine.run_core(inp, engine.with_defaults({"batteries": [BATTERY], "forecast_quality": quality}))
    return inp, seen


def test_no_forecast_battery_knows_nothing_about_future_waste(monkeypatch):
    _, seen = _record_plan_signals(monkeypatch, "none")
    assert seen and all((s == 0).all() for s in seen)


def test_our_forecast_battery_is_told_the_forecast_not_the_actual(monkeypatch):
    inp, seen = _record_plan_signals(monkeypatch, "ours")
    expected = np.nan_to_num(inp.fc_n) + np.nan_to_num(inp.fc_s)
    np.testing.assert_allclose(np.concatenate(seen), expected[: len(seen) * 48])


def test_forecast_after_day_ahead_cutoff_is_ignored(tmp_path, monkeypatch):
    from app import data
    fc = data.table("forecast.parquet")[0].copy()
    late = fc.copy()
    late["issued_at"] = late["hour_start"] - pd.Timedelta(hours=1)     # issued on the day: leaky
    late["curtailed_mwh_pred"] = 1e6
    cutoff = fc["hour_start"].dt.normalize() - pd.Timedelta(hours=14)
    assert (fc["issued_at"] <= cutoff).all()
    kept = pd.concat([fc, late])
    kept = kept[kept["issued_at"] <= kept["hour_start"].dt.normalize() - pd.Timedelta(hours=14)]
    assert kept["curtailed_mwh_pred"].max() < 1e6
    inp = engine.load_inputs()
    assert np.nanmax(inp.fc_n) < 1e5        # the real loader applies the same cutoff


def test_battery_soc_and_energy_conservation():
    inp = engine.load_inputs()
    r = engine.run_core(inp, engine.with_defaults({"batteries": [BATTERY], "forecast_quality": "perfect"}))
    p = r["plans"][0]
    assert (p["soc_mwh"] >= -1e-9).all() and (p["soc_mwh"] <= BATTERY["energy_mwh"] + 1e-9).all()
    energy_in = p["charge"].sum() * engine.H
    energy_out = p["discharge"].sum() * engine.H
    assert energy_out <= energy_in * BATTERY["efficiency"] + 1e-6
    assert (p["charge"] * p["discharge"] == 0).all()          # never both in one half hour


def test_wind_lever_is_continuous_near_cut_in():
    from sim.levers import potential_mw
    planned = np.array([[200.0]]); cap = np.array([300.0]); v = np.array([[4.0]])   # below MIN_CF
    a, b = potential_mw(planned, v, cap, 1.0), potential_mw(planned, v, cap, 1.01)
    assert abs(b - a).max() < 5.0, (a, b)


def test_windier_storm_never_wastes_less():
    from sim.levers import potential_mw
    planned = np.array([[250.0]]); cap = np.array([300.0]); v = np.array([[21.0]])   # strong hourly mean
    assert potential_mw(planned, v, cap, 1.3)[0, 0] >= potential_mw(planned, v, cap, 1.0)[0, 0]
    wastes = [total(simulate({"weather": {"wind_scale": s}})) for s in (0.7, 0.9, 1.0, 1.1, 1.3)]
    assert all(b >= a - 1e-12 for a, b in zip(wastes, wastes[1:])), wastes


def test_reports_input_quality():
    r = simulate({})
    assert r["input_quality"]["dropped_bmus"] == []
    assert isinstance(r["input_quality"]["gaps"], dict)


# ---------- per-farm allocation (#10, from the #22 calibration on real data) ----------

def test_historical_settings_reproduce_every_farm():
    from sim.calibrate import calibration_report
    by_farm = calibration_report()["by_farm"]
    assert (by_farm["simulated_mwh"] - by_farm["historical_mwh"]).abs().max() < 1e-3 * by_farm["historical_mwh"].max()


def test_allocation_follows_history_then_cheapest_first():
    from sim.engine import allocate
    pot = np.array([[100.0, 100.0, 100.0],     # history: unit 2 took all the waste
                    [100.0, 100.0, 100.0]])    # no history this period
    hist = np.array([[0.0, 0.0, 30.0],
                     [0.0, 0.0, 0.0]])
    out = allocate(pot, np.array([60.0, 150.0]), hist)
    np.testing.assert_allclose(out[0], [0.0, 0.0, 60.0])           # scaled-up historical pattern
    np.testing.assert_allclose(out[1], [100.0, 50.0, 0.0])         # cheapest first (column order)


def test_allocation_never_exceeds_output_and_conserves_excess():
    from sim.engine import allocate
    rng = np.random.default_rng(1)
    pot = rng.uniform(0, 100, (200, 6))
    hist = rng.uniform(0, 1, (200, 6)) * pot * (rng.uniform(0, 1, (200, 1)) > 0.5)
    ex = rng.uniform(0, 1, 200) * pot.sum(axis=1)
    out = allocate(pot, ex, hist)
    assert (out <= pot + 1e-9).all() and (out >= -1e-12).all()
    np.testing.assert_allclose(out.sum(axis=1), ex, rtol=1e-9, atol=1e-9)


# ---------- LP dispatcher per battery (#13 / #34) ----------

def test_lp_battery_is_planned_by_the_lp(monkeypatch):
    calls = []

    def fake_lp(battery, curtailment_mwh, price, headroom, soc):
        calls.append(battery["battery_id"])
        return engine.plan_day(battery, curtailment_mwh, price, headroom, soc)
    monkeypatch.setattr(engine, "lp_plan_day", fake_lp)
    monkeypatch.setattr(engine, "LP_DISPATCHER", "fake.lp")
    lp = {**BATTERY, "battery_id": "lp1", "dispatcher": "lp"}
    out = engine.simulate({"batteries": [BATTERY, lp]})
    assert calls and set(calls) == {"lp1"}
    assert out["dispatchers"] == {"b1": engine.DISPATCHER, "lp1": "fake.lp"}


def test_lp_battery_falls_back_to_rules_when_lp_missing(monkeypatch):
    monkeypatch.setattr(engine, "lp_plan_day", None)
    assert engine.planner_for({**BATTERY, "dispatcher": "lp"}) == (engine.plan_day, engine.DISPATCHER)
