"""#51: day-ahead GB market price forecast per half hour, for the battery autopilot.

    python -m pipeline.price   -> data/processed/price_forecast.parquet, price block in model_metrics.json

Today the autopilot expects each half hour's price to be its mean over the 7 days ending D-2
(dispatch.scorecard.expected_price). This model predicts it from what is known at the D-1 10:00
issue time, and the baseline is exactly that 7-day mean, so the comparison is like for like.

Signals (asof <= issued_at, checked by pipeline.features.check_no_leakage):
  price_lag2d_gbp_mwh        same half hour on D-2
  price_7d_mean_gbp_mwh      same half hour, mean over D-8 .. D-2 (the current method)
  price_lag2d_daymean_gbp_mwh  mean price over D-2
  gb_demand_fc_mw            Elexon national demand forecast, latest published by the issue time
  pot_scot_mw                forecast Scottish wind potential (day-2 weather run, as in features.py)
  ob_*                       order-book signals from D-2 (pipeline.orderbook)
  slot, weekday, month

Walk-forward by month over the same last 6 months as the waste forecast; training stops the day
before each test month (pipeline.train.train_mask). The money test is in dispatch.scorecard
(`price_forecast_effect`): a more accurate price is only worth having if batteries earn more.
"""
from __future__ import annotations

import json
import logging

import lightgbm as lgb
import numpy as np
import pandas as pd

from pipeline import features as ft
from pipeline import orderbook as ob
from pipeline import train as tr

log = logging.getLogger("price")
OUT = ft.PROCESSED / "price_forecast.parquet"
FEATURES = ["price_lag2d_gbp_mwh", "price_7d_mean_gbp_mwh", "price_lag2d_daymean_gbp_mwh", "gb_demand_fc_mw",
            "pot_scot_mw", *ob.SIGNALS, "slot", "weekday", "month"]
PARAMS = dict(objective="regression", n_estimators=400, learning_rate=0.05, num_leaves=31, min_child_samples=50,
              subsample=0.8, subsample_freq=1, colsample_bytree=0.8, verbose=-1, random_state=0)


# ---------- tested pieces ----------

def lag_features(price: pd.Series) -> pd.DataFrame:
    """Price signals per half hour known at D-1 10:00: same slot D-2, same slot mean D-8..D-2, D-2 mean."""
    idx = price.index
    at = lambda k: price.reindex(idx - pd.Timedelta(days=k)).to_numpy()  # noqa: E731
    lags = np.column_stack([at(k) for k in range(2, 9)])
    daymean = price.groupby(price.index.normalize()).mean()
    out = pd.DataFrame({
        "price_lag2d_gbp_mwh": lags[:, 0],
        "price_7d_mean_gbp_mwh": pd.DataFrame(lags).mean(axis=1, skipna=True).to_numpy(),
        "price_lag2d_daymean_gbp_mwh": (idx.normalize() - pd.Timedelta(days=2)).map(daymean).to_numpy(),
    }, index=idx)
    out["asof_price"] = idx.normalize() - pd.Timedelta(days=1)       # end of D-2
    return out


def by_day(fc: pd.DataFrame):
    """Lookup day -> 48 forecast prices (or None) from rows with period_start, price_pred_gbp_mwh."""
    s = fc.set_index("period_start").price_pred_gbp_mwh

    def get(day: pd.Timestamp):
        v = s.reindex(pd.date_range(day, periods=48, freq="30min")).to_numpy()
        return None if np.isnan(v).any() else v
    return get


# ---------- panel ----------

