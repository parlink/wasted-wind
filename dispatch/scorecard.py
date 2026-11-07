"""Part 3 scorecard: replay four battery strategies against what really happened.

    python -m dispatch.scorecard -> data/processed/schedules.parquet, data/processed/scorecard.json

Each battery sits behind one farm. Every day it is planned with `plan_day` (CONTRACT §6) from
what that strategy is allowed to know at D-1 10:00 UTC, then executed half hour by half hour
against reality:

  do_nothing  no battery dispatch                                       (zero line)
  reactive    plan knows no future waste; expected price = mean of the same half hour over
              the previous 7 days; expected headroom = same half hour yesterday
  forecast    as reactive, plus our day-ahead waste forecast (forecast.parquet, backtest split)
  perfect     plan sees the actual waste, price and headroom              (hindsight ceiling)

Execution is the same for every strategy: charge whenever the farm is actually being curtailed
(a battery reacts live), and only from that waste; sell when the plan says so, capped by the
real boundary headroom and only at a positive real price. So a forecast changes the plan, i.e.
whether the battery is empty before a storm, not what it is physically able to do.

Headroom (MW) comes from residual.parquet: flow_B4 = residual_N + wind_N,
flow_B6 = residual_S + wind_S + flow_B4; a north battery needs room on both boundaries.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from dispatch.rule_based import H, plan_day

ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "data" / "processed"
log = logging.getLogger("scorecard")

BATTERIES = [
    {"battery_id": "seagreen_bess", "farm_id": "seagreen", "zone": "north_scotland",
     "power_mw": 100.0, "energy_mwh": 200.0, "efficiency": 0.88},
    {"battery_id": "moray_east_bess", "farm_id": "moray_east", "zone": "north_scotland",
     "power_mw": 100.0, "energy_mwh": 200.0, "efficiency": 0.88},
    {"battery_id": "viking_bess", "farm_id": "viking", "zone": "north_scotland",
     "power_mw": 50.0, "energy_mwh": 100.0, "efficiency": 0.88},
    {"battery_id": "south_kyle_bess", "farm_id": "south_kyle", "zone": "south_scotland",
     "power_mw": 50.0, "energy_mwh": 100.0, "efficiency": 0.88},
]
STRATEGIES = ["do_nothing", "reactive", "forecast", "perfect"]
COLUMNS = ["battery_id", "farm_id", "zone", "strategy", "dispatcher", "period_start", "plan_issued_at",
           "action", "power_mw", "soc_mwh", "captured_curtailment_mwh", "revenue_gbp", "reason"]


# ---------- tested pieces ----------

def expected_price(price: np.ndarray, day_index: int, days: int = 7) -> np.ndarray:
    """Mean price of each half hour over the previous `days` days (NaN if there are none)."""
    d = price.reshape(-1, 48)
    prev = d[max(0, day_index - days):day_index]
    return prev.mean(axis=0) if len(prev) else np.full(48, np.nan)


def capture_ratios(revenue: dict) -> dict:
    """Strategy revenue / perfect-hindsight revenue (CONTRACT §5)."""
    top = revenue["perfect"]
    return {k: (round(v / top, 4) if top else 0.0) for k, v in revenue.items()}


def execute(battery: dict, plan: dict, actual_waste_mwh, price, headroom_mw, soc_start_mwh: float,
            plan_reason=None) -> dict:
    """Run a day's plan against reality. Returns per-half-hour arrays (CONTRACT §5 columns)."""
    p, e, eff = battery["power_mw"], battery["energy_mwh"], battery["efficiency"]
    w = np.nan_to_num(np.asarray(actual_waste_mwh, float))
    price, head = np.asarray(price, float), np.maximum(np.nan_to_num(np.asarray(headroom_mw, float)), 0)
    n = len(w)
    act = np.full(n, "hold", dtype=object)
    mw_out, soc_out, cap, rev = np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n)
    why = np.full(n, "", dtype=object)
    plan_reason = plan_reason if plan_reason is not None else np.full(n, "", dtype=object)
    soc = soc_start_mwh
    for i in range(n):
        planned = plan["action"][i]
        if w[i] > 0 and soc < e - 1e-9:
            mw = min(p, (e - soc) / (H * eff), w[i] / H)
            soc += mw * H * eff
            act[i], mw_out[i], cap[i] = "charge", mw, mw * H
            why[i] = plan_reason[i] if planned == "charge" else \
                f"charging: {w[i]:.0f} MWh being curtailed now (not in the plan)"
        elif planned == "discharge" and soc > 1e-9 and head[i] > 0 and price[i] > 0:
            mw = min(plan["power_mw"][i], soc / H, head[i])
            soc -= mw * H
            act[i], mw_out[i], rev[i] = "discharge", mw, mw * H * price[i]
            why[i] = plan_reason[i]
        else:
            if planned == "discharge":
                why[i] = "holding: planned sale not possible (no room, no charge or price <= 0)"
            elif planned == "charge":
                why[i] = "holding: forecast waste did not happen" if w[i] <= 0 else "holding: full"
            else:
                why[i] = plan_reason[i] or "holding"
        soc_out[i] = soc
    return {"action": act.astype(str), "power_mw": mw_out, "soc_mwh": soc_out,
            "captured_curtailment_mwh": cap, "revenue_gbp": rev, "reason": why.astype(str)}


