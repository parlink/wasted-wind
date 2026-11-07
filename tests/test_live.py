"""Tests for dispatch.live: the 30-minute re-planning loop (offline, injected inputs)."""
import json

import numpy as np
import pandas as pd

from dispatch import live
from dispatch import scorecard as sc

BAT = dict(sc.BATTERIES[0])
NOW = pd.Timestamp("2026-11-08T10:15Z")          # 20 complete, 10:00 in progress, 27 to re-plan


def inputs(waste_now=40.0):
    k = 20
    w = np.zeros(k)
    w[-3:] = waste_now                           # curtailed for the last 90 minutes
    return {BAT["battery_id"]: {
        "waste_so_far": w, "price_so_far": np.full(k, 60.0), "head_so_far": np.where(w > 0, 0.0, 300.0),
        "fc_rest": np.zeros(27), "price_rest": np.r_[np.full(15, 40.0), np.full(12, 140.0)],
        "head_rest": np.full(27, 300.0)}}


def test_nowcast_floors_the_next_hour_at_the_waste_happening_now():
    out = live.nowcast(np.array([0.0, 5.0, 0.0, 0.0]), observed_last_mwh=30.0)
    assert out.tolist() == [30.0, 30.0, 0.0, 0.0]


def test_replan_tracks_soc_and_plans_only_the_rest_of_today():
    sched = pd.DataFrame(columns=sc.COLUMNS)
    rows, state = live.replan([BAT], inputs(), sched, {}, NOW)
    assert len(rows) == 27 and list(rows.columns) == sc.COLUMNS
    assert (rows.period_start > NOW).all() and (rows.plan_issued_at == NOW).all()
    assert set(rows.dispatcher) == {"lp"} and set(rows.strategy) == {"forecast"}
    # it was being curtailed, so the live battery charged: SoC > 0, and the plan sells in the peak
    assert state[BAT["battery_id"]]["soc_mwh"] > 0
    assert (rows.action.iloc[-12:] == "discharge").any()
    assert state[BAT["battery_id"]]["as_of"] == "2026-11-08T10:00:00Z"


def test_second_run_continues_from_saved_state_and_newest_plan():
    sched = pd.DataFrame(columns=sc.COLUMNS)
    rows1, state1 = live.replan([BAT], inputs(), sched, {}, NOW)
    later = NOW + pd.Timedelta(minutes=30)
    x = inputs()
    x[BAT["battery_id"]] = {k: (np.r_[v, v[-1:]] if k.endswith("so_far") else v[1:]) for k, v in x[BAT["battery_id"]].items()}
    rows2, state2 = live.replan([BAT], x, rows1, state1, later)
    assert len(rows2) == 26 and (rows2.plan_issued_at == later).all()
    assert state2[BAT["battery_id"]]["soc_mwh"] >= state1[BAT["battery_id"]]["soc_mwh"] - 1e-9


def test_refresh_offline_keeps_the_last_plan(tmp_path):
    sp, st = tmp_path / "schedules.parquet", tmp_path / "state.json"
    rows, _ = live.replan([BAT], inputs(), pd.DataFrame(columns=sc.COLUMNS), {}, NOW)
    rows.to_parquet(sp, index=False)

    def offline(now):
        raise ConnectionError("no network")
    assert live.refresh(gather=offline, schedules_path=sp, state_path=st, now=NOW, batteries=[BAT]) is False
    assert len(pd.read_parquet(sp)) == 27 and not st.exists()


def test_refresh_appends_rows_and_saves_state(tmp_path):
    sp, st = tmp_path / "schedules.parquet", tmp_path / "state.json"
    pd.DataFrame(columns=sc.COLUMNS).astype({"period_start": "datetime64[ns, UTC]",
                                             "plan_issued_at": "datetime64[ns, UTC]"}).to_parquet(sp, index=False)
    assert live.refresh(gather=lambda now: inputs(), schedules_path=sp, state_path=st, now=NOW, batteries=[BAT])
    assert len(pd.read_parquet(sp)) == 27
    assert BAT["battery_id"] in json.loads(st.read_text())
