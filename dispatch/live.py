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

Offline / any fetch failure: nothing is written; the last plan stays, and its plan_issued_at
shows how old it is (the dashboard labels it stale). The battery is virtual, so its "current"
SoC is what executing our own plans against real waste gives; captured / revenue columns of
future (planned) rows are 0 until those half hours have happened.
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


def advance_soc(battery: dict, plan: pd.DataFrame, waste, price, head, soc_start: float) -> float:
    """Execute the newest plan over elapsed half hours against what really happened."""
    if len(plan) == 0:
        return soc_start
    p = {"action": plan.action.fillna("hold").to_numpy(), "power_mw": plan.power_mw.fillna(0.0).to_numpy()}
    r = sc.execute(battery, p, waste, price, head, soc_start)
    return float(r["soc_mwh"][-1])


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
    """One re-plan for every battery. inputs[battery_id] = dict of arrays (see gather_inputs)."""
    # Complete half hours so far are executed; the one in progress finishes under the previous
    # plan; re-planning starts at the next half-hour boundary (so plan_issued_at < period_start).
    now_hh = now.floor("30min")
    today = now.normalize()
    elapsed = pd.date_range(today, now_hh, freq="30min", inclusive="left")
    start = now.ceil("30min") if now > now_hh else now_hh + pd.Timedelta(minutes=30)
    remaining = pd.date_range(start, today + pd.Timedelta(days=1), freq="30min", inclusive="left")
    new_rows, new_state = [], {}
    for b in batteries:
        x = inputs[b["battery_id"]]
        st = state.get(b["battery_id"], {})
        since = pd.Timestamp(st["as_of"]) if st.get("as_of") else today
        soc = float(st.get("soc_mwh", 0.0))
        todo = elapsed[elapsed >= since]
        if len(todo):
            idx = elapsed.get_indexer(todo)
            soc = advance_soc(b, newest_plan(sched, b["battery_id"], todo), x["waste_so_far"][idx],
                              x["price_so_far"][idx], x["head_so_far"][idx], soc)
        last_obs = x["waste_so_far"][-1] if len(x["waste_so_far"]) else 0.0
        fc = nowcast(x["fc_rest"], last_obs)
        new_rows.append(plan_rows(b, now, remaining, soc, fc, x["price_rest"], x["head_rest"]))
        new_state[b["battery_id"]] = {"soc_mwh": round(soc, 4), "as_of": f"{now_hh:%Y-%m-%dT%H:%M:%SZ}"}
    return pd.concat(new_rows, ignore_index=True), new_state


def refresh(gather: Callable[[pd.Timestamp], dict] | None = None, schedules_path: Path = SCHEDULES,
            state_path: Path = STATE, now: pd.Timestamp | None = None, batteries=None) -> bool:
    """One live re-plan. Returns True if new plans were appended, False if the last plan was kept."""
    now = now or pd.Timestamp.now(tz="UTC").floor("min")
    batteries = batteries or sc.BATTERIES
    try:
        inputs = (gather or gather_inputs)(now)
    except Exception as e:  # noqa: BLE001 - offline / API trouble: keep the last plan
        log.warning("live re-plan failed (%s); keeping the last plan", e)
        return False
    sched = pd.read_parquet(schedules_path)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    rows, new_state = replan(batteries, inputs, sched, state, now)
    out = pd.concat([sched, rows], ignore_index=True)[sc.COLUMNS]
    tmp = schedules_path.with_suffix(".tmp")
    out.to_parquet(tmp, index=False)
    tmp.replace(schedules_path)
    state_path.write_text(json.dumps(new_state, indent=1))
    for b in batteries:
        r = rows[rows.battery_id == b["battery_id"]]
        log.info("%s: SoC %.0f MWh, %d half hours re-planned, next: %s", b["battery_id"],
                 new_state[b["battery_id"]]["soc_mwh"], len(r), r.reason.iloc[0] if len(r) else "-")
    return True


# ---------- network ----------

def gather_inputs(now: pd.Timestamp) -> dict:
    """Everything replan needs, per battery, for today. Raises on any network problem."""
    from pipeline import fetch, live_forecast as lf, reconstruct as rc, residual as rs, train as tr
    today = now.normalize()
    now_hh = now.floor("30min")
    n_min = int((now_hh - today) / pd.Timedelta(minutes=1))
    farms = pd.read_csv(sc.ROOT / "reference" / "farms.csv")
    window = [("from", f"{today:%Y-%m-%dT%H:%MZ}"), ("to", f"{now:%Y-%m-%dT%H:%MZ}")]

    # 3. forecast for today from the newest weather run (saved models)
    models, base = lf.load_models()
    curt = pd.read_parquet(sc.PROCESSED / "curtailment.parquet", columns=lf.CURT_COLS)
    fc = tr.live_rows(models, base, curt, now, day=today).set_index(["farm_id", "hour_start"]).curtailed_mwh_pred

    # prices: today so far (MID), and the same half hour over the last 7 complete days
    mid = fetch.records_to_frame(fetch.get_json(f"{fetch.BASE}/datasets/MID/stream", dict(window)))
    price_now = rs.market_price(mid) if len(mid) else pd.Series(dtype=float)
    res = pd.read_parquet(sc.PROCESSED / "residual.parquet", columns=["zone", "period_start",
                                                                         "market_price_gbp_mwh"])
    hist = res[res.zone == "north_scotland"].set_index("period_start").market_price_gbp_mwh
    hist = hist[hist.index < today].iloc[-7 * 48:]
    exp_price = hist.groupby(hist.index.strftime("%H:%M")).mean()

    # headroom: NESO day-ahead limit minus its (capped) flow, for today
    fetch.fetch_neso()
    neso = rs.boundary_series(rs.parse_neso(pd.read_csv(fetch.RAW_ROOT / "neso" / "day_ahead_constraints.csv",
                                                        encoding="utf-8-sig")))
    periods = pd.date_range(today, periods=48, freq="30min")
    nb = neso.reindex(periods).ffill().bfill()
    room4 = nb.b4_limit_mw - nb.b4_flow_mw.clip(upper=nb.b4_limit_mw)
    room6 = nb.b6_limit_mw - nb.b6_flow_mw.clip(upper=nb.b6_limit_mw)
    room = {"north_scotland": np.minimum(room4, room6).clip(lower=0).to_numpy(),
            "south_scotland": room6.clip(lower=0).to_numpy()}

    elapsed = periods[periods < now_hh]
    k = len(elapsed)
    start = now.ceil("30min") if now > now_hh else now_hh + pd.Timedelta(minutes=30)
    k_rest = int((start - today) / pd.Timedelta(minutes=30))       # first re-planned half hour
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
                m = rc.method_a(p_u, a_u, today, n_min).set_index("period_start").curtailed_mwh_a
                waste += m.reindex(elapsed).fillna(0.0).to_numpy()
        f = fc.loc[b["farm_id"]] if b["farm_id"] in fc.index.get_level_values(0) else pd.Series(dtype=float)
        half = (pd.concat([f, f.set_axis(f.index + pd.Timedelta(minutes=30))]) / 2).reindex(periods).fillna(0.0)
        head = room[b["zone"]].copy()
        head[:k] = np.where(waste > 0, 0.0, head[:k])         # no room while it was being curtailed
        typical = pd.Series(exp_price.reindex(elapsed.strftime("%H:%M")).to_numpy(), index=elapsed)
        out[b["battery_id"]] = {
            "waste_so_far": waste,
            "price_so_far": price_now.reindex(elapsed).fillna(typical).to_numpy(),
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
