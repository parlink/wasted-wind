"""Tests for dispatch.optimiser.plan_day: the LP dispatcher with the CONTRACT §6 signature."""
import time

import numpy as np
import pytest

from dispatch import optimiser as opt
from dispatch import rule_based as rb
from dispatch import scorecard as sc

BAT = {"battery_id": "t", "power_mw": 100.0, "energy_mwh": 200.0, "efficiency": 0.88,
       "zone": "north_scotland"}


def simple_day():
    w = np.zeros(48)
    w[4:8] = 60.0                       # waste 02:00-04:00
    price = np.full(48, 30.0)
    price[36:40] = 150.0                # evening peak 18:00-20:00
    head = np.where(w > 0, 0.0, 500.0)  # no room while curtailing
    return w, price, head


def test_contract_shape():
    out = opt.plan_day(BAT, *simple_day(), soc_start_mwh=0.0)
    assert set(out) == {"action", "power_mw", "soc_mwh", "reason"}
    assert all(len(v) == 48 for v in out.values())
    assert set(out["action"]) <= {"charge", "hold", "discharge"}
    assert (out["power_mw"] >= 0).all()
    assert (out["power_mw"][out["action"] == "hold"] == 0).all()
    assert all(isinstance(r, str) and r for r in out["reason"])


@pytest.mark.parametrize("seed", range(15))
def test_physical_limits_on_random_days(seed):
    rng = np.random.default_rng(seed)
    w = np.where(rng.random(48) < 0.3, rng.uniform(0, 150, 48), 0.0)
    p = rng.normal(60, 40, 48)
    h = np.where(w > 0, 0.0, rng.uniform(0, 300, 48))
    out = opt.plan_day(BAT, w, p, h, rng.uniform(0, 200))
    a, mw = out["action"], out["power_mw"]
    assert (out["soc_mwh"] >= -1e-6).all() and (out["soc_mwh"] <= 200 + 1e-6).all()
    assert (mw <= 100 + 1e-6).all()
    assert (mw[a == "charge"] * 0.5 <= w[a == "charge"] + 1e-6).all()
    assert (mw[a == "discharge"] <= h[a == "discharge"] + 1e-6).all()
    assert not ((a == "discharge") & (p <= 0)).any()


def test_agrees_with_rule_based_on_a_simple_day():
    # The issue's acceptance test: one block of waste, one price peak. Both should charge in
    # the waste and sell it all in the peak.
    w, price, head = simple_day()
    res = {}
    for name, fn in (("rule", rb.plan_day), ("lp", opt.plan_day)):
        plan = fn(BAT, w, price, head, 0.0)
        res[name] = sc.execute(BAT, plan, w, price, head, 0.0, plan["reason"])
    for r in res.values():
        assert (r["action"][4:8] == "charge").all()
        assert set(np.flatnonzero(r["action"] == "discharge")) <= set(range(36, 40))
    assert res["lp"]["revenue_gbp"].sum() == pytest.approx(res["rule"]["revenue_gbp"].sum(), rel=1e-6)
    assert res["lp"]["captured_curtailment_mwh"].sum() == pytest.approx(
        res["rule"]["captured_curtailment_mwh"].sum(), rel=1e-6)


def test_lp_plan_value_is_at_least_the_rule_plan_value():
    # On its own inputs the LP is optimal, so its planned revenue >= the rule plan's.
    rng = np.random.default_rng(11)
    for _ in range(10):
        w = np.where(rng.random(48) < 0.3, rng.uniform(0, 120, 48), 0.0)
        p = rng.normal(60, 40, 48)
        h = np.where(w > 0, 0.0, rng.uniform(0, 300, 48))
        lp = opt.plan_day(BAT, w, p, h, 0.0)
        r = sc.execute(BAT, rb.plan_day(BAT, w, p, h, 0.0), w, p, h, 0.0)
        assert opt.planned_revenue(lp, p) >= r["revenue_gbp"].sum() - 1e-6


def test_sells_unforecast_waste_the_same_day():
    # Same fix as #27 for the rule dispatcher: a zero forecast still leaves sale slots.
    _, price, _ = simple_day()
    plan = opt.plan_day(BAT, np.zeros(48), price, np.full(48, 500.0), 0.0)
    actual = np.zeros(48)
    actual[6] = 80.0
    r = sc.execute(BAT, plan, actual, price, np.full(48, 500.0), 0.0, plan["reason"])
    assert r["revenue_gbp"][36:40].sum() > 0


def test_each_solve_takes_milliseconds():
    w, price, head = simple_day()
    t = time.perf_counter()
    for _ in range(50):
        opt.plan_day(BAT, w, price, head, 0.0)
    assert (time.perf_counter() - t) / 50 < 0.05     # < 50 ms each, typically ~5 ms
