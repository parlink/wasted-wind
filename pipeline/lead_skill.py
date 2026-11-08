"""#49 small version: how the waste forecast's skill falls with days ahead (leads 1-6).

    python -m pipeline.lead_skill   -> data/processed/forecast_skill_by_lead.json

For each lead k the panel is rebuilt with every signal cut off at D-k 10:00 (features.py,
"Leads"), the model is fitted walk-forward on the same test months as pipeline.train (training
stops at day 1-k of each month), and every lead is scored on the same farm-hours, so the only
thing that changes down the table is how far ahead the forecast was made.

Lead 2+ has no NESO limits (published the day before), so lead 1 is also reported without them:
that row separates "lost the limits" from "the weather is further away". Climatology doesn't
depend on the lead and is the floor any forecast should beat.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

from pipeline import features as ft
from pipeline import train as tr

log = logging.getLogger("lead_skill")
OUT = ft.PROCESSED / "forecast_skill_by_lead.json"
KEY = ["farm_id", "hour_start"]


def common_rows(by_lead: dict[int, pd.DataFrame]) -> dict[int, pd.DataFrame]:
    """Keep only farm-hours every lead has, so leads are compared like for like."""
    keys = None
    for d in by_lead.values():
        k = pd.MultiIndex.from_frame(d[KEY])
        keys = k if keys is None else keys.intersection(k)
    out = {}
    for lead, d in by_lead.items():
        d = d[pd.MultiIndex.from_frame(d[KEY]).isin(keys)]
        out[lead] = d.sort_values(KEY).reset_index(drop=True)
    return out


def backtest_lead(panel: pd.DataFrame, months: list[pd.Timestamp], lead: int, no_limits: bool = False) -> pd.DataFrame:
    """Walk-forward predictions for one lead: point model plus the three baselines."""
    feats = tr.NO_LIMIT_FEATURES if no_limits else tr.lead_features(lead)
    rows = []
    for m in months:
        nxt = m + pd.offsets.MonthBegin(1)
        train = panel[tr.train_mask(panel.hour_start, m, lead)]
        test = panel[(panel.hour_start >= m) & (panel.hour_start < nxt)].copy()
        test["pred"] = np.clip(tr._fit(train, feats).predict(test[feats]), 0, None)
        test = tr.apply_baselines(test, tr.fit_baselines(train))
        rows.append(test[KEY + ["curtailed_mwh", "pred", "baseline_persistence_mwh",
                                "baseline_climatology_mwh", "baseline_wind_rule_mwh"]])
        log.info("lead %d%s %s: train %d, test %d", lead, " (no limits)" if no_limits else "",
                 f"{m:%Y-%m}", len(train), len(test))
    return pd.concat(rows, ignore_index=True)


def _scores(d: pd.DataFrame) -> dict:
    cols = {"lightgbm": "pred", "persistence": "baseline_persistence_mwh",
            "climatology": "baseline_climatology_mwh", "wind_rule": "baseline_wind_rule_mwh"}
    return {name: {k: round(v, 3) for k, v in tr.score(d.curtailed_mwh, d[c], d.hour_start).items()}
            for name, c in cols.items()}


def chart_rows(table: list[dict], n: int) -> list[dict]:
    """One flat row per lead for the Forecast tab (the model as it would run: lead 1 with limits).
    baseline_mae_mwh = climatology (the same at every lead)."""
    return [{"lead_days": r["lead_days"], "mae_mwh": r["lightgbm"]["mae_mwh"],
             "baseline_mae_mwh": r["climatology"]["mae_mwh"], "wind_rule_mae_mwh": r["wind_rule"]["mae_mwh"],
             "recall": r["lightgbm"]["recall"], "n": n}
            for r in table if r["neso_limits"] or r["lead_days"] > 1]


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    curt = pd.read_parquet(ft.PROCESSED / "curtailment.parquet",
                           columns=["farm_id", "zone", "period_start", "curtailed_mwh_a", "bid_price_gbp_mwh"])
    farms = pd.read_csv(ft.ROOT / "reference" / "farms.csv")
    ndf = ft._concat_parquet(sorted((ft.RAW / "elexon" / "NDF").glob("*.parquet")),
                             ["publishTime", "startTime", "demand"])
    limits = ft.load_limits()

    months, results = None, {}
    for lead in ft.LEADS:
        panel = ft.build_panel(curt, farms, ft.load_weather_forecast(lead), limits, ndf, lead_days=lead)
        ft.check_no_leakage(panel)
        months = months or tr.test_months(panel.hour_start)     # lead 1's months, used for every lead
        results[lead] = backtest_lead(panel, months, lead)
        if lead == 1:
            results["1_no_limits"] = backtest_lead(panel, months, 1, no_limits=True)
            d = results[1]      # on all its rows this must equal pipeline.train's headline lightgbm MAE
            log.info("lead 1, all rows: MAE %.3f", tr.score(d.curtailed_mwh, d.pred, d.hour_start)["mae_mwh"])
        del panel

    same = common_rows(results)
    rows = len(next(iter(same.values())))
    table = []
    for key, d in same.items():
        lead = 1 if key == "1_no_limits" else key
        table.append({"lead_days": lead, "issued": f"D-{lead} 10:00 UTC",
                      "neso_limits": key == 1, **_scores(d)})
    out = {"validation": f"walk-forward by month ({months[0]:%Y-%m} to {months[-1]:%Y-%m}); for lead k "
                         "training stops at day 1-k of each test month",
           "rows_scored": rows, "event_threshold_mwh": tr.EVENT_MWH, "chart": chart_rows(table, rows),
           "leads": table,
           "notes": ["every lead is scored on the same farm-hours (those with a weather run for every lead)",
                     "lead k uses Open-Meteo previous_day(k+1), the freshest run issued by D-k 10:00 for "
                     "every hour of D; lead 7 would need an 8-day-old run, which Open-Meteo doesn't keep",
                     "leads 2+ have no NESO limits (published the day before); lead 1 without them is "
                     "listed to separate the two effects",
                     "persistence for lead k = the same hour k+1 days earlier; climatology doesn't depend "
                     "on the lead",
                     "runs older than 2 days start about 2024-06, so leads 2+ train on fewer months than lead 1"]}
    OUT.write_text(json.dumps(out, indent=2))
    log.info("wrote %s (%d farm-hours per lead)", OUT.name, rows)
    for r in table:
        log.info("lead %d limits=%-5s MAE %.3f (clim %.3f) recall %.2f precision %.2f monthly %.1f%%",
                 r["lead_days"], r["neso_limits"], r["lightgbm"]["mae_mwh"], r["climatology"]["mae_mwh"],
                 r["lightgbm"]["recall"], r["lightgbm"]["precision"], r["lightgbm"]["monthly_total_error_pct"])


if __name__ == "__main__":
    main()
