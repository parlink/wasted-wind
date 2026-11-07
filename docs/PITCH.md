# Pitch: Wasted Wind

For whoever is presenting. Two 3-minute running orders, from `docs/BRIEF.md` → Pitch plan, with every number checked against our own outputs. Each number gives its source, so it can be re-checked if the data is rebuilt. Release used: **data-v6**.

## The sentence to open with

> "Scotland pays wind farms to switch off when the grid is full, then pays gas plants in England to replace them. **The yearly totals are known. We revealed the when, the where and the why**, predict it a day ahead, let a model run batteries to capture it, and let you test what new cables or batteries would change."

Never say "nobody knows how much is wasted". Yearly totals are published (Montel, Modo, REF).

## Numbers we can stand behind

| Claim | Number | Source |
| --- | --- | --- |
| Waste rebuilt, 33 Scottish farms (50 units), 2025 | **8.78 TWh** (Montel: 8.8 TWh for northern Scotland) | `curtailment.parquet`, Method A |
| Farm check vs Modo, 2025 | Seagreen **2.72** TWh (Modo 2.7), Moray East **1.74** (1.7), Moray West **1.36** (1.4): all within 3% | #3 |
| Switch-off payments, those farms, 2025 | **~£286 m** (Modo: ~£350 m, all Scottish wind) | Σ −bid × waste |
| Homes-equivalent of that waste | **~3.25 m homes** (2.7 MWh per home per year) | CONTRACT §7 formula |
| North of B4 | **92%** of the waste | `curtailment.parquet` by zone |
| Two independent methods agree | Daily correlation **0.85–0.97** on the five biggest farms | #3, Method A vs B |
| Data behind it | **12.3 m** planned-output records, **21.4 m** bid records, **11.0 m** metered half hours, **1.7 m** turn-down instructions, for all 239 GB wind units, Jan 2024 to Nov 2026 (the model uses the 50 Scottish ones) | `data/raw/elexon` row counts |
| Biggest day | **27 Oct 2026: 115 GWh** switched off (Method B: 107 GWh) | daily Σ |
| Seagreen's worst day | **22 Mar 2024**: planned 26 GWh, delivered **0**, switched off all day | `curtailment.parquet` |
| How often each bottleneck is full (2025) | B4 **55%**, B6 **22%** of half hours (Modo: 42% / 34%, see Limitations) | `residual.parquet` |
| Forecast error, walk-forward May–Oct 2026 | **19.1 MWh** per farm-hour vs wind rule 24.1, climatology 38.3, persistence 43.3 | `model_metrics.json` |
| Waste events caught (> 10 MWh) | recall **82%** (wind rule 52%) | same |
| Forecast without the NESO-limit signals | MAE **19.4**: the edge doesn't rest on the one timing assumption | `ablations` |
| Battery headline, **hold-out months** (Aug–Oct) | our forecast earns **33%** of the hindsight maximum, a battery without it **27%** | `scorecard.json` → `holdout` |
| Same, all six months | **37%** vs **29%** (£1.83 m vs £1.45 m, ceiling £4.97 m) | `scorecard.json` → `results` |
| Simulator calibration | history reproduced for every farm (0.0%) | #28 |
| Simulator demo | **B4 +1.5 GW**: waste 8.78 → 7.89 TWh, but B6 waste 0.70 → **5.05 TWh**: the bottleneck moves south | #28, 2025 window |
| Simulator speed | ~0.2–0.4 s per scenario, full period | #28 |

The dispatcher numbers above are from #27 as fixed after review. If #27 changes again, re-read `scorecard.json` before rehearsing.

## HRT: Best Use of Data for Predictions & Decision Making (3 min)

