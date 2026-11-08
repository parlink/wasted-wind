"""Generate FAKE data in app/fixtures/ matching docs/CONTRACT.md, so the app can be built before
Person A releases real data. Run: python -m app.fixtures.make_fixtures

The numbers are invented but internally consistent: wind -> power curve -> zone surplus ->
B4 then B6 limits -> curtailment, so the simulator can calibrate against them too.
BMU IDs start with FX_ so they can never be mistaken for real Elexon units.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from app import contract

OUT = Path(__file__).resolve().parent
SEED = 7
START = pd.Timestamp("2025-11-01", tz="UTC")
DAYS = 14                    # history days in curtailment/residual
LIVE_DAY = START + pd.Timedelta(days=DAYS)   # "tomorrow", forecast + schedule only
FIRST_BACKTEST_DAY = 2       # persistence needs 48 h of history
EVENT_MWH = 10.0

# farm_id, name, lat, lon, zone, site_type, [BMU capacities MW]
FARMS = [
    ("seagreen", "Seagreen", 56.58, -1.75, "north_scotland", "offshore", [537.5, 537.5]),
    ("moray_east", "Moray East", 58.19, -2.72, "north_scotland", "offshore", [950.0]),
    ("moray_west", "Moray West", 58.08, -3.08, "north_scotland", "offshore", [441.0, 441.0]),
    ("beatrice", "Beatrice", 58.26, -3.00, "north_scotland", "offshore", [294.0, 294.0]),
    ("viking", "Viking", 60.30, -1.28, "north_scotland", "onshore", [443.0]),
    ("stronelairg", "Stronelairg", 57.09, -4.58, "north_scotland", "onshore", [228.0]),
    ("whitelee", "Whitelee", 55.68, -4.27, "south_scotland", "onshore", [269.5, 269.5]),
    ("clyde", "Clyde", 55.45, -3.65, "south_scotland", "onshore", [261.0, 261.0]),
    ("kilgallioch", "Kilgallioch", 55.03, -4.73, "south_scotland", "onshore", [239.0]),
]
BATTERIES = [
    {"battery_id": "seagreen_bess", "farm_id": "seagreen", "zone": "north_scotland",
     "power_mw": 100.0, "energy_mwh": 200.0, "efficiency": 0.88},
    {"battery_id": "whitelee_bess", "farm_id": "whitelee", "zone": "south_scotland",
     "power_mw": 50.0, "energy_mwh": 100.0, "efficiency": 0.88},
]


def power_curve(v: np.ndarray) -> np.ndarray:
    """Capacity factor from 100 m wind speed (m/s): cut-in 3, rated 12.5, cut-out 25."""
    cf = np.clip((v**3 - 27.0) / (12.5**3 - 27.0), 0.0, 1.0)
    return np.where(v > 25.0, 0.0, cf) * 0.97


def ar1(rng, n, phi, sigma):
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + rng.normal(0, sigma)
    return x


def farms_table() -> pd.DataFrame:
    rows = []
    for fid, name, lat, lon, zone, kind, caps in FARMS:
        tag = fid.replace("_", "").upper()[:6]
        for i, cap in enumerate(caps, 1):
            rows.append(dict(bmu_id=f"FX_{tag}-{i}", farm_id=fid, farm_name=name, lat=lat, lon=lon,
                             capacity_mw=cap, zone=zone, site_type=kind, repd_ref_id=None))
    return pd.DataFrame(rows, columns=contract.FARMS_COLUMNS)


def simulate_history(rng, farms: pd.DataFrame, n_days: int):
    """Half-hourly fake grid for n_days. Returns per-BMU and per-zone frames (MW)."""
    periods = pd.date_range(START, periods=48 * n_days, freq="30min", tz="UTC")
    n = len(periods)
    hours = np.arange(n_days * 24)
    # Scotland-wide weather: slow synoptic cycle + AR noise, hourly, repeated per half hour
    base_h = 9.5 + 5.0 * np.sin(2 * np.pi * hours / (24 * 4.3)) + ar1(rng, len(hours), 0.95, 0.6)
    farm_ids = [f[0] for f in FARMS]
    wind_h = {}
    for fid, _name, _lat, _lon, _zone, kind, _caps in FARMS:
        scale = 1.15 if kind == "offshore" else 0.95
        wind_h[fid] = np.clip(base_h * scale + ar1(rng, len(hours), 0.9, 0.5), 0, 30)
    wind = {fid: np.repeat(w, 2) for fid, w in wind_h.items()}

    farm_cap = farms.groupby("farm_id")["capacity_mw"].sum()
    pot = pd.DataFrame({fid: farm_cap[fid] * power_curve(wind[fid]) for fid in farm_ids}, index=periods)
    zone_of = {f[0]: f[4] for f in FARMS}
    north = [f for f in farm_ids if zone_of[f] == "north_scotland"]
    south = [f for f in farm_ids if zone_of[f] == "south_scotland"]

    tod = (periods.hour + periods.minute / 60).to_numpy()
    day_shape = np.sin(np.pi * np.clip(tod - 6, 0, 16) / 16)          # 0 at night, 1 mid-afternoon
    res_n = -400 + 150 * (1 - day_shape) + ar1(rng, n, 0.9, 30)
    res_s = -700 - 400 * day_shape + ar1(rng, n, 0.9, 40)
    b4 = np.full(n, 2400.0)
    b4[48 * 5:48 * 7] = 1500.0                                         # planned outage, days 6-7
    b6 = np.full(n, 2600.0)
    b6[48 * 10:48 * 11] = 2000.0

    pn = pot[north].sum(axis=1).to_numpy()
    ps = pot[south].sum(axis=1).to_numpy()
    surplus_n = pn + res_n
    excess_b4 = np.clip(surplus_n - b4, 0, pn)
    flow_b4 = surplus_n - excess_b4
    curt = pd.DataFrame(0.0, index=periods, columns=farm_ids)
    with np.errstate(invalid="ignore", divide="ignore"):
        share_n = np.nan_to_num(pot[north].to_numpy() / pn[:, None])
    curt[north] = share_n * excess_b4[:, None]
    after_b4 = pot - curt
    scot_left = after_b4.sum(axis=1).to_numpy()
    surplus_s = ps + res_s + flow_b4
    excess_b6 = np.clip(surplus_s - b6, 0, scot_left)
    with np.errstate(invalid="ignore", divide="ignore"):
        share_all = np.nan_to_num(after_b4.to_numpy() / scot_left[:, None])
    curt += share_all * excess_b6[:, None]
    flow_b6 = surplus_s - excess_b6

    demand = 28000 + 7000 * day_shape + ar1(rng, n, 0.95, 150)
    scot_cf = pot.sum(axis=1).to_numpy() / farm_cap.sum()
    price = 82 + 30 * day_shape - 70 * scot_cf + ar1(rng, n, 0.8, 6)

    zones = pd.DataFrame({
        "period_start": np.tile(periods, 2),
        "zone": ["north_scotland"] * n + ["south_scotland"] * n,
        "residual_mw": np.r_[res_n, res_s],
        "excess_mw": np.r_[excess_b4, excess_b6],
        "boundary_limit_mw": np.r_[b4, b6],
        "boundary_flow_mw": np.r_[flow_b4, flow_b6],
        "wind_delivered_mwh": np.r_[(pot[north] - curt[north]).sum(axis=1), (pot[south] - curt[south]).sum(axis=1)] * 0.5,
        "market_price_gbp_mwh": np.r_[price, price],
        "gb_demand_mw": np.r_[demand, demand],
        # fake town temperature: a November day-night cycle, the north a degree colder
        "temperature_2m_c": np.r_[5.0 - 1.0 + 3 * day_shape + ar1(rng, n, 0.97, 0.4), 5.0 + 3 * day_shape + ar1(rng, n, 0.97, 0.4)],
    })
    return periods, wind, pot, curt, zones


def curtailment_table(rng, farms, periods, wind, pot, curt) -> pd.DataFrame:
    farm_cap = farms.groupby("farm_id")["capacity_mw"].sum()
    top5 = set(farm_cap.sort_values(ascending=False).index[:5])
    bid_base = {f[0]: rng.uniform(-95, -35) for f in FARMS}
    local = periods.tz_convert("Europe/London")
    sdate = local.normalize()
    sp = ((local - sdate).total_seconds() // 1800 + 1).astype(int)
    out = []
    for b in farms.itertuples():
        frac = b.capacity_mw / farm_cap[b.farm_id]
        planned = pot[b.farm_id].to_numpy() * frac * 0.5
        c_a = curt[b.farm_id].to_numpy() * frac * 0.5
        accepted = planned - c_a
        metered = accepted * rng.uniform(0.96, 1.0, len(periods))
        if b.farm_id in top5:
            pot_b = planned * np.exp(rng.normal(0, 0.08, len(periods)))
            c_b = np.maximum(0, pot_b - metered)
        else:
            pot_b = c_b = np.full(len(periods), np.nan)
        bid = np.where(c_a > 1e-6, bid_base[b.farm_id] + rng.normal(0, 8, len(periods)), np.nan)
        out.append(pd.DataFrame({
            "bmu_id": b.bmu_id, "farm_id": b.farm_id, "zone": b.zone, "period_start": periods,
            "settlement_date": sdate.date, "settlement_period": sp,
            "planned_mwh": planned, "accepted_mwh": accepted, "curtailed_mwh_a": c_a,
            "potential_mwh_b": pot_b, "curtailed_mwh_b": c_b, "metered_mwh": metered,
            "bid_price_gbp_mwh": bid, "wind_speed_100m_ms": wind[b.farm_id],
        }))
    return pd.concat(out, ignore_index=True)[contract.CURTAILMENT_COLUMNS]


def residual_table(zones: pd.DataFrame) -> pd.DataFrame:
    z = zones.copy()
    z["residual_method"] = np.where(z["excess_mw"] > 1e-6, "binding", "flow_estimate")
    z["boundary"] = z["zone"].map(contract.BOUNDARY_OF_ZONE)
    return z[contract.RESIDUAL_COLUMNS + contract.RESIDUAL_OPTIONAL].sort_values(["zone", "period_start"]).reset_index(drop=True)


def forecast_table(rng, pot, curt) -> pd.DataFrame:
    """Hourly per-farm fake forecast: backtest for history days, live for LIVE_DAY."""
    actual = (curt * 0.5).resample("1h").sum()                     # MWh per farm-hour
    scot_pot = pot.sum(axis=1).resample("1h").mean()
    zone_of = {f[0]: f[4] for f in FARMS}
    rows = []
    for fid in actual.columns:
        a = actual[fid]
        noise = np.exp(rng.normal(0, 0.35, len(a)))
        false_alarm = (scot_pot.to_numpy() > 3000) * rng.exponential(3.0, len(a)) * (a.to_numpy() == 0)
        pred = np.maximum(0, a.to_numpy() * noise + false_alarm)
        rule_mean = a[scot_pot > 3200].mean() if (scot_pot > 3200).any() else 0.0
        df = pd.DataFrame({
            "farm_id": fid, "zone": zone_of[fid], "hour_start": a.index,
            "curtailed_mwh_pred": pred, "curtailed_mwh_p10": pred * 0.45, "curtailed_mwh_p90": pred * 1.8 + 2,
            "curtailed_mwh_actual": a.to_numpy(),
            "baseline_persistence_mwh": a.shift(48).to_numpy(),
            "baseline_wind_rule_mwh": np.where(scot_pot.to_numpy() > 3200, rule_mean, 0.0),
        })
        hist = df["hour_start"] < LIVE_DAY
        clim = df[hist].groupby(df["hour_start"].dt.hour)["curtailed_mwh_actual"].mean()
        df["baseline_climatology_mwh"] = df["hour_start"].dt.hour.map(clim)
        rows.append(df)
    df = pd.concat(rows, ignore_index=True)
    day = df["hour_start"].dt.normalize()
    df["issued_at"] = day - pd.Timedelta(hours=14)                   # D-1 10:00 UTC
    df["split"] = np.where(df["hour_start"] >= LIVE_DAY, "live", "backtest")
    df.loc[df["split"] == "live", "curtailed_mwh_actual"] = np.nan
    df["model_version"] = "fixture-v1"
    df = df[day >= START + pd.Timedelta(days=FIRST_BACKTEST_DAY)]
    return df[contract.FORECAST_COLUMNS].reset_index(drop=True)


def model_metrics(fc: pd.DataFrame) -> dict:
    bt = fc[fc["split"] == "backtest"]
    actual = bt["curtailed_mwh_actual"]
    cols = {"lightgbm": "curtailed_mwh_pred", "persistence": "baseline_persistence_mwh",
            "climatology": "baseline_climatology_mwh", "wind_rule": "baseline_wind_rule_mwh"}
    models = {}
    for name, col in cols.items():
        p = bt[col].fillna(0)
        hit_p, hit_a = p > EVENT_MWH, actual > EVENT_MWH
        models[name] = {
            "mae_mwh": round(float((p - actual).abs().mean()), 3),
            "precision": round(float((hit_p & hit_a).sum() / max(hit_p.sum(), 1)), 3),
            "recall": round(float((hit_p & hit_a).sum() / max(hit_a.sum(), 1)), 3),
            "monthly_total_error_pct": round(float(abs(p.sum() - actual.sum()) / actual.sum() * 100), 2),
        }
    return {
        "model_version": "fixture-v1",
        "validation": "FAKE fixture data, not a real backtest",
        "event_threshold_mwh": EVENT_MWH,
        "models": models,
        "shap_top_signals": [
            {"signal": "b4_excess_potential_mw", "mean_abs_shap": 2.1},
            {"signal": "scotland_forecast_potential_mw", "mean_abs_shap": 1.6},
            {"signal": "wind_speed_100m_forecast_ms", "mean_abs_shap": 1.2},
            {"signal": "gb_demand_forecast_mw", "mean_abs_shap": 0.8},
            {"signal": "b4_limit_mw", "mean_abs_shap": 0.7},
            {"signal": "curtailed_mwh_lag48h", "mean_abs_shap": 0.5},
            {"signal": "hour_of_day", "mean_abs_shap": 0.3},
            {"signal": "median_bid_price_30d_gbp_mwh", "mean_abs_shap": 0.2},
        ],
    }


def _rule_day(bat, signal, price_exp, actual_waste, price, soc, boundary, source):
    """Fixture-only stand-in for dispatch.rule_based.plan_day (Person A builds the real one)."""
    p, e, eff = bat["power_mw"], bat["energy_mwh"], bat["efficiency"]
    quiet = signal <= 1.0
    order = np.argsort(-np.where(quiet, price_exp, -np.inf))
    top = set(order[:8][quiet[order[:8]]])
    rows = []
    for i in range(48):
        if signal[i] > 1.0 and soc < e - 1e-6:
            mwh_in = min(p * 0.5, (e - soc) / eff, signal[i])
            soc += mwh_in * eff
            captured = min(mwh_in, actual_waste[i])
            rev = -(mwh_in - captured) * price[i]
            rows.append(("charge", mwh_in / 0.5, soc, captured, rev,
                         f"charging: {signal[i]:.0f} MWh waste {source} behind {boundary}"))
        elif i in top and soc > 1e-6:
            mwh_out = min(p * 0.5, soc)
            soc -= mwh_out
            rows.append(("discharge", mwh_out / 0.5, soc, 0.0, mwh_out * price[i],
                         f"discharging: expected £{price_exp[i]:.0f}/MWh is a top-8 period, boundary has room"))
        else:
            why = "battery full" if soc >= e - 1e-6 else "no waste expected, price not in top periods"
            rows.append(("hold", 0.0, soc, 0.0, 0.0, f"holding: {why}"))
    return rows, soc


def schedules_table(curt, zones, fc):
    price = zones[zones["zone"] == "north_scotland"].set_index("period_start")["market_price_gbp_mwh"]
    sp = price.index.hour * 2 + price.index.minute // 30
    fc_half = (fc.set_index(["farm_id", "hour_start"])["curtailed_mwh_pred"] / 2)
    days = [START + pd.Timedelta(days=d) for d in range(FIRST_BACKTEST_DAY, DAYS + 1)]
    out = []
    for bat in BATTERIES:
        fid, boundary = bat["farm_id"], contract.BOUNDARY_OF_ZONE[bat["zone"]]
        waste = curt[fid] * 0.5                                          # MWh per half hour
        fc_f = fc_half.loc[fid]
        fc_f = fc_f.reindex(fc_f.index.repeat(2))
        fc_f.index = fc_f.index + pd.to_timedelta(np.tile([0, 30], len(fc_f) // 2), unit="min")
        for strategy in contract.STRATEGIES:
            soc = 0.0
            for day in days:
                idx = pd.date_range(day, periods=48, freq="30min", tz="UTC")
                live = day >= LIVE_DAY
                if live and strategy != "forecast":
                    continue
                act = np.zeros(48) if live else waste.reindex(idx).to_numpy()
                pr = np.zeros(48) if live else price.reindex(idx).to_numpy()
                week = (price.index < day) & (price.index >= day - pd.Timedelta(days=7))
                pr_exp = price[week].groupby(sp[week]).mean().reindex(range(48)).to_numpy()
                if strategy == "do_nothing":
                    sig, src = np.zeros(48), ""
                elif strategy == "perfect":
                    sig, src, pr_exp = act, "known", pr
                elif strategy == "reactive":
                    sig, src = np.r_[0.0, act[:-1]], "seen last period"
                else:
                    sig, src = fc_f.reindex(idx).fillna(0).to_numpy(), "forecast"
                rows, soc = _rule_day(bat, sig, pr_exp, act, pr, soc, boundary, src)
                df = pd.DataFrame(rows, columns=["action", "power_mw", "soc_mwh", "captured_curtailment_mwh",
                                                 "revenue_gbp", "reason"])
                if live:
                    df[["captured_curtailment_mwh", "revenue_gbp"]] = np.nan
                df["battery_id"], df["farm_id"], df["zone"] = bat["battery_id"], fid, bat["zone"]
                df["strategy"], df["dispatcher"], df["period_start"] = strategy, "rule", idx
                df["plan_issued_at"] = day - pd.Timedelta(hours=6)            # D-1 18:00 UTC
                out.append(df)
    return pd.concat(out, ignore_index=True)[contract.SCHEDULES_COLUMNS]


def scorecard(sched: pd.DataFrame) -> dict:
    """payments_avoided uses a flat fake £60/MWh switch-off payment."""
    hist = sched[sched["period_start"] < LIVE_DAY]
    results = []
    perfect = hist[hist["strategy"] == "perfect"]["revenue_gbp"].sum()
    for (strategy, disp), g in hist.groupby(["strategy", "dispatcher"]):
        rev = float(g["revenue_gbp"].sum())
        captured = float(g["captured_curtailment_mwh"].sum())
        results.append({"strategy": strategy, "dispatcher": disp,
                        "captured_curtailment_mwh": round(captured, 1), "revenue_gbp": round(rev, 0),
                        "payments_avoided_gbp": round(captured * 60.0, 0),
                        "capture_ratio": round(rev / perfect, 3) if perfect else 0.0})
    return {
        "period": {"start": f"{hist['period_start'].min():%Y-%m-%dT%H:%M:%SZ}",
                   "end": f"{LIVE_DAY:%Y-%m-%dT%H:%M:%SZ}"},
        "batteries": BATTERIES,
        "results": sorted(results, key=lambda r: contract.STRATEGIES.index(r["strategy"])),
    }


def main():
    rng = np.random.default_rng(SEED)
    farms = farms_table()
    periods, wind, pot, curt, zones = simulate_history(rng, farms, DAYS + 1)
    hist = periods < LIVE_DAY
    curtail = curtailment_table(rng, farms, periods[hist], {k: v[hist] for k, v in wind.items()},
                                pot[hist], curt[hist])
    resid = residual_table(zones[zones["period_start"] < LIVE_DAY])
    fc = forecast_table(rng, pot, curt)
    sched = schedules_table(curt, zones, fc)
    card = scorecard(sched)
    # live mode has run through the first 9 hours of the live day: those half hours were executed
    ex = sched[(sched["strategy"] == "forecast") & (sched["period_start"] >= LIVE_DAY)
               & (sched["period_start"] < LIVE_DAY + pd.Timedelta(hours=9))].copy()
    ex[["captured_curtailment_mwh", "revenue_gbp"]] = ex[["captured_curtailment_mwh", "revenue_gbp"]].fillna(0.0)

    farms.to_csv(OUT / "farms.csv", index=False)
    for name, df in [("curtailment.parquet", curtail), ("residual.parquet", resid),
                     ("forecast.parquet", fc), ("schedules.parquet", sched), ("live_executed.parquet", ex)]:
        df.to_parquet(OUT / name, index=False)
    (OUT / "model_metrics.json").write_text(json.dumps(model_metrics(fc), indent=2))
    (OUT / "scorecard.json").write_text(json.dumps(card, indent=2))
    tot = curtail["curtailed_mwh_a"].sum()
    print(f"fixtures written to {OUT}: {len(curtail)} curtailment rows, {tot:,.0f} MWh curtailed, "
          f"B4 binding {(resid.query('zone==\"north_scotland\"').residual_method == 'binding').mean():.0%}")
    for r in card["results"]:
        print(f"  {r['strategy']:<10} £{r['revenue_gbp']:>10,.0f}  capture {r['capture_ratio']}")


if __name__ == "__main__":
    main()