def build_panel(price: pd.Series, ndf: pd.DataFrame, weather: pd.DataFrame, curt: pd.DataFrame,
                farms: pd.DataFrame, daily_ob: pd.DataFrame) -> pd.DataFrame:
    p = lag_features(price)
    p["price_actual_gbp_mwh"] = price
    p["issued_at"] = ft.issue_time(p.index)
    dem = ft.demand_forecast(ndf)
    p["gb_demand_fc_mw"] = p.index.map(dem.gb_demand_fc_mw)
    p["asof_demand"] = p.index.map(dem.publish_time)
    zp = ft.zone_potential(weather, farms, ft.commissioning(curt))
    pot = zp.sum(axis=1)
    p["pot_scot_mw"] = p.index.floor("h").map(pot)
    p["asof_wind"] = p.index.floor("h") - ft.WIND_LEAD
    obh = ob.for_hours(daily_ob, pd.Series(p.index, index=p.index))
    p = p.join(obh)
    p["slot"] = p.index.hour * 2 + p.index.minute // 30
    p["weekday"], p["month"] = p.index.weekday, p.index.month
    p = p.rename_axis("period_start").reset_index()
    return p[p.price_actual_gbp_mwh.notna() & p.price_7d_mean_gbp_mwh.notna()].reset_index(drop=True)


def run(panel: pd.DataFrame):
    months = tr.test_months(panel.period_start)
    rows = []
    for m in months:
        nxt = m + pd.offsets.MonthBegin(1)
        train = panel[tr.train_mask(panel.period_start, m) & panel.pot_scot_mw.notna()]
        test = panel[(panel.period_start >= m) & (panel.period_start < nxt)].copy()
        model = lgb.LGBMRegressor(**PARAMS).fit(train[FEATURES], train.price_actual_gbp_mwh)
        test["price_pred_gbp_mwh"] = model.predict(test[FEATURES])
        rows.append(test)
        log.info("%s: train %d, test %d, MAE model %.2f vs 7-day %.2f", f"{m:%Y-%m}", len(train), len(test),
                 (test.price_pred_gbp_mwh - test.price_actual_gbp_mwh).abs().mean(),
                 (test.price_7d_mean_gbp_mwh - test.price_actual_gbp_mwh).abs().mean())
    return pd.concat(rows, ignore_index=True), months


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    from pipeline import residual as rs
    farms = pd.read_csv(ft.ROOT / "reference" / "farms.csv")
    price = rs.market_price(rs._load_elexon("MID"))
    ndf = ft._concat_parquet(sorted((ft.RAW / "elexon" / "NDF").glob("*.parquet")), ["publishTime", "startTime", "demand"])
    curt = pd.read_parquet(ft.PROCESSED / "curtailment.parquet", columns=["farm_id", "period_start"])
    panel = build_panel(price, ndf, ft.load_weather_forecast(), curt, farms, ob.load_daily(farms))
    ft.check_no_leakage(panel)
    bt, months = run(panel)
    out = bt[["period_start", "issued_at"]].assign(split="backtest", price_pred_gbp_mwh=bt.price_pred_gbp_mwh,
                                                   price_7day_gbp_mwh=bt.price_7d_mean_gbp_mwh,
                                                   price_actual_gbp_mwh=bt.price_actual_gbp_mwh)
    out.to_parquet(OUT, index=False)
    err = lambda c: float((bt[c] - bt.price_actual_gbp_mwh).abs().mean())  # noqa: E731
    by_month = bt.groupby(bt.period_start.dt.strftime("%Y-%m")).apply(
        lambda g: {"mae_model": round(float((g.price_pred_gbp_mwh - g.price_actual_gbp_mwh).abs().mean()), 2),
                   "mae_7day": round(float((g.price_7d_mean_gbp_mwh - g.price_actual_gbp_mwh).abs().mean()), 2)}).to_dict()
    block = {"validation": f"walk-forward by month ({months[0]:%Y-%m} to {months[-1]:%Y-%m}), trained to D-1 of each month",
             "mae_model_gbp_mwh": round(err("price_pred_gbp_mwh"), 2), "mae_7day_gbp_mwh": round(err("price_7d_mean_gbp_mwh"), 2),
             "by_month": by_month, "features": FEATURES,
             "money_test": "see scorecard.json price_forecast_effect"}
    mpath = ft.PROCESSED / "model_metrics.json"
    m = json.loads(mpath.read_text()) if mpath.exists() else {}
    m["price_model"] = block
    mpath.write_text(json.dumps(m, indent=2))
    log.info("wrote %s (%d rows); MAE model %.2f vs 7-day %.2f GBP/MWh", OUT.name, len(out),
             block["mae_model_gbp_mwh"], block["mae_7day_gbp_mwh"])


if __name__ == "__main__":
    main()
