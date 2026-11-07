"""Live re-planning (#13): every 30 minutes, re-solve the rest of today from the current SoC.

    python -m dispatch.live              # one re-plan
    python -m dispatch.live --every 30   # judging day

For each battery (dispatch.scorecard.BATTERIES), with the LP dispatcher and our forecast:

1. What happened so far today: PN and acceptances (BOALF) for the farm's units since midnight,
   from Elexon's per-unit streams, turned into waste with Part 1's Method A, plus the market
   index price. (`/balancing/acceptances/all/latest` is not usable here: it ignores the bmUnit
   filter and returns only the latest ~100 acceptances GB-wide; see docs/samples.)
2. Current SoC: the newest plan for each elapsed half hour, executed against that actual waste
   (dispatch.scorecard.execute), starting from the SoC saved by the previous run.
3. The rest of today: our forecast from the newest weather run (saved models, pipeline.train
   live_rows for today), nowcast so the next hour expects at least the waste happening now;
   price = same half hour over the last 7 complete days; headroom = NESO day-ahead limit minus
   its flow, capped.
4. dispatch.optimiser.plan_day over the remaining half hours; the rows are appended to
   schedules.parquet with plan_issued_at = now (strategy "forecast", dispatcher "lp"). The server
   keeps the newest plan per half hour, and its badge says "live" with that time.

Each run also writes what was EXECUTED in the elapsed half hours to live_executed.parquet (same
columns as schedules.parquet; plan_issued_at = the plan that was executed), so the Autopilot can
show actual actions for the past and plans for the future. Plans cover a rolling 24 h.

Offline / any fetch failure: nothing is written; the last plan stays, and its plan_issued_at
shows how old it is (the dashboard labels it stale). The battery is virtual, so its "current"
SoC is what executing our own plans against real waste gives, saved in live_state.json. Live rows
are PLANS: their captured / revenue columns stay 0, and a "discharge" row can be sale intent at a
planned SoC of 0 (sold only if the battery has charge by then). Executed SoC lives in the state.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from dispatch import optimiser
from dispatch import scorecard as sc

log = logging.getLogger("live")
SCHEDULES = sc.PROCESSED / "schedules.parquet"
STATE = sc.PROCESSED / "live_state.json"
EXECUTED = sc.PROCESSED / "live_executed.parquet"   # what the live battery actually did
HORIZON = 48                 # re-plan the next 24 h (rolling), so slots after midnight have a plan
MAX_CATCHUP = pd.Timedelta(days=2)   # after a longer outage, don't back-fill further than this
MIN_PRICE_SLOTS = 40         # of 48 half hours of the day, needed for an expected-price curve
NOWCAST_PERIODS = 2          # the next hour expects at least the waste happening now


# ---------- pure, tested pieces ----------

def nowcast(fc_half: np.ndarray, observed_last_mwh: float, k: int = NOWCAST_PERIODS) -> np.ndarray:
    """Forecast for the remaining half hours, floored for the next k at the latest observed waste."""
    out = np.nan_to_num(np.asarray(fc_half, float)).copy()
    out[:k] = np.maximum(out[:k], max(float(observed_last_mwh), 0.0))
    return out


def newest_plan(sched: pd.DataFrame, battery_id: str, periods: pd.DatetimeIndex) -> pd.DataFrame:
    """For each period, the action/power of the newest live plan (empty if none)."""
    s = sched[(sched.battery_id == battery_id) & (sched.strategy == "forecast") & (sched.dispatcher == "lp")
              & sched.period_start.isin(periods)]
    s = s.sort_values("plan_issued_at").drop_duplicates("period_start", keep="last")
    return s.set_index("period_start").reindex(periods)


def advance(battery: dict, plan: pd.DataFrame, waste, price, head, soc_start: float):
    """Execute the newest plan over elapsed half hours against what really happened.

    Returns (soc at the end, executed rows in CONTRACT §5 columns, plan_issued_at = the plan used).
    """
    if len(plan) == 0:
        return soc_start, pd.DataFrame(columns=sc.COLUMNS)
    p = {"action": plan.action.fillna("hold").to_numpy(), "power_mw": plan.power_mw.fillna(0.0).to_numpy()}
    reason = plan.reason.fillna("no plan for this half hour").to_numpy()
    r = sc.execute(battery, p, waste, price, head, soc_start, reason)
    rows = pd.DataFrame({"period_start": plan.index, "plan_issued_at": plan.plan_issued_at.to_numpy(), **r})
    for k in ("battery_id", "farm_id", "zone"):
        rows[k] = battery[k]
    rows["strategy"], rows["dispatcher"] = "forecast", "lp"
    return float(r["soc_mwh"][-1]), rows[sc.COLUMNS]


def advance_soc(battery: dict, plan: pd.DataFrame, waste, price, head, soc_start: float) -> float:
    return advance(battery, plan, waste, price, head, soc_start)[0]


def expected_price_by_slot(price: pd.Series) -> pd.Series:
    """Mean price for each of the 48 half hours of the day (index "HH:MM"). A missing slot takes the
    mean of the others; fewer than MIN_PRICE_SLOTS slots with data raises (treated as offline)."""
    by = price.dropna().groupby(price.dropna().index.strftime("%H:%M")).mean()
    if len(by) < MIN_PRICE_SLOTS:
        raise RuntimeError(f"market prices for only {len(by)} of 48 half hours of the day")
    slots = pd.date_range("2000-01-01", periods=48, freq="30min").strftime("%H:%M")
    return by.reindex(slots).fillna(by.mean())


def plan_rows(battery: dict, now: pd.Timestamp, periods: pd.DatetimeIndex, soc_now: float,
              fc, price, head) -> pd.DataFrame:
    """Re-plan the remaining periods with the LP; CONTRACT §5 rows with plan_issued_at = now."""
    plan = optimiser.plan_day(battery, fc, price, head, soc_now)
    df = pd.DataFrame({"period_start": periods, "plan_issued_at": now, "action": plan["action"],
                       "power_mw": plan["power_mw"], "soc_mwh": plan["soc_mwh"],
                       "captured_curtailment_mwh": 0.0, "revenue_gbp": 0.0, "reason": plan["reason"]})
    for k in ("battery_id", "farm_id", "zone"):
        df[k] = battery[k]
    df["strategy"], df["dispatcher"] = "forecast", "lp"
    return df[sc.COLUMNS]


def replan(batteries, inputs: dict, sched: pd.DataFrame, state: dict, now: pd.Timestamp):
    """One re-plan for every battery. inputs[battery_id] = dict from gather_inputs.

    `*_so_far` arrays cover the complete half hours from inputs["start"] (the oldest saved
    `as_of`, possibly yesterday) to now; `*_rest` arrays cover the re-planned half hours.
    """
    # Complete half hours since the last run are executed (across midnight too); the one in
    # progress finishes under the previous plan; re-planning starts at the next half-hour boundary
    # (so plan_issued_at < period_start). After 23:30 there is nothing left today to re-plan.
    now_hh = now.floor("30min")
    today = now.normalize()
    start = now.ceil("30min") if now > now_hh else now_hh + pd.Timedelta(minutes=30)
    new_rows, new_state, executed = [], {}, []
    for b in batteries:
        x = inputs[b["battery_id"]]
        remaining = pd.date_range(start, periods=len(x["fc_rest"]), freq="30min")   # rolling horizon
        elapsed = pd.date_range(x["start"], now_hh, freq="30min", inclusive="left")
        st = state.get(b["battery_id"], {})
        since = pd.Timestamp(st["as_of"]) if st.get("as_of") else today
        soc = float(st.get("soc_mwh", 0.0))
        todo = elapsed[elapsed >= since]
        if len(todo):
            idx = elapsed.get_indexer(todo)
            soc, done = advance(b, newest_plan(sched, b["battery_id"], todo), x["waste_so_far"][idx],
                                x["price_so_far"][idx], x["head_so_far"][idx], soc)
            executed.append(done)
        last_obs = x["waste_so_far"][-1] if len(x["waste_so_far"]) else 0.0
        fc = nowcast(x["fc_rest"], last_obs)
        new_rows.append(plan_rows(b, now, remaining, soc, fc, x["price_rest"], x["head_rest"]))
        new_state[b["battery_id"]] = {"soc_mwh": round(soc, 4), "as_of": f"{now_hh:%Y-%m-%dT%H:%M:%SZ}"}
    ex = pd.concat(executed, ignore_index=True) if executed else pd.DataFrame(columns=sc.COLUMNS)
    return pd.concat(new_rows, ignore_index=True), new_state, ex


def _atomic_write(path: Path, write) -> None:
    tmp = path.with_suffix(".tmp")
    try:
        write(tmp)
        tmp.replace(path)                     # atomic, so readers never see half a file
    finally:
        tmp.unlink(missing_ok=True)


def refresh(gather: Callable[..., dict] | None = None, schedules_path: Path = SCHEDULES,
            state_path: Path = STATE, now: pd.Timestamp | None = None, batteries=None,
            executed_path: Path | None = None) -> bool:
    """One live re-plan. Returns True if new plans were appended, False if the last plan was kept.

    Never raises: a fetch or re-plan error, or a failed write (e.g. Windows refusing to replace a
    file another process has open), keeps the last plan and SoC; the next run tries again. The
    state file is only written after the schedule, and both are written atomically.
    """
    now = now or pd.Timestamp.now(tz="UTC").floor("min")
    batteries = batteries or sc.BATTERIES
    executed_path = executed_path or EXECUTED      # resolved at call time (tests redirect it)
    try:
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        since = min((pd.Timestamp(v["as_of"]) for v in state.values() if v.get("as_of")),
                    default=now.normalize())
        if since < now - MAX_CATCHUP:          # long outage: don't back-fill (or fetch) days of history
            log.warning("live re-plan: last run %s; catching up only from %s", since, now - MAX_CATCHUP)
            since = now - MAX_CATCHUP
        inputs = (gather or gather_inputs)(now, since)
        sched = pd.read_parquet(schedules_path)
        rows, new_state, done = replan(batteries, inputs, sched, state, now)
        old_done = pd.read_parquet(executed_path) if executed_path.exists() else pd.DataFrame(columns=sc.COLUMNS)
    except (OSError, RuntimeError) as e:
        log.warning("live re-plan: offline or API error (%s: %s); keeping the last plan", type(e).__name__, e)
        return False
    except Exception:  # noqa: BLE001 - a real bug: log it loudly, but keep the loop and the last plan
        log.exception("live re-plan: unexpected error; keeping the last plan")
        return False
    out = pd.concat([sched, rows], ignore_index=True)[sc.COLUMNS]
    try:
        _atomic_write(schedules_path, lambda t: out.to_parquet(t, index=False))
        if len(done):
            ex = (pd.concat([old_done, done], ignore_index=True)
                  .drop_duplicates(["battery_id", "period_start"], keep="last")[sc.COLUMNS])
            _atomic_write(executed_path, lambda t: ex.to_parquet(t, index=False))
        _atomic_write(state_path, lambda t: t.write_text(json.dumps(new_state, indent=1)))
    except OSError as e:
        log.warning("live re-plan: could not write (%s: %s); keeping the last plan, retry next time",
                    type(e).__name__, e)
        return False
    for b in batteries:
        r = rows[rows.battery_id == b["battery_id"]]
        log.info("%s: SoC %.0f MWh, %d half hours re-planned, next: %s", b["battery_id"],
                 new_state[b["battery_id"]]["soc_mwh"], len(r), r.reason.iloc[0] if len(r) else "-")
    return True


# ---------- network ----------

def gather_inputs(now: pd.Timestamp, since: pd.Timestamp | None = None) -> dict:
    """Everything replan needs, per battery. Raises on any network problem.

    `since` (the oldest saved as_of) may be yesterday: then yesterday's last half hours are fetched
    too, so the first run after midnight executes them. Elexon's streams return records that
    overlap the window, so acceptances issued before `start` that still apply are included.
    """
    from pipeline import fetch, live_forecast as lf, reconstruct as rc, residual as rs, train as tr
    today = now.normalize()
    now_hh = now.floor("30min")
    start = min(since or today, today).floor("30min")
    n_min = int((now_hh - start) / pd.Timedelta(minutes=1))
    farms = pd.read_csv(sc.ROOT / "reference" / "farms.csv")
    window = [("from", f"{start:%Y-%m-%dT%H:%MZ}"), ("to", f"{now:%Y-%m-%dT%H:%MZ}")]

    # forecast for today from the newest weather run (saved models)
    models, base = lf.load_models()
    curt = pd.read_parquet(sc.PROCESSED / "curtailment.parquet", columns=lf.CURT_COLS)
    fc = pd.concat([tr.live_rows(models, base, curt, now, day=d) for d in (today, today + pd.Timedelta(days=1))])
    fc = fc.set_index(["farm_id", "hour_start"]).curtailed_mwh_pred

    # prices: fetched live for the last 8 days (no dependence on how old residual.parquet is);
    # expected = same half hour over the 7 complete days before today
    week = {"from": f"{today - pd.Timedelta(days=8):%Y-%m-%dT%H:%MZ}", "to": f"{now:%Y-%m-%dT%H:%MZ}"}
    price = rs.market_price(fetch.records_to_frame(fetch.get_json(f"{fetch.BASE}/datasets/MID/stream", week)))
    hist = price[(price.index >= today - pd.Timedelta(days=7)) & (price.index < today)]
    exp_price = expected_price_by_slot(hist)

    # headroom: NESO day-ahead limit minus its (capped) flow
    fetch.fetch_neso()
    neso = rs.boundary_series(rs.parse_neso(pd.read_csv(fetch.RAW_ROOT / "neso" / "day_ahead_constraints.csv",
                                                        encoding="utf-8-sig")))
    rest_from = now.ceil("30min") if now > now_hh else now_hh + pd.Timedelta(minutes=30)
    periods = pd.date_range(start, rest_from + pd.Timedelta(minutes=30) * HORIZON, freq="30min", inclusive="left")
    nb = neso.reindex(periods).ffill().bfill()
    room4 = nb.b4_limit_mw - nb.b4_flow_mw.clip(upper=nb.b4_limit_mw)
    room6 = nb.b6_limit_mw - nb.b6_flow_mw.clip(upper=nb.b6_limit_mw)
    room = {"north_scotland": np.minimum(room4, room6).clip(lower=0).to_numpy(),
            "south_scotland": room6.clip(lower=0).to_numpy()}

    elapsed = periods[periods < now_hh]
    k = len(elapsed)
    k_rest = int(periods.searchsorted(rest_from))
    out = {}
    for b in sc.BATTERIES:
        bmus = farms.loc[farms.farm_id == b["farm_id"], "bmu_id"].tolist()
        units = [("bmUnit", u) for u in bmus]
        pn = fetch.records_to_frame(fetch.get_json(f"{fetch.BASE}/datasets/PN/stream", window + units))
        boa = fetch.records_to_frame(fetch.get_json(f"{fetch.BASE}/datasets/BOALF/stream", window + units))
        waste = np.zeros(k)
        for u in bmus:
            p_u = pn[pn.bmUnit == u] if len(pn) else pn
            a_u = boa[boa.bmUnit == u] if len(boa) else boa
            if len(p_u) and n_min:
                m = rc.method_a(p_u, a_u, start, n_min).set_index("period_start").curtailed_mwh_a
                waste += m.reindex(elapsed).fillna(0.0).to_numpy()
        f = fc.loc[b["farm_id"]] if b["farm_id"] in fc.index.get_level_values(0) else pd.Series(dtype=float)
        half = (pd.concat([f, f.set_axis(f.index + pd.Timedelta(minutes=30))]) / 2).reindex(periods).fillna(0.0)
        head = room[b["zone"]].copy()
        head[:k] = np.where(waste > 0, 0.0, head[:k])         # no room while it was being curtailed
        typical = pd.Series(exp_price.reindex(elapsed.strftime("%H:%M")).to_numpy(), index=elapsed)
        out[b["battery_id"]] = {
            "start": start,
            "waste_so_far": waste,
            "price_so_far": price.reindex(elapsed).fillna(typical).to_numpy(),
            "head_so_far": head[:k],
            "fc_rest": half.to_numpy()[k_rest:],
            "price_rest": exp_price.reindex(periods[k_rest:].strftime("%H:%M")).to_numpy(),
            "head_rest": head[k_rest:]}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=float, default=None, help="minutes between re-plans")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    while True:
        refresh()
        if not a.every:
            break
        time.sleep(a.every * 60)


if __name__ == "__main__":
    main()
