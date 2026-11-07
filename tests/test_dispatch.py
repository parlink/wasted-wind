"""Tests for dispatch.rule_based.plan_day (CONTRACT §6) and dispatch.scorecard."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dispatch import rule_based as rb
from dispatch import scorecard as sc

ROOT = Path(__file__).resolve().parent.parent
BAT = {"battery_id": "t", "power_mw": 100.0, "energy_mwh": 200.0, "efficiency": 0.88,
       "zone": "north_scotland"}


def day(waste=None, price=None, head=None):
    w = np.zeros(48) if waste is None else np.asarray(waste, float)
    p = np.full(48, 50.0) if price is None else np.asarray(price, float)
    h = np.full(48, 1000.0) if head is None else np.asarray(head, float)
    return w, p, h


# ---------- plan_day: contract shape and physical limits ----------

def test_plan_day_contract_shape():
    out = rb.plan_day(BAT, *day(), soc_start_mwh=0.0)
    assert set(out) == {"action", "power_mw", "soc_mwh", "reason"}
    for k in out:
        assert len(out[k]) == 48
    assert set(out["action"]) <= {"charge", "hold", "discharge"}
    assert (out["power_mw"] >= 0).all()
    assert (out["power_mw"][out["action"] == "hold"] == 0).all()
    assert all(isinstance(r, str) and r for r in out["reason"])


@pytest.mark.parametrize("seed", range(20))
def test_soc_bounds_and_energy_accounting_on_random_days(seed):
    rng = np.random.default_rng(seed)
    w = np.where(rng.random(48) < 0.3, rng.uniform(0, 150, 48), 0.0)
    p = rng.normal(60, 40, 48)
    h = np.where(rng.random(48) < 0.5, rng.uniform(0, 300, 48), 0.0)
    soc0 = rng.uniform(0, BAT["energy_mwh"])
    out = rb.plan_day(BAT, w, p, h, soc0)
    soc, a, mw = out["soc_mwh"], out["action"], out["power_mw"]
    assert (soc >= -1e-9).all() and (soc <= BAT["energy_mwh"] + 1e-9).all()
    e_in = (mw[a == "charge"] * 0.5).sum()
    e_out = (mw[a == "discharge"] * 0.5).sum()
    assert e_out <= soc0 + e_in * BAT["efficiency"] + 1e-6
    assert (mw <= BAT["power_mw"] + 1e-9).all()
    assert (mw[a == "discharge"] <= h[a == "discharge"] + 1e-9).all()     # boundary room
    assert (mw[a == "charge"] * 0.5 <= w[a == "charge"] + 1e-9).all()     # only forecast waste
    # SoC path is consistent with the actions
    prev = np.r_[soc0, soc[:-1]]
    step = np.where(a == "charge", mw * 0.5 * BAT["efficiency"], np.where(a == "discharge", -mw * 0.5, 0))
    assert np.allclose(soc, prev + step)


def test_charges_on_forecast_waste_and_says_why():
    w = np.zeros(48)
    w[4:8] = 60.0
    out = rb.plan_day(BAT, *day(waste=w), soc_start_mwh=0.0)
    assert (out["action"][4:8] == "charge").all()
    assert "waste forecast" in out["reason"][4] and "B4" in out["reason"][4]


def test_empties_ahead_of_a_waste_spike():
    # Full battery, waste from 14:00, a price peak at 10:00: sell first, then soak up the waste.
    w = np.zeros(48)
    w[28:34] = 80.0
    p = np.full(48, 40.0)
    p[20:22] = 150.0
    out = rb.plan_day(BAT, w, p, np.full(48, 500.0), soc_start_mwh=200.0)
    assert (out["action"][:28] == "discharge").any()
    first_dis = int(np.argmax(out["action"] == "discharge"))
    assert "ahead of" in out["reason"][first_dis]
    assert (out["action"][28:34] == "charge").any()


def test_never_discharges_without_boundary_room_or_at_negative_price():
    p = np.full(48, -20.0)
    out = rb.plan_day(BAT, *day(price=p), soc_start_mwh=200.0)
    assert not (out["action"] == "discharge").any()
    out = rb.plan_day(BAT, *day(head=np.zeros(48)), soc_start_mwh=200.0)
    assert not (out["action"] == "discharge").any()


def test_sells_in_top_price_half_hours_when_no_waste_expected():
    p = np.full(48, 30.0)
    p[36:40] = 120.0
    out = rb.plan_day(BAT, *day(price=p), soc_start_mwh=200.0)
    assert (out["action"][36:40] == "discharge").all()
    assert (out["action"][:36] == "hold").all()


# ---------- scorecard: replaying a plan against what really happened ----------

def test_execute_only_captures_actual_waste_and_respects_actual_headroom():
    plan = {"action": np.array(["charge", "charge", "discharge", "hold"]),
            "power_mw": np.array([100.0, 100.0, 100.0, 0.0])}
    actual_waste = np.array([20.0, 0.0, 0.0, 30.0])     # MWh per half hour
    head = np.array([0.0, 0.0, 40.0, 0.0])
    price = np.array([50.0, 50.0, 80.0, 50.0])
    r = sc.execute(BAT, plan, actual_waste, price, head, soc_start_mwh=100.0)
    assert r["captured_curtailment_mwh"].tolist()[:2] == [20.0, 0.0]     # nothing to catch in 2
    assert r["captured_curtailment_mwh"][3] == 30.0                       # reacts live to waste
    assert r["power_mw"][2] == pytest.approx(40.0)                        # capped by headroom
    assert r["revenue_gbp"][2] == pytest.approx(40.0 * 0.5 * 80.0)
    assert (r["soc_mwh"] >= 0).all() and (r["soc_mwh"] <= 200.0).all()


def test_expected_price_uses_only_previous_seven_days():
    price = np.arange(48 * 10, dtype=float)
    exp = sc.expected_price(price, day_index=8)
    days = price.reshape(10, 48)[1:8]
    assert np.allclose(exp, days.mean(axis=0))


def test_capture_ratio_definition():
    res = sc.capture_ratios({"perfect": 200.0, "forecast": 150.0, "reactive": 100.0, "do_nothing": 0.0})
    assert res == {"perfect": 1.0, "forecast": 0.75, "reactive": 0.5, "do_nothing": 0.0}


# ---------- real output (skips until built) ----------

SCORE = ROOT / "data" / "processed" / "scorecard.json"
SCHED = ROOT / "data" / "processed" / "schedules.parquet"
SCHED_COLS = ["battery_id", "farm_id", "zone", "strategy", "dispatcher", "period_start", "plan_issued_at",
              "action", "power_mw", "soc_mwh", "captured_curtailment_mwh", "revenue_gbp", "reason"]


@pytest.mark.skipif(not SCHED.exists(), reason="schedules.parquet not built")
def test_built_schedules_match_contract_and_physics():
    s = pd.read_parquet(SCHED)
    assert list(s.columns) == SCHED_COLS
    bats = {b["battery_id"]: b for b in json.loads(SCORE.read_text())["batteries"]}
    cap = s.battery_id.map({k: v["energy_mwh"] for k, v in bats.items()})
    assert (s.soc_mwh >= -1e-6).all() and (s.soc_mwh <= cap + 1e-6).all()
    assert (s.reason.str.len() > 0).all()
    assert (s.plan_issued_at < s.period_start).all()


@pytest.mark.skipif(not SCHED.exists(), reason="schedules.parquet not built")
def test_perfect_beats_ours_beats_do_nothing_every_month():
    s = pd.read_parquet(SCHED, columns=["strategy", "period_start", "revenue_gbp"])
    m = s.groupby([s.period_start.dt.strftime("%Y-%m"), "strategy"]).revenue_gbp.sum().unstack()
    assert (m["perfect"] >= m["forecast"] - 1e-6).all(), m
    assert (m["forecast"] >= m["do_nothing"] - 1e-6).all(), m
