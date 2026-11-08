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
from dispatch import band, optimiser
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

def headline_window() -> tuple[pd.Timestamp, pd.Timestamp]:
    """The scorecard's headline period: the tuning plus hold-out months (unchanged by #50)."""
    months = sorted(TUNE_MONTHS + HOLDOUT_MONTHS)
    start = pd.Timestamp(f"{months[0]}-01T00:00Z")
    return start, pd.Timestamp(f"{months[-1]}-01T00:00Z") + pd.offsets.MonthBegin(1)


def replay_periods(fc_hours: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    """Half hours to replay: the forecast's whole days within [start, end)."""
    h = fc_hours[(fc_hours >= start) & (fc_hours < end)]
    return pd.date_range(h.min().normalize(), (h.max() + pd.Timedelta(hours=1)).normalize(),
                         freq="30min", inclusive="left")


def load_inputs(start: pd.Timestamp | None = None, end: pd.Timestamp | None = None):
    """Inputs for replaying [start, end) (default: the headline window)."""
    if start is None:
        start, end = headline_window()
    curt = pd.read_parquet(PROCESSED / "curtailment.parquet",
                           columns=["farm_id", "period_start", "curtailed_mwh_a", "bid_price_gbp_mwh"])
    res = pd.read_parquet(PROCESSED / "residual.parquet")
    fc = pd.read_parquet(PROCESSED / "forecast.parquet")
    fc = fc[(fc.split == "backtest") & (fc.issued_at == fc.hour_start.dt.normalize() - pd.Timedelta(hours=14))]
    fc = fc.sort_values("issued_at").drop_duplicates(["farm_id", "hour_start"], keep="last")
    periods = replay_periods(fc.hour_start, start, end)
    start, end = periods[0], periods[-1] + pd.Timedelta(minutes=30)
    fc = fc[(fc.hour_start >= start - pd.Timedelta(days=PRE_DAYS)) & (fc.hour_start < end)]
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
        fb = fc[fc.farm_id == b["farm_id"]].set_index("hour_start")

        def halves(col):   # hourly MWh -> each half hour gets half, NaN where missing
            if col not in fb:
                return np.full(len(hist), np.nan)
            f = fb[col]
            return (pd.concat([f, f.set_axis(f.index + pd.Timedelta(minutes=30))]).sort_index() / 2).reindex(hist).to_numpy()
        farm[b["battery_id"]] = {"waste": waste.to_numpy(), "fc": halves("curtailed_mwh_pred"),
                                 "fc_p90": halves("curtailed_mwh_p90"), "avoided_price": avoided_price.to_numpy()}
    return periods, hist, price, head, farm


# ---------- replay ----------

DISPATCHERS = {"rule": plan_day, "lp": optimiser.plan_day, band.NAME: band.plan_day}


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
                if dispatcher == band.NAME and strategy == "forecast":   # #52: plan towards the band's high end
                    sig = band.band_signal(f["fc"][sl], f.get("fc_p90", np.full(len(hist), np.nan))[sl],
                                           battery.get("band_weight", band.DEFAULT_WEIGHT))
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


def choose_band_weight(bats, hist, price, head, farm) -> dict:
    """#52: pick the band weight by forecast-battery revenue on TUNE_MONTHS only (LP planner)."""
    scores = {}
    for w in band.WEIGHT_GRID:
        scores[w] = round(sum(_revenue(replay(dict(b, band_weight=w), "forecast", hist, price, head[b["zone"]],
                                              farm[b["battery_id"]], band.NAME), TUNE_MONTHS) for b in bats), 0)
    best = max(scores, key=scores.get)
    log.info("band weight on %s: %s -> %s", "/".join(TUNE_MONTHS), scores, best)
    return {"chosen": best, "selected_on": list(TUNE_MONTHS), "forecast_revenue_gbp_by_weight": scores,
            "signal": "point + weight * (p90 - point)"}


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


def price_forecast_effect(bats, hist, price, head, farm, hold: dict, tot: pd.DataFrame,
                          sched: pd.DataFrame | None = None) -> dict | None:
    """#51 money test: capture ratio planning with the price forecast vs the 7-day mean, same
    batteries, same ceiling (perfect/lp, actual prices). Full period and hold-out months, each with a
    day-block bootstrap 95% interval on the difference (sched: the main replay, with `key`)."""
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
        ci = {}
        if sched is not None:
            from pipeline import bootstrap as bs
            day = lambda d: d.groupby(d.period_start.dt.normalize()).revenue_gbp.sum()  # noqa: E731
            pf, base, ceil = day(rev), day(sched[sched.key == key]), day(sched[sched.key == "perfect/lp"])
            in_hold = lambda s: s[s.index.strftime("%Y-%m").isin(HOLDOUT_MONTHS)]  # noqa: E731
            ci = {"capture_ratio_diff_ci95": [round(v, 4) for v in bs.capture_diff_ci95(pf, base, ceil)],
                  "holdout_capture_ratio_diff_ci95": [round(v, 4) for v in bs.capture_diff_ci95(
                      in_hold(pf), in_hold(base), in_hold(ceil))]}
        out[key] = {
            "capture_ratio_7day": round(float(tot.loc[key, "revenue_gbp"]) / ceiling, 4),
            "capture_ratio_price_forecast": round(full / ceiling, 4),
            "holdout_capture_ratio_7day": round(hold[key] / ceiling_hold, 4),
            "holdout_capture_ratio_price_forecast": round(held / ceiling_hold, 4),
            "revenue_gbp_7day": round(float(tot.loc[key, "revenue_gbp"]), 0),
            "revenue_gbp_price_forecast": round(full, 0),
            # difference = price forecast minus 7-day; an interval spanning 0 means no detectable effect
            **ci,
            # outside the price backtest months the plan falls back to the 7-day mean, so the full-period
            # figure mixes both; the hold-out figure is the price forecast on every day (checked above)
            "battery_days_with_price_forecast": int(len(used)),
            "battery_days_fallback_7day": int(len(fell)),
            "months_with_price_forecast": sorted(set(used.strftime("%Y-%m"))),
            # half hours missing from a day's price forecast, filled from that day's neighbours (pipeline.price.by_day)
            "price_forecast_days_with_filled_slots": len(lookup.filled),
            "price_forecast_slots_filled": int(sum(lookup.filled.values()))}
        log.info("price forecast, %s: capture %.3f (7-day %.3f); hold-out %.3f (7-day %.3f)", key,
                 out[key]["capture_ratio_price_forecast"], out[key]["capture_ratio_7day"],
                 out[key]["holdout_capture_ratio_price_forecast"], out[key]["holdout_capture_ratio_7day"])
    return out


def extended_replay(bats: list[dict], band_weight: float):
    """#50: the same batteries and settings (tuned on TUNE_MONTHS) replayed on the backtest months
    before the headline window, for the time machine. Returns (schedule rows, summary) or (None, None)."""
    fc = pd.read_parquet(PROCESSED / "forecast.parquet", columns=["hour_start", "split"])
    first = fc.loc[fc.split == "backtest", "hour_start"].min()
    start, end = headline_window()
    if pd.isna(first) or first >= start:
        return None, None
    periods, hist, price, head, farm = load_inputs(first.normalize(), start)
    frames = [replay(b, s, hist, price, head[b["zone"]], farm[b["battery_id"]]) for b in bats for s in STRATEGIES]
    frames += [replay(b, s, hist, price, head[b["zone"]], farm[b["battery_id"]], "lp")
               for b in bats for s in ("reactive", "forecast")]
    frames += [replay(dict(b, band_weight=band_weight), "forecast", hist, price, head[b["zone"]],
                      farm[b["battery_id"]], band.NAME) for b in bats]
    frames += [lp_rows(b, hist, price, head[b["zone"]], farm[b["battery_id"]]) for b in bats]
    sched = pd.concat(frames, ignore_index=True)
    rev = (sched.strategy + "/" + sched.dispatcher).to_frame("key").assign(r=sched.revenue_gbp).groupby("key").r.sum()
    summary = {"period": {"start": f"{periods[0]:%Y-%m-%dT%H:%M:%SZ}",
                          "end": f"{periods[-1] + pd.Timedelta(minutes=30):%Y-%m-%dT%H:%M:%SZ}"},
               "capture_ratio": capture_ratios(rev.to_dict()),
               "revenue_gbp": {k: round(float(v), 0) for k, v in rev.items()},
               "note": "same batteries and settings, tuned on " + "/".join(TUNE_MONTHS) + " (after this period); "
                       "for the time machine; the forecast is walk-forward (trained only on data before each month)"}
    log.info("extended replay %s: capture %s", summary["period"], summary["capture_ratio"])
    return sched, summary


def _holdout_gap_ci(sched: pd.DataFrame) -> dict:
    """Day-block bootstrap 95% interval of the hold-out capture-ratio gap, forecast minus reactive."""
    from pipeline import bootstrap as bs
    h = sched[sched.period_start.dt.strftime("%Y-%m").isin(HOLDOUT_MONTHS)]
    def day(k):
        g = h[h.key == k]
        return g.groupby(g.period_start.dt.normalize()).revenue_gbp.sum()
    return {d: [round(v, 4) for v in bs.capture_diff_ci95(day(f"forecast/{d}"), day(f"reactive/{d}"), day("perfect/lp"))]
            for d in ("lp", "rule")}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    periods, hist, price, head, farm = load_inputs()
    tuning = choose_fraction(hist, price, head, farm)
    bats = [dict(b, plan_waste_fraction=tuning["chosen"]) for b in BATTERIES]
    frames = [replay(b, s, hist, price, head[b["zone"]], farm[b["battery_id"]])
              for b in bats for s in STRATEGIES]
    frames += [replay(b, s, hist, price, head[b["zone"]], farm[b["battery_id"]], "lp")
               for b in bats for s in ("reactive", "forecast")]
    band_tuning = choose_band_weight(bats, hist, price, head, farm)
    frames += [replay(dict(b, band_weight=band_tuning["chosen"]), "forecast", hist, price, head[b["zone"]],
                      farm[b["battery_id"]], band.NAME) for b in bats]
    frames += [lp_rows(b, hist, price, head[b["zone"]], farm[b["battery_id"]]) for b in bats]
    sched = pd.concat(frames, ignore_index=True)
    ext_sched, ext_summary = extended_replay(bats, band_tuning["chosen"])
    path = PROCESSED / "schedules.parquet"
    backtest_end = periods[-1] + pd.Timedelta(minutes=30)
    all_sched = sched if ext_sched is None else pd.concat([ext_sched, sched], ignore_index=True)
    full = keep_live_rows(pd.read_parquet(path), all_sched, backtest_end) if path.exists() else all_sched
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
            f"forecast/{band.NAME}", "perfect/rule", "perfect/lp"]

    ratios = capture_ratios(tot.revenue_gbp.to_dict())
    hold = {k: _revenue(sched[sched.key == k], HOLDOUT_MONTHS) for k in keys}
    price_effect = price_forecast_effect(bats, hist, price, head, farm, hold, tot, sched)
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
                       # is the headline gap real? forecast minus reactive, 95% interval resampling whole days
                       "forecast_minus_reactive_ci95": _holdout_gap_ci(sched),
                       "revenue_gbp": {k: round(v, 0) for k, v in hold.items()}},
           **({"extended_backtest": ext_summary} if ext_summary else {}),
           "plan_waste_fraction": tuning,
           "band_weight": band_tuning}
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