# ---------- inputs ----------

def load_inputs():
    curt = pd.read_parquet(PROCESSED / "curtailment.parquet",
                           columns=["farm_id", "period_start", "curtailed_mwh_a", "bid_price_gbp_mwh"])
    res = pd.read_parquet(PROCESSED / "residual.parquet")
    fc = pd.read_parquet(PROCESSED / "forecast.parquet")
    fc = fc[fc.split == "backtest"]
    start = fc.hour_start.min().normalize().replace(day=1)
    end = (fc.hour_start.max() + pd.Timedelta(hours=1)).normalize()
    periods = pd.date_range(start, end, freq="30min", inclusive="left")
    hist = pd.date_range(start - pd.Timedelta(days=7), end, freq="30min", inclusive="left")

    r = {z: g.set_index("period_start") for z, g in res.groupby("zone")}
    n, s = r["north_scotland"].reindex(hist), r["south_scotland"].reindex(hist)
    flow4 = n.residual_mw + n.wind_delivered_mwh / H
    flow6 = s.residual_mw + s.wind_delivered_mwh / H + flow4
    head6 = (s.boundary_limit_mw - flow6).clip(lower=0)
    head = {"south_scotland": head6.fillna(0).to_numpy(),
            "north_scotland": np.minimum((n.boundary_limit_mw - flow4).clip(lower=0), head6).fillna(0).to_numpy()}
    price = n.market_price_gbp_mwh.to_numpy()

    farm = {}
    for b in BATTERIES:
        c = curt[curt.farm_id == b["farm_id"]]
        g = c.groupby("period_start")
        waste = g.curtailed_mwh_a.sum().reindex(hist).fillna(0.0)
        paid = (-(c.bid_price_gbp_mwh * c.curtailed_mwh_a)).groupby(c.period_start).sum()
        avoided_price = (paid / g.curtailed_mwh_a.sum()).reindex(hist)       # GBP per MWh not wasted
        f = fc[fc.farm_id == b["farm_id"]].set_index("hour_start").curtailed_mwh_pred
        half = pd.concat([f, f.set_axis(f.index + pd.Timedelta(minutes=30))]).sort_index() / 2
        farm[b["battery_id"]] = {"waste": waste.to_numpy(), "fc": half.reindex(hist).to_numpy(),
                                 "avoided_price": avoided_price.to_numpy()}
    return periods, hist, price, head, farm


# ---------- replay ----------

