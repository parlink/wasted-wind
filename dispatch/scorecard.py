"""Part 3 scorecard: replay four battery strategies against what really happened.

    python -m dispatch.scorecard -> data/processed/schedules.parquet, data/processed/scorecard.json

Each battery sits behind one farm. Every day it is planned with `plan_day` (CONTRACT §6) from
what that strategy is allowed to know at D-1 10:00 UTC, then executed half hour by half hour
against reality:

  do_nothing  no battery dispatch                                              (zero line)
  reactive    plan knows no future waste; expected price = mean of the same half hour over
              D-8 .. D-2, expected headroom = same half hour on D-2 (all known at D-1 10:00)
  forecast    as reactive, plus our day-ahead waste forecast (issued D-1 10:00)
  perfect/rule  the same rules fed the actual waste, price and headroom ("rule-based with
              hindsight"; not a ceiling)
  reactive/lp, forecast/lp  the same plans made by the LP dispatcher (dispatch.optimiser.plan_day)
  perfect/lp  dispatch.optimiser.hindsight_bound: the best any battery could have done with
              perfect knowledge (a linear programme over the whole period); capture ratios use it

`plan_issued_at` = D-1 10:00: the plan's market inputs (prices, headroom, our forecast) are all
as known then. At midnight the plan is re-run with the
same inputs from the battery's actual state of charge (its own state, known to the operator),
the "plan, then re-plan from the current state" pattern in the brief.

The planning threshold PLAN_WASTE_FRACTION is chosen on May-Jul 2026 only; Aug-Oct is reported
separately as a hold-out (scorecard.json "holdout").

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

from dispatch import rule_based
from dispatch import optimiser
from dispatch.optimiser import hindsight_bound
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
PRE_DAYS = 8                       # price history before the first replayed day (D-8 .. D-2)
TUNE_MONTHS = ("2026-05", "2026-06", "2026-07")
HOLDOUT_MONTHS = ("2026-08", "2026-09", "2026-10")
FRACTION_GRID = [0.0, 0.05, 0.1, 0.25, 0.5]
COLUMNS = ["battery_id", "farm_id", "zone", "strategy", "dispatcher", "period_start", "plan_issued_at",
           "action", "power_mw", "soc_mwh", "captured_curtailment_mwh", "revenue_gbp", "reason"]


# ---------- tested pieces ----------

def expected_price(price: np.ndarray, day_index: int, days: int = 7) -> np.ndarray:
    """Mean price of each half hour over the `days` days ending at D-2 (D-1 is not complete at
    the D-1 10:00 issue time). NaN if there are none."""
    d = price.reshape(-1, 48)
    prev = d[max(0, day_index - 1 - days):max(0, day_index - 1)]
    return prev.mean(axis=0) if len(prev) else np.full(48, np.nan)


def capture_ratios(revenue: dict, ceiling_key: str = "perfect/lp") -> dict:
    """Revenue / ceiling revenue, keyed by "strategy/dispatcher". The ceiling is the
    perfect-hindsight LP, so perfect/rule is below 1 (CONTRACT §5 assumed perfect = 1.0)."""
    top = revenue[ceiling_key]
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
            mw = min(p, plan["power_mw"][i], soc / H, head[i])     # never above rated power
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
    fc = fc[(fc.split == "backtest") & (fc.issued_at == fc.hour_start.dt.normalize() - pd.Timedelta(hours=14))]
    fc = fc.sort_values("issued_at").drop_duplicates(["farm_id", "hour_start"], keep="last")
    start = fc.hour_start.min().normalize().replace(day=1)
    end = (fc.hour_start.max() + pd.Timedelta(hours=1)).normalize()
    periods = pd.date_range(start, end, freq="30min", inclusive="left")
    hist = pd.date_range(start - pd.Timedelta(days=PRE_DAYS), end, freq="30min", inclusive="left")

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

DISPATCHERS = {"rule": plan_day, "lp": optimiser.plan_day}


def replay(battery: dict, strategy: str, hist, price, head_zone, f, dispatcher: str = "rule",
           price_fc=None, stats: dict | None = None) -> pd.DataFrame:
    """price_fc: optional day -> 48 expected prices (pipeline.price.by_day); else the 7-day mean.
    stats: optional dict, filled with the planned days that used the price forecast ("forecast_days") and
    those that fell back to the 7-day mean ("fallback_days"), so a silent lookup failure can't pass as
    "no gain" (#57 review)."""
    days = len(hist) // 48 - PRE_DAYS          # the first PRE_DAYS days are history only
    soc, rows = 0.0, []
    for d in range(PRE_DAYS, PRE_DAYS + days):
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
                fc_p = price_fc(day_start) if price_fc is not None else None
                exp_p = fc_p if fc_p is not None else expected_price(price, d)
                if stats is not None and price_fc is not None:
                    stats.setdefault("forecast_days" if fc_p is not None else "fallback_days", []).append(day_start)
                exp_h = head_zone[(d - 2) * 48:(d - 1) * 48]    # same half hour on D-2
            plan = DISPATCHERS[dispatcher](battery, sig, exp_p, exp_h, soc)
            out = execute(battery, plan, actual_w, actual_p, actual_h, soc, plan["reason"])
            soc = float(out["soc_mwh"][-1])
        rows.append(pd.DataFrame({"period_start": hist[sl], "plan_issued_at": issued, **out}))
    df = pd.concat(rows, ignore_index=True)
    for k in ("battery_id", "farm_id", "zone"):
        df[k] = battery[k]
    df["strategy"], df["dispatcher"] = strategy, dispatcher
    return df[COLUMNS]


def keep_live_rows(existing: pd.DataFrame, new: pd.DataFrame, backtest_end: pd.Timestamp) -> pd.DataFrame:
    """New backtest rows plus any live re-plans (dispatch.live) beyond the backtest window."""
    live = existing[existing.period_start >= backtest_end]
    return pd.concat([new, live], ignore_index=True)[COLUMNS]


def lp_rows(battery: dict, hist, price, head_zone, f) -> pd.DataFrame:
    """perfect/lp rows: the hindsight optimum over the whole replay period."""
    sl = slice(PRE_DAYS * 48, None)
    out = hindsight_bound(battery, f["waste"][sl], price[sl], head_zone[sl], 0.0)
    df = pd.DataFrame({"period_start": hist[sl],
                       "plan_issued_at": hist[sl].normalize() - pd.Timedelta(hours=14), **out})
    for k in ("battery_id", "farm_id", "zone"):
        df[k] = battery[k]
    df["strategy"], df["dispatcher"] = "perfect", "lp"
    return df[COLUMNS]


def _revenue(df: pd.DataFrame, months) -> float:
    return float(df.loc[df.period_start.dt.strftime("%Y-%m").isin(months), "revenue_gbp"].sum())


def choose_fraction(hist, price, head, farm) -> dict:
    """Pick PLAN_WASTE_FRACTION by forecast-battery revenue on TUNE_MONTHS only."""
    scores = {}
    for frac in FRACTION_GRID:
        tot = 0.0
        for b in BATTERIES:
            r = replay(dict(b, plan_waste_fraction=frac), "forecast", hist, price, head[b["zone"]],
                       farm[b["battery_id"]])
            tot += _revenue(r, TUNE_MONTHS)
        scores[frac] = round(tot, 0)
    best = max(scores, key=scores.get)
    log.info("plan_waste_fraction on %s: %s -> %s", "/".join(TUNE_MONTHS), scores, best)
    return {"chosen": best, "selected_on": list(TUNE_MONTHS), "forecast_revenue_gbp_by_fraction": scores}


def price_forecast_effect(bats, hist, price, head, farm, hold: dict, tot: pd.DataFrame) -> dict | None:
    """#51 money test: capture ratio planning with the price forecast vs the 7-day mean, same
    batteries, same ceiling (perfect/lp, actual prices). Full period and hold-out months."""
    path = PROCESSED / "price_forecast.parquet"
    if not path.exists():
        return None
    from pipeline.price import by_day
    lookup = by_day(pd.read_parquet(path))
    ceiling, ceiling_hold = float(tot.loc["perfect/lp", "revenue_gbp"]), hold["perfect/lp"]
    out = {}
    for strat in ("forecast", "reactive"):
        key = f"{strat}/lp"
        stats: dict = {}
        rev = pd.concat([replay(b, strat, hist, price, head[b["zone"]], farm[b["battery_id"]], "lp", lookup, stats)
                         for b in bats], ignore_index=True)
        used = pd.DatetimeIndex(stats.get("forecast_days", []))
        fell = pd.DatetimeIndex(stats.get("fallback_days", []))
        held_fell = fell[fell.strftime("%Y-%m").isin(HOLDOUT_MONTHS)]
        if len(held_fell):   # the hold-out comparison must be the price forecast on every day, or it means nothing
            raise RuntimeError(f"price forecast missing on {len(held_fell)} battery-days in the hold-out months "
                               f"(first {held_fell.min():%Y-%m-%d}); check price_forecast.parquet covers {HOLDOUT_MONTHS}")
        full, held = float(rev.revenue_gbp.sum()), _revenue(rev, HOLDOUT_MONTHS)
        out[key] = {
            "capture_ratio_7day": round(float(tot.loc[key, "revenue_gbp"]) / ceiling, 4),
            "capture_ratio_price_forecast": round(full / ceiling, 4),
            "holdout_capture_ratio_7day": round(hold[key] / ceiling_hold, 4),
            "holdout_capture_ratio_price_forecast": round(held / ceiling_hold, 4),
            "revenue_gbp_7day": round(float(tot.loc[key, "revenue_gbp"]), 0),
            "revenue_gbp_price_forecast": round(full, 0),
            # outside the price backtest months the plan falls back to the 7-day mean, so the full-period
            # figure mixes both; the hold-out figure is the price forecast on every day (checked above)
            "battery_days_with_price_forecast": int(len(used)),
            "battery_days_fallback_7day": int(len(fell)),
            "months_with_price_forecast": sorted(set(used.strftime("%Y-%m")))}
        log.info("price forecast, %s: capture %.3f (7-day %.3f); hold-out %.3f (7-day %.3f)", key,
                 out[key]["capture_ratio_price_forecast"], out[key]["capture_ratio_7day"],
                 out[key]["holdout_capture_ratio_price_forecast"], out[key]["holdout_capture_ratio_7day"])
    return out


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    periods, hist, price, head, farm = load_inputs()
    tuning = choose_fraction(hist, price, head, farm)
    bats = [dict(b, plan_waste_fraction=tuning["chosen"]) for b in BATTERIES]
    frames = [replay(b, s, hist, price, head[b["zone"]], farm[b["battery_id"]])
              for b in bats for s in STRATEGIES]
    frames += [replay(b, s, hist, price, head[b["zone"]], farm[b["battery_id"]], "lp")
               for b in bats for s in ("reactive", "forecast")]
    frames += [lp_rows(b, hist, price, head[b["zone"]], farm[b["battery_id"]]) for b in bats]
    sched = pd.concat(frames, ignore_index=True)
    path = PROCESSED / "schedules.parquet"
    backtest_end = periods[-1] + pd.Timedelta(minutes=30)
    full = keep_live_rows(pd.read_parquet(path), sched, backtest_end) if path.exists() else sched
    full.to_parquet(path, index=False)

    sched["key"] = sched.strategy + "/" + sched.dispatcher
    avoided = {}
    for b in bats:
        ap = pd.Series(farm[b["battery_id"]]["avoided_price"], index=hist)
        sb = sched[sched.battery_id == b["battery_id"]]
        for key, g in sb.groupby("key"):
            avoided[key] = avoided.get(key, 0.0) + float((g.captured_curtailment_mwh
                                                          * g.period_start.map(ap).fillna(0)).sum())
    tot = sched.groupby("key")[["captured_curtailment_mwh", "revenue_gbp"]].sum()
    keys = ["do_nothing/rule", "reactive/rule", "forecast/rule", "reactive/lp", "forecast/lp",
            "perfect/rule", "perfect/lp"]

    ratios = capture_ratios(tot.revenue_gbp.to_dict())
    hold = {k: _revenue(sched[sched.key == k], HOLDOUT_MONTHS) for k in keys}
    price_effect = price_forecast_effect(bats, hist, price, head, farm, hold, tot)
    out = {"period": {"start": f"{periods[0]:%Y-%m-%dT%H:%M:%SZ}",
                      "end": f"{periods[-1] + pd.Timedelta(minutes=30):%Y-%m-%dT%H:%M:%SZ}"},
           "batteries": [{k: b[k] for k in ("battery_id", "farm_id", "zone", "power_mw", "energy_mwh",
                                            "efficiency")} for b in bats],
           "results": [{"strategy": k.split("/")[0], "dispatcher": k.split("/")[1],
                        "captured_curtailment_mwh": round(float(tot.loc[k, "captured_curtailment_mwh"]), 1),
                        "revenue_gbp": round(float(tot.loc[k, "revenue_gbp"]), 0),
                        "payments_avoided_gbp": round(avoided.get(k, 0.0), 0),
                        "capture_ratio": ratios[k]} for k in keys],
           "capture_ratio_basis": "revenue / perfect-hindsight LP revenue (dispatch.optimiser)",
           "caveats": ["each battery assumes all of its boundary headroom is its own, so fleet £ totals can "
                       "exceed what the boundary could carry; ratios are comparable, £ sums are optimistic",
                       "the whole-period LP carries SoC across months, so its Aug-Oct slice is a near-bound, "
                       "not a strict bound, for that window"],
           **({"price_forecast_effect": price_effect} if price_effect else {}),
           "holdout": {"months": list(HOLDOUT_MONTHS),
                       "capture_ratio": capture_ratios(hold),
                       "revenue_gbp": {k: round(v, 0) for k, v in hold.items()}},
           "plan_waste_fraction": tuning}
    (PROCESSED / "scorecard.json").write_text(json.dumps(out, indent=2))
    log.info("wrote schedules.parquet (%d rows) and scorecard.json", len(sched))
    for r in out["results"]:
        log.info("%-10s %-4s captured %9.0f MWh  revenue £%10.0f  avoided £%9.0f  capture %.3f",
                 r["strategy"], r["dispatcher"], r["captured_curtailment_mwh"], r["revenue_gbp"],
                 r["payments_avoided_gbp"], r["capture_ratio"])
    log.info("hold-out %s capture: %s", "/".join(HOLDOUT_MONTHS), out["holdout"]["capture_ratio"])
    by_month = sched.groupby([sched.period_start.dt.strftime("%Y-%m"), "key"]).revenue_gbp.sum().unstack()
    log.info("revenue by month (£):\n%s", by_month[keys].round(0).to_string())


if __name__ == "__main__":
    main()
