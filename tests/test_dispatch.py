"""Tests for dispatch.rule_based.plan_day (CONTRACT §6) and dispatch.scorecard."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dispatch import rule_based as rb
from dispatch import optimiser as opt
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
def test_plan_and_execution_respect_physics_on_random_days(seed):
    rng = np.random.default_rng(seed)
    w = np.where(rng.random(48) < 0.3, rng.uniform(0, 150, 48), 0.0)
    p = rng.normal(60, 40, 48)
    h = np.where(rng.random(48) < 0.5, rng.uniform(0, 300, 48), 0.0)
    soc0 = rng.uniform(0, BAT["energy_mwh"])
    plan = rb.plan_day(BAT, w, p, h, soc0)
    a, mw = plan["action"], plan["power_mw"]
    assert (plan["soc_mwh"] >= -1e-9).all() and (plan["soc_mwh"] <= BAT["energy_mwh"] + 1e-9).all()
    assert (mw <= BAT["power_mw"] + 1e-9).all()
    assert (mw[a == "discharge"] <= h[a == "discharge"] + 1e-9).all()     # boundary room
    assert (mw[a == "charge"] * 0.5 <= w[a == "charge"] + 1e-9).all()     # only forecast waste
    # Execution against a different "actual" day: real physics must hold.
    aw = np.where(rng.random(48) < 0.3, rng.uniform(0, 150, 48), 0.0)
    r = sc.execute(BAT, plan, aw, p, h, soc0, plan["reason"])
    soc, ea, emw = r["soc_mwh"], r["action"], r["power_mw"]
    assert (soc >= -1e-9).all() and (soc <= BAT["energy_mwh"] + 1e-9).all()
    e_in = (emw[ea == "charge"] * 0.5).sum()
    e_out = (emw[ea == "discharge"] * 0.5).sum()
    assert e_out <= soc0 + e_in * BAT["efficiency"] + 1e-6
    prev = np.r_[soc0, soc[:-1]]
    step = np.where(ea == "charge", emw * 0.5 * BAT["efficiency"], np.where(ea == "discharge", -emw * 0.5, 0))
    assert np.allclose(soc, prev + step)
    assert (r["captured_curtailment_mwh"] <= aw + 1e-9).all()


def test_unplanned_waste_is_sold_the_same_day():
    # Review #27: a battery with no forecast that catches waste at 03:00 must still sell at the peak.
    plan = rb.plan_day(BAT, np.zeros(48), np.r_[np.full(36, 30.0), np.full(4, 150.0), np.full(8, 30.0)],
                       np.full(48, 500.0), soc_start_mwh=0.0)
    assert (plan["action"][36:40] == "discharge").all()          # intent, even though planned SoC is 0
    actual = np.zeros(48)
    actual[6] = 80.0
    r = sc.execute(BAT, plan, actual, np.r_[np.full(36, 30.0), np.full(4, 150.0), np.full(8, 30.0)],
                   np.full(48, 500.0), 0.0, plan["reason"])
    assert r["revenue_gbp"][36:40].sum() > 0


def test_sell_slots_count_headroom_capped_power():
    # Review #27: slots capped by headroom sell less, so more of them are needed.
    p = np.full(48, 30.0)
    p[30:40] = 100.0
    head = np.full(48, 25.0)                     # 25 MW of room: 12.5 MWh per slot
    out = rb.plan_day(BAT, np.zeros(48), p, head, soc_start_mwh=200.0)
    assert (out["action"] == "discharge").sum() >= 10


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


def test_expected_price_uses_seven_days_ending_two_days_before():
    # Review #27: the plan is issued at D-1 10:00, so D-1 is not complete; use D-8 .. D-2.
    price = np.arange(48 * 12, dtype=float)
    exp = sc.expected_price(price, day_index=10)
    days = price.reshape(12, 48)[2:9]          # days 2..8 = D-8 .. D-2 for D = 10
    assert np.allclose(exp, days.mean(axis=0))


def test_hindsight_bound_beats_any_feasible_schedule():
    rng = np.random.default_rng(3)
    T = 48 * 3
    waste = np.where(rng.random(T) < 0.3, rng.uniform(0, 80, T), 0.0)
    price = rng.normal(60, 40, T)
    head = np.where(waste > 0, 0.0, rng.uniform(0, 200, T))
    lp = opt.hindsight_bound(BAT, waste, price, head, soc_start_mwh=0.0)
    soc = lp["soc_mwh"]
    assert (soc >= -1e-6).all() and (soc <= BAT["energy_mwh"] + 1e-6).all()
    assert (lp["captured_curtailment_mwh"] <= waste + 1e-6).all()
    # any rule-based schedule executed on the same days is feasible, so it can't earn more
    soc0, rule_rev = 0.0, 0.0
    for d in range(3):
        sl = slice(d * 48, d * 48 + 48)
        plan = rb.plan_day(BAT, waste[sl], price[sl], head[sl], soc0)
        r = sc.execute(BAT, plan, waste[sl], price[sl], head[sl], soc0, plan["reason"])
        soc0, rule_rev = r["soc_mwh"][-1], rule_rev + r["revenue_gbp"].sum()
    assert lp["revenue_gbp"].sum() >= rule_rev - 1e-6


def test_synthetic_month_ordering_runs_in_ci():
    # Review #27: the real-data ordering test skips in CI; this one always runs.
    rng = np.random.default_rng(7)
    days = sc.PRE_DAYS + 30
    hist = pd.date_range("2026-05-01T00:00Z", periods=48 * days, freq="30min")
    price = rng.normal(70, 35, len(hist))
    waste = np.where(rng.random(len(hist)) < 0.25, rng.uniform(0, 120, len(hist)), 0.0)
    head = {"north_scotland": np.where(waste > 0, 0.0, rng.uniform(0, 400, len(hist)))}
    f = {"waste": waste, "fc": waste * rng.uniform(0.5, 1.5, len(hist)), "avoided_price": np.full(len(hist), 30.0)}
    b = sc.BATTERIES[0]
    rev = {s: sc.replay(b, s, hist, price, head["north_scotland"], f).revenue_gbp.sum()
           for s in ("do_nothing", "reactive", "forecast")}
    rev["perfect_lp"] = opt.hindsight_bound(b, waste[sc.PRE_DAYS * 48:], price[sc.PRE_DAYS * 48:], head["north_scotland"][sc.PRE_DAYS * 48:],
                                            0.0)["revenue_gbp"].sum()
    assert rev["perfect_lp"] >= rev["forecast"] - 1e-6 and rev["perfect_lp"] >= rev["reactive"] - 1e-6
    assert rev["forecast"] >= rev["do_nothing"] and rev["reactive"] >= rev["do_nothing"]


def test_capture_ratio_definition():
    res = sc.capture_ratios({"perfect/lp": 200.0, "perfect/rule": 180.0, "forecast/rule": 150.0,
                             "reactive/rule": 100.0, "do_nothing/rule": 0.0})
    assert res == {"perfect/lp": 1.0, "perfect/rule": 0.9, "forecast/rule": 0.75,
                   "reactive/rule": 0.5, "do_nothing/rule": 0.0}


def test_execute_never_discharges_above_rated_power():
    # Review #27 follow-up: a plan asking for more than the rating must be capped, or the LP
    # (which is capped) would no longer be an upper bound.
    plan = {"action": np.array(["discharge"]), "power_mw": np.array([500.0])}
    r = sc.execute(BAT, plan, np.zeros(1), np.array([80.0]), np.array([1000.0]), soc_start_mwh=200.0)
    assert r["power_mw"][0] == pytest.approx(BAT["power_mw"])

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
    s = pd.read_parquet(SCHED, columns=["strategy", "dispatcher", "period_start", "revenue_gbp"])
    s = s.assign(k=s.strategy + "_" + s.dispatcher)
    m = s.groupby([s.period_start.dt.strftime("%Y-%m"), "k"]).revenue_gbp.sum().unstack()
    assert (m["perfect_lp"] >= m["forecast_rule"] - 1e-6).all(), m
    assert (m["perfect_lp"] >= m["reactive_rule"] - 1e-6).all(), m
    assert (m["forecast_rule"] >= m["do_nothing_rule"] - 1e-6).all(), m