def replay(battery: dict, strategy: str, hist, price, head_zone, f) -> pd.DataFrame:
    days = len(hist) // 48 - 7                 # the first 7 days are price history only
    soc, rows = 0.0, []
    for d in range(7, 7 + days):
        sl = slice(d * 48, d * 48 + 48)
        actual_w, actual_p, actual_h = f["waste"][sl], price[sl], head_zone[sl]
        day_start = hist[d * 48]
        issued = day_start - pd.Timedelta(hours=14)
        if strategy == "do_nothing":
            out = {"action": np.full(48, "hold"), "power_mw": np.zeros(48), "soc_mwh": np.zeros(48),
                   "captured_curtailment_mwh": np.zeros(48), "revenue_gbp": np.zeros(48),
                   "reason": np.full(48, "do nothing: no battery")}
        else:
            if strategy == "perfect":
                sig, exp_p, exp_h = actual_w, actual_p, actual_h
            else:
                sig = np.zeros(48) if strategy == "reactive" else np.nan_to_num(f["fc"][sl])
                exp_p = expected_price(price, d)
                exp_h = head_zone[(d - 1) * 48:d * 48]          # same half hour yesterday
            plan = plan_day(battery, sig, exp_p, exp_h, soc)
            out = execute(battery, plan, actual_w, actual_p, actual_h, soc, plan["reason"])
            soc = float(out["soc_mwh"][-1])
        rows.append(pd.DataFrame({"period_start": hist[sl], "plan_issued_at": issued, **out}))
    df = pd.concat(rows, ignore_index=True)
    for k in ("battery_id", "farm_id", "zone"):
        df[k] = battery[k]
    df["strategy"], df["dispatcher"] = strategy, "rule"
    return df[COLUMNS]


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    periods, hist, price, head, farm = load_inputs()
    frames = [replay(b, s, hist, price, head[b["zone"]], farm[b["battery_id"]])
              for b in BATTERIES for s in STRATEGIES]
    sched = pd.concat(frames, ignore_index=True)
    sched.to_parquet(PROCESSED / "schedules.parquet", index=False)

    avoided = {}
    for b in BATTERIES:
        ap = pd.Series(farm[b["battery_id"]]["avoided_price"], index=hist)
        s = sched[sched.battery_id == b["battery_id"]]
        for strat, g in s.groupby("strategy"):
            v = (g.captured_curtailment_mwh * g.period_start.map(ap).fillna(0)).sum()
            avoided[strat] = avoided.get(strat, 0.0) + float(v)
    tot = sched.groupby("strategy")[["captured_curtailment_mwh", "revenue_gbp"]].sum()
    ratios = capture_ratios(tot.revenue_gbp.to_dict())
    out = {"period": {"start": f"{periods[0]:%Y-%m-%dT%H:%M:%SZ}",
                      "end": f"{periods[-1] + pd.Timedelta(minutes=30):%Y-%m-%dT%H:%M:%SZ}"},
           "batteries": [{k: b[k] for k in ("battery_id", "farm_id", "zone", "power_mw", "energy_mwh",
                                            "efficiency")} for b in BATTERIES],
           "results": [{"strategy": s, "dispatcher": "rule",
                        "captured_curtailment_mwh": round(float(tot.loc[s, "captured_curtailment_mwh"]), 1),
                        "revenue_gbp": round(float(tot.loc[s, "revenue_gbp"]), 0),
                        "payments_avoided_gbp": round(avoided.get(s, 0.0), 0),
                        "capture_ratio": ratios[s]} for s in STRATEGIES]}
    (PROCESSED / "scorecard.json").write_text(json.dumps(out, indent=2))
    log.info("wrote schedules.parquet (%d rows) and scorecard.json", len(sched))
    for r in out["results"]:
        log.info("%-10s captured %9.0f MWh  revenue £%10.0f  avoided £%10.0f  capture %.2f",
                 r["strategy"], r["captured_curtailment_mwh"], r["revenue_gbp"],
                 r["payments_avoided_gbp"], r["capture_ratio"])
    by_month = sched.groupby([sched.period_start.dt.strftime("%Y-%m"), "strategy"]).revenue_gbp.sum().unstack()
    log.info("revenue by month (£):\n%s", by_month[STRATEGIES].round(0).to_string())


if __name__ == "__main__":
    main()