1. **Problem, one number (20 s).** "In 2025 Britain switched off about 10 TWh of wind, nearly all of it in Scotland, and paid for it twice: once to switch off, once for gas in England. Our 33 farms alone: 8.8 TWh and about £286 m in switch-off payments."
2. **Signals (20 s).** "Market data is the grid's order book: for every wind farm in Britain, 12 million planned-output records, 21 million bids and 1.7 million turn-down instructions, plus day-ahead weather and NESO's boundary limits. Every signal is timestamped and checked to be known by 10:00 the day before."
3. **The model (40 s).** Predict tab: model vs the three baselines, SHAP chart. "LightGBM, walk-forward by month, never shuffled. 19 MWh error per farm-hour vs 24 for the best simple rule, and it catches 82% of waste events vs 52%. Without the one signal whose publication time we can't verify, it's 19.4: the edge is real." **Say where it loses:** "It loses to the wind rule in calm August and on monthly totals, and it runs about 18% low. Calibration is next."
4. **The autopilot, live (60 s).** Autopilot tab: tomorrow's schedule with reasons, then a replayed storm day. "Every half hour the dispatcher decides charge, hold or sell, and says why. On months it was never tuned on, our forecast earns 33% of the theoretical maximum, against 27% for the same battery without it. The maximum is a linear programme with perfect hindsight, so it's a true ceiling, not a guess." Point at `holding: full`: "Most of the time the battery is full and waiting, because the boundary has no room. That's the real constraint."
5. **The simulator as the decision layer (30 s).** Simulate tab: B4 +1.5 GW. "Widen the first bottleneck and waste falls 10%, but B6 waste goes from 0.7 to 5 TWh. The problem just moves south. That's the kind of decision this is for."
6. **Honesty (10 s).** "Three zones, a hand-checked farm list, and constraint flags that aren't perfect. It's all on the Method tab."

## G-Research: Make the invisible visible (3 min)

1. **(15 s)** "The yearly totals are known. Where, when and why are not."
2. **The reveal (60 s).** Reveal tab, one storm day: 27 October 2026, 115 GWh in a day. Click Seagreen: planned vs delivered, with the gap shaded. "We rebuilt this from the market's own instructions, minute by minute. A completely independent physics estimate, from wind speed and a power curve, agrees: 107 vs 115 GWh that day, and 0.85–0.97 correlation day to day." Then Seagreen on 22 March 2024: "planned 26 GWh, delivered nothing."
3. **The second hidden quantity (30 s).** "Behind each bottleneck is Scotland's net position: non-wind generation minus local demand. Nobody publishes it per zone, and we infer it. B4, inside Scotland, is full **55%** of the time; the border, B6, **22%**. **92%** of the waste is north of B4. The first bottleneck is the one that matters."
4. **What we can do with it (60 s).** Forecast, then autopilot, then simulator (the same beats as HRT 3–5, shorter).
5. **Reuse (15 s).** "It's all public data and open code. One command rebuilds the whole picture from Elexon, NESO and Open-Meteo."

## Limitations (say these before a judge asks)

- **Three zones.** England and Wales is one zone; further bottlenecks south of the border aren't modelled.
- **The constraint flag isn't perfect.** We count turn-downs marked as system actions (`soFlag`, 97% of wind turn-downs). Some ordinary balancing may be in, or out.
- **B4 vs B6 attribution.** North farms are also turned down when only B6 is full, so our "B4 full" share (55%) is higher than Modo's 42% and our B6 share lower. Public data doesn't say which boundary each instruction was for.
- **33 farms, hand-checked** (the top 50 units by 2025 constraint volume); coordinates approximate. Smaller farms' wind ends up in the residual.
- **NESO's day-ahead limits have no publication time**, so we assume they're known by 10:00 the day before. The model without them is almost as good (19.4 vs 19.1).
- **Forecast history starts March 2024**, when archived day-ahead weather begins (not January, as first planned).
- **Batteries are a simple model:** no costs, no degradation, no effect on prices, and they charge only from waste. The planning threshold was tuned on May–Jul; Aug–Oct is the hold-out we quote.

## Before going on stage

- [ ] Fresh clone, then `gh release download data-v6 -D data/processed`, then `make serve` on the *other* laptop.
- [ ] Re-run `python -m pipeline.train` the evening before, so "Tomorrow" is live, and check the badge time.
- [ ] Rehearse each running order twice on the real demo. Time it.
- [ ] Devpost: headline from BRIEF, screenshots of Reveal (storm day), Predict (SHAP), Autopilot (timeline), Simulate (B4 +1.5 GW). Make the repo public.
