"""Tests for dispatch.live: the 30-minute re-planning loop (offline, injected inputs)."""
import json

import numpy as np
import pandas as pd

from dispatch import live
from dispatch import scorecard as sc

BAT = dict(sc.BATTERIES[0])
NOW = pd.Timestamp("2026-11-08T10:15Z")          # 20 complete, 10:00 in progress, 27 to re-plan


def inputs(waste_now=40.0, now=NOW):
    k = 20
    w = np.zeros(k)
    w[-3:] = waste_now                           # curtailed for the last 90 minutes
    return {BAT["battery_id"]: {"start": now.normalize(),
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
    x[BAT["battery_id"]] = {k: (v if k == "start" else np.r_[v, v[-1:]] if k.endswith("so_far") else v[1:])
                            for k, v in x[BAT["battery_id"]].items()}
    rows2, state2 = live.replan([BAT], x, rows1, state1, later)
    assert len(rows2) == 26 and (rows2.plan_issued_at == later).all()
    assert state2[BAT["battery_id"]]["soc_mwh"] >= state1[BAT["battery_id"]]["soc_mwh"] - 1e-9


def test_refresh_offline_keeps_the_last_plan(tmp_path):
    sp, st = tmp_path / "schedules.parquet", tmp_path / "state.json"
    rows, _ = live.replan([BAT], inputs(), pd.DataFrame(columns=sc.COLUMNS), {}, NOW)
    rows.to_parquet(sp, index=False)

    def offline(now, since=None):
        raise ConnectionError("no network")
    assert live.refresh(gather=offline, schedules_path=sp, state_path=st, now=NOW, batteries=[BAT]) is False
    assert len(pd.read_parquet(sp)) == 27 and not st.exists()


def test_refresh_appends_rows_and_saves_state(tmp_path):
    sp, st = tmp_path / "schedules.parquet", tmp_path / "state.json"
    pd.DataFrame(columns=sc.COLUMNS).astype({"period_start": "datetime64[ns, UTC]",
                                             "plan_issued_at": "datetime64[ns, UTC]"}).to_parquet(sp, index=False)
    assert live.refresh(gather=lambda now, since=None: inputs(), schedules_path=sp, state_path=st, now=NOW, batteries=[BAT])
    assert len(pd.read_parquet(sp)) == 27
    assert BAT["battery_id"] in json.loads(st.read_text())



# ---------- review #34 ----------

def test_last_half_hour_of_the_day_plans_nothing_and_does_not_crash():
    # Review #34: from 23:30 there is nothing left today; the LP must not see T = 0.
    late = pd.Timestamp("2026-11-08T23:45Z")
    k = 47
    x = {BAT["battery_id"]: {"start": late.normalize(), "waste_so_far": np.zeros(k), "price_so_far": np.full(k, 50.0),
                             "head_so_far": np.full(k, 100.0), "fc_rest": np.zeros(0), "price_rest": np.zeros(0),
                             "head_rest": np.zeros(0)}}
    rows, state = live.replan([BAT], x, pd.DataFrame(columns=sc.COLUMNS), {}, late)
    assert len(rows) == 0 and state[BAT["battery_id"]]["as_of"] == "2026-11-08T23:30:00Z"


def test_empty_day_plan_from_the_optimiser():
    from dispatch import optimiser
    out = optimiser.plan_day(BAT, np.zeros(0), np.zeros(0), np.zeros(0), 50.0)
    assert all(len(v) == 0 for v in out.values())


def test_replan_error_is_treated_like_offline(tmp_path):
    sp, st = tmp_path / "schedules.parquet", tmp_path / "state.json"
    rows, _ = live.replan([BAT], inputs(), pd.DataFrame(columns=sc.COLUMNS), {}, NOW)
    rows.to_parquet(sp, index=False)
    broken = lambda now, since=None: {BAT["battery_id"]: {}}  # noqa: E731 - missing every key
    assert live.refresh(gather=broken, schedules_path=sp, state_path=st, now=NOW, batteries=[BAT]) is False
    assert len(pd.read_parquet(sp)) == 27 and not st.exists()


def test_failed_schedule_write_keeps_old_files_and_state(tmp_path, monkeypatch):
    sp, st = tmp_path / "schedules.parquet", tmp_path / "state.json"
    rows, _ = live.replan([BAT], inputs(), pd.DataFrame(columns=sc.COLUMNS), {}, NOW)
    rows.to_parquet(sp, index=False)
    st.write_text('{"x": 1}')
    real = type(sp).replace

    def locked(self, target):
        if str(target) == str(sp):
            raise PermissionError("open in another process")
        return real(self, target)
    monkeypatch.setattr(type(sp), "replace", locked)
    later = NOW + pd.Timedelta(minutes=30)
    assert live.refresh(gather=lambda now, since=None: inputs(now=later), schedules_path=sp, state_path=st,
                        now=later, batteries=[BAT]) is False
    assert len(pd.read_parquet(sp)) == 27 and st.read_text() == '{"x": 1}'
    assert not list(tmp_path.glob("*.tmp"))


def test_first_run_after_midnight_executes_yesterdays_last_half_hours():
    # Review #34 point 6: the 23:00 plan's remaining slots must not be skipped at the day roll.
    now = pd.Timestamp("2026-11-09T00:45Z")
    start = pd.Timestamp("2026-11-08T23:00Z")                 # = the saved as_of
    w = np.array([0.0, 60.0, 0.0])                            # 23:00, 23:30, 00:00
    x = {BAT["battery_id"]: {"start": start, "waste_so_far": w, "price_so_far": np.full(3, 50.0),
                             "head_so_far": np.where(w > 0, 0.0, 100.0), "fc_rest": np.zeros(46),
                             "price_rest": np.full(46, 50.0), "head_rest": np.full(46, 100.0)}}
    state = {BAT["battery_id"]: {"soc_mwh": 0.0, "as_of": "2026-11-08T23:00:00Z"}}
    rows, new = live.replan([BAT], x, pd.DataFrame(columns=sc.COLUMNS), state, now)
    assert new[BAT["battery_id"]]["soc_mwh"] > 0               # the 23:30 waste was caught
    assert rows.period_start.min() == pd.Timestamp("2026-11-09T01:00Z")
