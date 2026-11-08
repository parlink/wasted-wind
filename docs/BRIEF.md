# Wasted Wind: Project Brief

Nov 7, 2026 · @p

## Summary

Wasted Wind rebuilds how much clean electricity each Scottish wind farm is paid to throw away, predicts tomorrow's waste, automatically schedules batteries to catch it, and lets anyone test fixes in a live grid simulator with 13 controls.

**One-line pitch:** "Scotland pays wind farms to switch off when the grid is full, then pays gas plants in England to replace them. We revealed where and when that happens, predict it a day ahead, let a model run batteries automatically to capture it, and let you test what new cables, batteries or demand would change."

**What judges see:** a full-screen map of Scotland with wind farms sized by wasted energy, a "Tomorrow" forecast, an automated battery schedule scored against what really happened, and a simulator panel where every slider instantly changes the wasted energy, the cost and the CO₂.

**Tracks:** HRT *Best Use of Data for Predictions & Decision Making* and G-Research *Make the invisible visible*. Both are checked line by line in Track fit below.

**Time:** about 48 person-hours for the full version and 37 for the core. HackTheBurgh's 24 hours (7–8 November 2026) give two people about 44 working hours, so Core is safe and most of Full is reachable with AI agents working in parallel. See Time estimate.

**You are already on the clock.** The event runs today, so read Summary, Concepts, Time estimate, GitHub setup and AI agent setup first; the five Parts are reference for when you build each one. Export this doc as Markdown into the repo as docs/BRIEF.md so your agents can read it too.

## Concepts in plain English

Every technical word in this brief, explained without jargon. Skim it once now; come back when a word trips you up.

**Electricity and the grid**

| Term | What it means | Everyday comparison |
| --- | --- | --- |
| The grid | The network of high-voltage cables that carries electricity from power stations to homes | The motorway system for electricity |
| MW (megawatt) | How fast electricity is produced or used *right now* | Speed of a tap: litres per second |
| MWh (megawatt-hour) | An *amount* of electricity: 1 MW running for 1 hour | Litres in a bucket |
| TWh (terawatt-hour) | 1,000,000 MWh. Britain's yearly wasted wind is about 10 TWh | A reservoir |
| Curtailment | The grid tells a wind farm to switch off even though the wind is blowing, because the cables can't carry the power away | A full motorway, so a slip road is closed |
| Constraint payment | Money paid to the wind farm for switching off, because it lost income. Bill-payers fund it | Paying a delivery driver who couldn't get through |
| Replacement generation | Gas power stations in England switched on to make up for the wind switched off in Scotland. Paid for a second time | Paying a second driver from a closer depot |
| Transmission boundary | A line on the map where the cables crossing it have a maximum capacity | A bridge with a weight limit |
| B4 boundary | The bottleneck between North Scotland and South Scotland | The first bridge |
| B6 boundary | The bottleneck between Scotland and England | The second bridge |
| Zone | An area of the grid between boundaries. We use three: North Scotland, South Scotland, and England and Wales | Towns between bridges |
| HVDC subsea link | An undersea cable that carries power straight from Scotland to England, skipping the boundaries | A ferry that avoids both bridges |
| NESO | National Energy System Operator, which runs the grid minute by minute and decides who switches off | Air traffic control |
| Elexon | The organisation that records and settles every trade in the electricity market, and publishes the data we use | The stock exchange's record-keeper |
| Balancing Mechanism | The live market NESO uses to ask power stations to go up or down | An auction run every half hour |
| BMU (Balancing Mechanism Unit) | One registered generator in that market; a wind farm may have one or several | A trader's account number |
| Settlement period | The half-hour blocks the market is measured in; 48 per day | Time slots on a timetable |
| Physical Notification (PN) | What a wind farm tells NESO it plans to generate | Your announced travel plan |
| Bid | A price a generator names for being turned *down* | "I'll stop if you pay me £X" |
| Bid-offer acceptance (BOA) | NESO accepting that bid and telling the farm to reduce output | The deal being taken |
| Metered output (B1610) | What the farm actually produced, measured by its meter | Your actual journey |
| Market index price | The going rate for electricity in each half hour | The market price of a share |
| Power curve | How much a turbine generates at each wind speed. Output rises steeply with wind, flattens at full power, and stops in storms | A car's speed vs how hard you press the pedal |
| 100 m wind speed | Wind speed at turbine height, which is what matters (not ground-level wind) | Weather at cruising altitude |
| Battery: power vs energy | Power (MW) is how fast it charges; energy (MWh) is how much it holds | Pipe width vs tank size |
| Round-trip efficiency | Share of energy you get back out after storing it; around 88% | Some water spills when you pour it in and out |
| State of charge | How full the battery is right now | Fuel gauge |
| Cycle | One full charge and discharge | One fill-and-empty of the tank |
| Electrolyser | A machine that uses electricity to split water into hydrogen fuel | Turning spare electricity into a storable fuel |

**Data, prediction and decisions**

| Term | What it means | Everyday comparison |
| --- | --- | --- |
| Hidden variable | A number that matters but nobody publishes, so we work it out from other data. Here: wasted energy per farm per half hour | Working out someone's speed from two photos and timestamps |
| Reconstruction | Calculating that hidden number from public data | Rebuilding a crash from skid marks |
| Cross-check | A second, independent way of calculating the same thing, to see if both agree | Checking a sum with a calculator *and* by hand |
| Residual | Everything else in a zone (other power stations minus local demand) that we infer rather than measure | The unknown in an equation you solve for |
| Calibration | Tuning the simulator so it reproduces what really happened before we ask it "what if" | Zeroing a scale before weighing |
| Forecast model | A program that learns patterns from the past to predict tomorrow | A weather forecast, for wasted wind |
| LightGBM | The specific model we use: it builds hundreds of small decision trees that each correct the last one's mistakes | A panel of experts, each fixing the last one's errors |
| Tweedie objective | A setting that suits a target that is usually zero but sometimes large | Rainfall: most days 0 mm, some days 40 mm |
| Feature / signal | One input the model uses, such as forecast wind speed. HRT calls these signals | A clue a detective uses |
| Data leakage | Accidentally letting the model see information it wouldn't have in real life, which makes it look better than it is | Seeing the exam answers before the test |
| Walk-forward validation | Train on the past, test on the next month, slide forward, repeat. Never test on the past | Testing a weather forecaster only on days that hadn't happened yet |
| Baseline | A simple rule the model must beat to prove it is useful, such as "same as two days ago" | The bar to clear |
| MAE (mean absolute error) | Average size of the model's mistakes, in MWh | Average miss distance in darts |
| Precision / recall | Precision: when it predicts waste, how often it's right. Recall: of all real waste events, how many it caught | Smoke alarm: false alarms vs missed fires |
| SHAP | A method that shows how much each signal pushed a prediction up or down | A receipt showing what each item cost |
| Backtest | Replaying the past to see how a strategy would have performed | Testing a betting system on last season's results |
| Dispatcher | The automated program that decides, each half hour, whether a battery charges, waits or discharges | An autopilot |
| Policy | The rules the dispatcher follows | The autopilot's settings |
| Simulator | A model of the grid that recalculates outcomes when you change something | A flight simulator for the grid |
| Lever | One setting a user can change in the simulator, such as cable capacity | A slider on a mixing desk |
| Scenario | One combination of lever settings | One "what if" question |

**Software and AI tools**

| Term | What it means |
| --- | --- |
| API | A web address a program can call to get data back, such as Elexon's |
| Endpoint | One specific API address, such as the bid-acceptance data |
| Parquet | A compact file format for large tables; much faster than CSV |
| Python, pandas, NumPy | The programming language and its standard libraries for working with tables and numbers |
| FastAPI | A Python tool for building our own small API that the website calls |
| MapLibre | A free library for drawing interactive maps in a web page |
| Git / GitHub | Git tracks every change to the code; GitHub hosts it online so both of you (and your agents) share one copy |
| Branch | A separate line of work in Git, so two people can work without overwriting each other |
| Pull request (PR) | A request to merge a branch into the main code, where it can be reviewed first |
| Issue | A task written on GitHub, which people or agents can pick up |
| Worktree | A second folder of the same repository on a different branch, so two agents can work on one machine without clashing |
| AI coding agent | An AI that can read your files, write code, run it and fix errors itself, rather than just chatting. Claude Code and Gemini CLI are agents |
| CLAUDE.md / GEMINI.md | A notes file in the repository that each agent reads automatically at the start of every session |
| Subagent | A helper Claude Code starts inside a session for a focused job, like a junior colleague |
| Headless mode | Running an agent with one command and no chat window, so another program can call it |
| MCP | A standard way to plug an outside tool into an AI agent |
| Usage limit | How much AI work your plan allows per 5-hour window and per week |

## Motivation

In 2025 Great Britain switched off about 10 TWh of wind power, 8.8 TWh of it in northern Scotland, because the cables south were full ([Montel via Sustainability Magazine](https://sustainabilitymag.com/news/infrastructure-keep-pace-renewables-supply)). That is roughly enough to power every Scottish home for a year.

Bill-payers pay twice. In 2025 Scottish wind farms were paid around £350 million to switch off, and replacement gas generation cost over £1 billion more ([Modo Energy](https://modoenergy.com/research/en/gb-great-britain-battery-energy-storage-curtailment-wind-constraint-scotland-costs)). Modo reports that the B4 and B6 boundaries were constrained 42% and 34% of the time, and that Seagreen alone lost 2.7 TWh.

**Why it is a good hackathon problem:**

- **It is local.** HackTheBurgh is in Edinburgh, and the waste happens a few hours' drive north.
- **The data is public but scattered.** It sits across Elexon, NESO and weather archives, minute by minute and unit by unit. Nobody has stitched it into a half-hourly, per-farm, per-bottleneck picture.
- **It has real decisions attached.** Where to put batteries, whether new cables are worth it, and when a battery should charge are decisions worth millions.
- **It looks like HRT's own work.** It is a market with bids and prices, plus alternative data (weather), turned into automated decisions.

## Track fit, verified

Every line of both briefs maps to a built feature. The one HRT gap from the earlier version (human sliders instead of automated decisions) is closed by Part 3, the automated dispatcher.

**HRT: Best Use of Data for Predictions & Decision Making**

| HRT card says | Wasted Wind delivers | Verdict |
| --- | --- | --- |
| Track title: *Predictions* | Part 2: a day-ahead forecast of wasted energy per farm, scored against three baselines | Met |
| Track title: *Decision Making* | Part 3: a dispatcher that turns forecasts into battery schedules automatically; Part 4: storage and cable decisions | Met |
| Massive Scale | About 50 wind units × minute-level data × 22+ months: roughly 50 million points per series, plus prices, weather and boundary limits | Met at hackathon scale. Quote the real row counts; never say "petabyte" |
| Diverse Inputs: market + alt data | Market: bids, accepted bids, prices (Elexon's bid data is effectively the grid's order book). Alt: weather forecasts, NESO boundary limits, farm locations | Strong match |
| Signal creation | Engineered signals: Scotland-wide forecast potential, demand, recent bid prices, persistence. A SHAP chart shows which signals matter | Met; use the word "signals" in the pitch |
| Real-Time ML | Live mode: the forecast refreshes from the newest weather run, and the dispatcher re-plans every half hour as Elexon publishes new acceptances | Met; demo it live |
| Automated Decisions | The dispatcher outputs charge/hold/discharge for every battery for every half hour, with no human input, and a scorecard shows the £ and MWh it earned vs a no-forecast battery | Met. This was the fix |
| "High-speed accuracy" | Dispatcher plans a day in under a second; accuracy reported as error vs baselines and £ vs a perfect-hindsight battery | Met |

**G-Research: Make the invisible visible**

| G-Research slide says | Wasted Wind delivers | Verdict |
| --- | --- | --- |
| Choose something hard to observe | When, where and why wind is wasted, half-hour by half-hour and farm by farm, and which bottleneck caused it | Met |
| Build tools to collect data, or dig into public data | Fetchers for Elexon, NESO and Open-Meteo; a reconstruction pipeline | Met |
| Example: "a market's mood" | We read the grid's hidden state from the bids in its market | Close to their own example |
| Show what data is hidden | Per-farm half-hourly waste and Scotland's hidden net position (the residual) | Met |
| How we can find it | Planned vs accepted output, cross-checked with a physics estimate | Met; show both lines on one storm day |
| What we can do with it | Forecast it, automate batteries, simulate fixes | Met |

**The one claim to word carefully:** annual totals and per-farm payment totals *are* published (by the Renewable Energy Foundation and analysts such as Montel). So never say "nobody knows how much is wasted". Say: **"The yearly totals are known. We revealed the when, the where and the why, and what would change it."**

**Above and beyond:** most hackathon entries do one of reveal, predict, or simulate. This project does all three, plus an automated decision-maker, calibrated against reality and grounded in a validated hidden variable.

## What we're building

Five parts, each usable on its own, chained so each one feeds the next.

&#91;embedded content: the five parts · how data flows between them\]

Part 1 produces the hidden numbers. Part 2 learns to predict them. Part 3 acts on the predictions automatically. Part 4 uses Part 1 to calibrate itself and Part 3 to run its batteries. Part 5 shows all four on one map.

## Requirements

Everything is free except the AI plans you already have; the only hard requirements are two laptops, internet, and the data downloads started in the first hour.

**Data** (all public, no payment). Elexon's API needs no key; base address `https://data.elexon.co.uk/bmrs/api/v1` ([developer portal](https://developer.data.elexon.co.uk/)).

| Data | Where | Detail | Used for |
| --- | --- | --- | --- |
| Planned output (PN) | Elexon `/datasets/PN` or `/balancing/physical` | Minute-level MW, per unit | What each farm meant to generate |
| Accepted bids (BOALF) | Elexon `/balancing/acceptances`, plus `/balancing/acceptances/all/latest` for live mode ([spec](https://apis.io/apis/elexon/elexon-bid-offer-acceptances-api/)) | Each turn-down instruction | How far each farm was turned down |
| Bid prices (BOD) | Elexon `/balancing/bid-offer` | Per half hour | Cost of switching each farm off |
| Metered output (B1610) | Elexon `/datasets/B1610/stream` ([guide](https://www.elexon.co.uk/article/bsc-insight-how-to-get-more-detailed-metered-volume-data-from-our-insights-solution/)) | Half-hourly MWh | What was actually produced |
| Prices and demand | Elexon market index, system prices, demand outturn and forecast (confirm exact paths in the portal) | Half-hourly | Battery revenue, demand signal |
| Boundary limits and flows | NESO Day Ahead Constraint Flows and Limits, CSV ([NESO](https://neso.energy/data-portal/day-ahead-constraint-flows-and-limits/day_ahead_constraint_flows_and_limits)) | Half-hourly MW per boundary | Simulator limits for B4 and B6 |
| Weekly boundary limits | NESO network congestion data ([NESO](https://www.neso.energy/data-portal/operational-transparency-forum-network-congestion-data/network_congestion_forecast_and_actual_data)) | Weekly MW | Cross-check and fallback |
| Weather, actual | Open-Meteo archive: 100 m wind speed and direction, 2 m temperature | Hourly | Physics cross-check, heat-pump demand |
| Weather, as forecast the day before | Open-Meteo Previous Runs API, from January 2024 ([Open-Meteo](https://openmeteo.substack.com/p/single-runs-api)) | Hourly | Leak-free model signals |
| Farm locations, capacity | Renewable Energy Planning Database (UK government CSV) | Static | Map, capacities, zones |
| Unit-to-farm mapping | Elexon BM unit reference data, hand-checked for the top 25–50 | Static | Joining Elexon IDs to farms |

**Software** (install before anything else):

| Tool | Purpose |
| --- | --- |
| Python 3.11+ with pandas, numpy, pyarrow, requests, lightgbm, shap, scikit-learn, scipy, fastapi, uvicorn, pytest (numba optional) | Pipeline, model, simulator, server |
| Node.js 20+ | Needed by Gemini CLI |
| Git and the GitHub CLI (`gh`) | Shared code and the agents' task board |
| VS Code with the Claude Code extension | Main editor and agent |
| MapLibre GL JS and Chart.js, loaded from a CDN | Map and charts, no install |
| OpenFreeMap or a free MapTiler key | Base map tiles |

**Accounts:**

- Two GitHub accounts, both added to one repository.
- Two Claude Pro plans, one per person. Pro includes Claude Code, and chat and Claude Code share the same usage allowance ([Claude Help Center](https://support.claude.com/en/articles/11145838)).
- One free Google AI Studio API key for Gemini CLI (see AI agent setup: your Gemini Plus plan does not unlock Gemini CLI).
- Gemini Plus, used in the Gemini web app for research, reading documentation and drafting.

**Hardware and network:** any recent laptop with 8 GB RAM and 5 GB of free disk. Venue Wi-Fi is the biggest risk: start the Elexon downloads in hour one, cache everything, and share the processed files (not the raw downloads) through a GitHub Release so the second laptop never re-downloads.

## Part 1, Reveal: rebuilding the hidden waste

**In plain English:** each wind farm tells the grid "I plan to make 400 MW". If the grid then says "only make 150 MW", the missing 250 MW for that stretch of time is wasted wind. We add that gap up for every farm, every half hour.

**Method A, from market data (main method).** Planned output (PN) and accepted levels (BOALF) arrive as MW values at timestamps. Spread both onto a one-minute grid per unit, take the gap wherever accepted is below planned, and add it up per half hour:

```latex
C_{u,p} = \frac{1}{60} \sum_{t \in p} \max\big(0,\; \mathrm{PN}_u(t) - \mathrm{BOAL}_u(t)\big)
```

In words: wasted MWh for unit u in half-hour p = the sum, over each minute, of planned minus accepted (never below zero), divided by 60 to turn MW-minutes into MWh. Each acceptance carries a system flag; keep only flagged (constraint) bids so ordinary market balancing isn't counted. Confirm the field name from a real sample.

**Method B, from physics (cross-check).** Potential output = farm capacity × power curve(100 m wind speed at the farm). Waste ≈ potential − metered output. Noisier, but completely independent of Method A.

**Bonus hidden variable, the residual.** For each Scottish zone and half hour, the residual is all non-wind generation minus local demand. Nobody publishes it per zone. When a boundary was full, residual = boundary limit − wind delivered behind it; otherwise, estimate it from NESO's day-ahead flow. The simulator needs it, and it is a second "invisible made visible" result.

**Output:** `curtailment.parquet`, one row per unit per half hour: `planned_mwh, accepted_mwh, curtailed_mwh_a, curtailed_mwh_b, metered_mwh, bid_price_gbp_mwh, zone`.

**Validation targets:** 2025 totals for Seagreen (2.7 TWh), Moray East (1.7 TWh) and Moray West (1.4 TWh) from Modo's analysis; Great Britain total around 10 TWh. Within about 20% means the reconstruction works.

## Part 2, Predict: the forecast model

**In plain English:** using tomorrow's weather forecast, expected demand and recent patterns, the model predicts how much each farm will be told to switch off in each hour tomorrow, with a range around it.

**Model:** LightGBM with a Tweedie objective (suits a number that is usually zero and sometimes large). Target: wasted MWh per farm per hour, from Part 1. Also train two quantile versions (10th and 90th percentile) to draw the uncertainty band.

**Signals** (all known by 10:00 the day before, so no leakage):

| Signal | Why it predicts waste |
| --- | --- |
| Forecast 100 m wind speed at the farm | Drives the farm's own potential output |
| Total forecast potential across all Scottish farms, per zone | Waste is a bottleneck problem: all of Scotland overfills the cables at once |
| Forecast potential north of B4 minus the B4 limit; the same for B6 | Directly measures how far over each bottleneck tomorrow will be |
| GB demand forecast | Low demand plus high wind is the classic waste hour |
| NESO day-ahead boundary limit | Planned maintenance shrinks limits and causes spikes |
| Hour, weekday, month | Daily and seasonal cycles |
| Farm capacity, trailing 30-day waste rate | Some farms sit behind tighter bottlenecks |
| Farm's median recent bid price | Cheaper bids are accepted first |
| Waste 48 hours earlier | Persistence, using only already-published data |

**Validation:** walk-forward by month over the last 6 months; never shuffle rows.

**Baselines it must beat:** persistence (same hour two days ago), climatology (the farm's average for that hour and month), and a one-line wind rule (waste when Scottish forecast potential passes a fitted threshold).

**Metrics shown:** MAE in MWh per farm-hour, precision and recall on waste events over 10 MWh, error on monthly totals, and a SHAP chart of the top signals.

**Live mode:** on judging day the server pulls the newest Open-Meteo forecast every hour and re-predicts, so "Tomorrow" is genuinely live.

## Part 3, Decide automatically: the battery dispatcher

This is the HRT fix: a program, not a person, turns predictions into decisions, and is scored in pounds and MWh against what really happened.

**In plain English:** every evening the dispatcher reads tomorrow's waste forecast and price expectations, then writes a timetable for each battery: "empty yourself 14:00–16:00 because prices are high, keep room free overnight, soak up wasted wind 02:00–05:00." During the day it recalculates every half hour as real data arrives, like a sat-nav rerouting.

**How it decides (two versions, both built):**

1. **Rule-based dispatcher** (build first, about 1 hour): charge whenever forecast waste at its farm exceeds a threshold; discharge in the day's highest-price hours when the boundary has room; empty itself ahead of a predicted waste spike.
2. **Optimising dispatcher** (the impressive one, about 3 hours): for each battery and each day, solve a small linear programme over 48 half-hours with `scipy.optimize.linprog`. It chooses charge and discharge amounts to maximise captured waste × its value + discharge × price, subject to battery size, power, efficiency and boundary headroom. Each solve takes milliseconds.

**Real-time re-planning:** every 30 minutes, take the newest accepted bids (Elexon's latest-acceptances endpoint) and the newest weather run, then re-solve the rest of the day from the battery's current charge. This is "model predictive control": plan ahead, act on the next step only, re-plan.

**Price expectation:** the average market price for that half hour over the previous 7 days. Simple, leak-free, and easy to explain. A price model is a stretch goal.

**Scorecard** (four batteries replayed over the last 6 months):

| Battery | Plans with | Purpose |
| --- | --- | --- |
| Do nothing | — | Zero line |
| Reactive | Only what is happening now | Represents a battery with no forecast |
| **Our dispatcher** | Our forecast | The product |
| Perfect hindsight | What actually happened | The ceiling no real battery can beat |

Reported: MWh of waste captured, £ revenue, £ of switch-off payments avoided, and the **capture ratio** = our dispatcher's £ ÷ perfect hindsight's £. "Our forecast earns X% of the theoretical maximum, vs Y% for a battery without it" is the HRT headline.

**Outputs:** `schedules.parquet` (battery, half-hour, action, MW, state of charge, reason) and a decision log the dashboard can replay. Every decision carries a one-line reason, such as "charging: 180 MWh waste forecast behind B4".

## Part 4, Simulate: the grid engine and 13 levers

**In plain English:** a simplified copy of the Scottish grid that replays every half hour since January 2024. Change anything (a cable, a battery, the weather, demand) and it recalculates how much wind would have been wasted, what it would have cost and how much CO₂ it would have caused.

**The engine, step by step, for each half hour:**

1. **Potential wind** for every farm: its planned output, or the power curve applied to (possibly changed) wind speed.
2. **Zone surplus:** potential wind in North Scotland and South Scotland, plus each zone's residual from Part 1.
3. **Batteries and electrolysers** in each zone absorb what would otherwise be wasted, following the Part 3 dispatcher.
4. **Push power south:** North Scotland's surplus crosses B4 up to its limit; South Scotland's surplus plus what crossed B4 then crosses B6 up to its limit. Subsea links carry extra power directly to England.
5. **Waste** is anything over a limit. Excess at B4 comes from North Scotland farms; excess at B6 from any Scottish farm; within each group, cheapest bids first.
6. **Cost it:** switch-off payments, replacement gas cost, CO₂.

**Calibration:** with every lever at its historical setting, the engine must reproduce Part 1's reconstructed waste. Show the match on screen; it is why judges should trust every scenario.

**Implementation:** one NumPy function `simulate(scenario)`, looping over about 33,000 half-hours (needed because batteries carry charge forward). Well under a second for 50 farms; add Numba if not. The website sends lever settings to `POST /api/scenario`; results are cached by a fingerprint of the settings, and sliders wait 300 ms after you stop moving them.

**The 13 levers:**

| # | Lever | In plain English | Control | How it works in code | Tier |
| --- | --- | --- | --- | --- | --- |
| 1 | B4 capacity | Widen or narrow the bridge between North and South Scotland | Historical, fixed MW, or historical + X MW (up to +10 GW) | Replace or offset the B4 limit series before the run | Core |
| 2 | B6 capacity | Widen or narrow the Scotland–England bridge | Same as B4 | Same, on the B6 series | Core |
| 3 | Subsea link | Add an undersea cable straight to England, like the planned Eastern Green Links (check current specs before quoting) | Start zone, GW, availability % | Extra export capacity from its zone that skips both boundaries; used before the boundary | Full |
| 4 | Batteries | Place batteries that soak up waste and sell it later | Click map; MW, MWh, efficiency 80–95%, dispatcher type | Absorb waste before curtailment; discharge only with boundary headroom, per Part 3 | Core |
| 5 | Electrolysers | Turn wasted power into hydrogen fuel | Click map; MW, minimum running level | Like a battery that only charges; hydrogen kg = MWh ÷ 0.052. Report how often it sits idle | Stretch |
| 6 | Scottish demand growth | More heat pumps, electric cars, data centres in Scotland | GW of each, per zone | Each has a daily shape (heat pumps follow cold weather from Open-Meteo temperature; cars charge evenings; data centres flat), subtracted from the residual | Full |
| 7 | Wind fleet | Add, remove or resize wind farms | Toggle farms; click to add (MW, onshore/offshore); scale ±50% | New farm: MW × power curve(wind at that spot) | Stretch |
| 8 | Weather | Replay any storm, or imagine a windier year | Pick a day or week; scale wind ±30% | Scale *wind speed*, then rerun the power curve (output rises steeply with wind, then caps) | Core |
| 9 | Gas price and carbon price | What replacing wasted wind with gas costs | Pence per therm; £ per tonne CO₂ | Replacement £/MWh = (gas p/therm ÷ 2.93) ÷ 0.5 efficiency + carbon £/t × 0.37 t/MWh | Full |
| 10 | Switch-off price | What wind farms are paid to stop | Multiplier on historical bids, or one flat price | Payments = waste MWh × adjusted price; also changes which farms are switched off first | Full |
| 11 | Carbon intensity | How dirty the replacement power is | t CO₂ per MWh (0.37 default for gas) | CO₂ = waste MWh × intensity | Full |
| 12 | Time window | Look at one storm or a whole year | Date range; play button | Engine always runs the full period so batteries arrive realistically charged; results sliced to the window | Core |
| 13 | Forecast quality | What our prediction is worth | No forecast / our forecast / perfect hindsight | Switches what the Part 3 dispatcher plans with; the gap = the £ value of our model | Core |

**Every scenario reports, next to the historical baseline:** TWh wasted (split by B4 and B6), £ switch-off payments, £ replacement gas, tonnes CO₂, homes-equivalent, battery and electrolyser results, and a per-farm table.

**Sanity tests that double as demo moments:** infinite B4 and B6 → waste near zero; no batteries → exactly the baseline; wind at 0% → no waste; historical settings → matches Part 1.

## Part 5, Show: the dashboard

One full-screen map of Scotland with a side panel of five tabs; every number on screen comes from precomputed files or a sub-second simulation.

**Stack:** FastAPI server; one `index.html` with MapLibre GL JS (map) and Chart.js (charts), loaded from a CDN. Fallback if the frontend is behind at hour 18: Streamlit.

**Intro (two screens, Esc to skip):** "In 2025 Britain switched off about 10 TWh of wind, almost all of it in Scotland." → "The yearly total is known. Where, when and why is not. We rebuilt it." → Enter map.

**Map layers:**

| Layer | What you see | Default |
| --- | --- | --- |
| Wind farms | Circle size = wasted MWh in the chosen window; colour = share of potential wasted (grey 0% to deep orange 50%+) | On |
| Boundaries | Dashed B4 and B6 lines, thicker and redder the more often they were full; labelled "approximate" | On |
| Wind | Arrows from the 100 m forecast for the chosen hour | Off |
| Batteries and electrolysers | Icons where the user placed them, ringed gold for the top-10 recommended sites | Off until used |
| Subsea links | A curved line to England when lever 3 is on | Off |

**Panel tabs:**

1. **Reveal:** date slider and play button; Scotland totals (TWh, £, homes-equivalent); click a farm to see planned vs actual with the wasted gap shaded, and Method A vs Method B.
2. **Predict:** tomorrow's forecast per farm with its range; model vs baselines chart; SHAP signals chart.
3. **Autopilot:** the dispatcher's schedule for tomorrow as a timeline per battery, its decision log with reasons, and the four-battery scorecard. A "live" badge with the last update time.
4. **Simulate:** all 13 levers, grouped (Grid, Storage, Demand and fleet, Weather, Economics), with results shown as change vs history. A "Reset to history" button.
5. **Method:** the calibration match, assumptions in plain language, data sources.

**Honesty labels:** estimated numbers carry an "estimated" tag, every live number shows its timestamp, and stale data says so.

## Validation and tests

A silent bug in Part 1 would poison everything after it, so these run as `pytest` on every pull request (GitHub Actions does it automatically, see GitHub setup).

**Part 1, Reveal:**

- [ ] Waste is never negative and never above planned output.
- [ ] Planned output never exceeds farm capacity × 0.5 h; if it does, the unit mapping is wrong.
- [ ] Toy case: planned flat 100 MW, turned down to 40 MW for 15 minutes → exactly 15 MWh.
- [ ] Method A and Method B correlate above 0.6 on daily totals for the five biggest farms.
- [ ] 2025 totals for Seagreen, Moray East and Moray West within 20% of Modo's figures.

**Part 2, Predict:**

- [ ] No signal uses data timestamped after the forecast time (an assert in the feature code).
- [ ] The model beats all three baselines walk-forward; if not, say so and show where.

**Part 3, Dispatcher:**

- [ ] Charge level always between 0 and capacity; energy out ≤ energy in × efficiency.
- [ ] Perfect hindsight ≥ our dispatcher ≥ do-nothing, in £, every month.
- [ ] The optimiser and rule-based versions agree on a simple hand-made day.

**Part 4, Simulator:**

- [ ] Historical settings reproduce Part 1 waste (report the error).
- [ ] Infinite boundaries → waste near zero; no batteries → identical to baseline; wind 0% → zero waste.
- [ ] Raising a boundary never increases waste.

**Demo checks:** a fresh clone runs with `make serve` from the released data files; live mode falls back to a labelled cached forecast if the internet drops.

## Scope tiers

Build in three layers so there is always a working demo: Core is a complete, winnable project on its own; Full is the "above and beyond" version; Stretch is only if time is left.

| Tier | What's in it | Why it matters |
| --- | --- | --- |
| **Core** (must finish) | Data fetch; Method A reconstruction + tests; Method B on 5 farms; forecast model vs 3 baselines; rule-based dispatcher + scorecard; simulator with levers 1, 2, 4, 8, 12, 13; map with Reveal, Predict and Simulate tabs | Already covers reveal, predict, automated decide and simulate. Meets every line of both tracks |
| **Full** (aim for) | Optimising dispatcher; live re-planning every 30 min; residual from NESO flows; SHAP + uncertainty band + live forecast; levers 3, 6, 9, 10, 11; Autopilot tab; polish | The "wow": live autopilot, 11 levers, quant-grade scoring |
| **Stretch** (bonus) | Levers 5 and 7 (electrolysers, fleet editing); a price forecast model; demo video | Makes the simulator feel unlimited |

**The rule:** nobody starts a Full item until Core works end to end, and nobody starts Stretch until Full is merged. Twelve sliders that move nothing lose to six that work.

## Time estimate

Core is about 37 person-hours of work and Full about 48. HackTheBurgh XIII is a 24-hour event on 7–8 November 2026 ([event listing](https://createwith.com/event/edinburgh-hacktheburgh-xiii-nov-2026)), which gives two people about 44 working hours with 2 hours' rest each. So Core is safe, most of Full is reachable, and Stretch happens only if everything goes smoothly.

**Effort per task** (hours of one person steering AI agents, including review and fixes):

| Task | Owner | Tier | Hours |
| --- | --- | --- | --- |
| Repo, CLAUDE.md, contract, agent setup | Both | Core | 2 (1 each) |
| Fetch Elexon, NESO, Open-Meteo; cache; map units to farms | A | Core | 4 |
| Reconstruction Methods A and B, tests, validation vs Modo | A | Core | 4 |
| Simple residual | A | Core | 1 |
| Forecast model, baselines, walk-forward | A | Core | 3 |
| Rule-based dispatcher and four-battery scorecard | A | Core | 3 |
| FastAPI server and endpoints | B | Core | 1.5 |
| Map, Reveal and Predict tabs (fake data first) | B | Core | 4.5 |
| Simulator engine and calibration | B | Core | 4 |
| Core levers 1, 2, 4, 8, 12, 13 and Simulate tab | B | Core | 3 |
| Autopilot tab | B | Core | 3 |
| Pitch, Devpost, rehearsal | Both | Core | 4 (2 each) |
| **Core total** |  |  | **37** |
| Optimising dispatcher and live re-planning | A | Full | 4 |
| SHAP, uncertainty band, live forecast, residual from NESO flows | A | Full | 2 |
| Levers 3, 6, 9, 10, 11 and polish | B | Full | 4.5 |
| **Full total** |  |  | **47.5** |
| Levers 5 and 7, price model, demo video | Either | Stretch | 5 |

&#91;embedded content: build plan · 2 people, 24 hours, 5 gates\]

If you start late or lose time, cut from the bottom of the Full list first, never from Core. The real bottleneck is Claude usage, not hours: expect each person to hit the 5-hour limit at least once, which is when Gemini and the non-coding work (pitch, Devpost, testing by hand) fill the gap. Note the estimate assumes you start from zero code; anything already built moves the gates earlier.

## GitHub repository setup

GitHub is the shared brain: code lives there, tasks live there as Issues, and both people's agents read and write the same Issues, so nobody has to relay messages by hand. Setup takes about 30 minutes; do it together at the start.

**1. Create the repository** (Person B, who owns setup):

```bash
gh auth login
gh repo create wasted-wind --private --clone --add-readme
cd wasted-wind
```

Add Person A under Settings → Collaborators. Make it public before submitting to Devpost.

**2. Folder layout:**

```
wasted-wind/
  CLAUDE.md            shared instructions every agent reads
  GEMINI.md            one line: "Read and follow CLAUDE.md"
  docs/CONTRACT.md     table formats and API shapes both sides build against
  docs/samples/        one real response from each Elexon and NESO endpoint
  pipeline/            fetch, reconstruct, residual, features, train
  dispatch/            rule-based and optimising dispatchers, scorecard
  sim/                 simulate(scenario) and the levers
  app/                 server.py, static/index.html, static/app.js, fixtures/
  reference/farms.csv  unit id, name, lat, lon, capacity, zone
  tests/
  data/raw/            ignored by git
  data/processed/      shared through Releases, not committed
  .claude/agents/      subagent definitions (see AI agent setup)
  .github/workflows/   automatic tests
  Makefile             make data | make model | make sim | make serve
```

**3. Labels and task board:**

```bash
gh label create "owner:A-data" --color 1f77b4
gh label create "owner:B-app" --color ff7f0e
gh label create "agent:gemini" --color 2ca02c
gh label create "tier:core" --color d62728
gh label create "tier:full" --color 9467bd
gh label create "needs-human" --color 000000
```

Create one Issue per task in Time estimate, each with an owner and tier label. Turn on a GitHub Project board (Todo, In progress, Done) so you both see progress at a glance.

**4. Branches and merging:** one branch per Issue, named like `a/12-reconstruct`. Merge to `main` through a pull request when tests pass. Never push straight to `main`; it is what you demo from.

**5. Automatic tests** in `.github/workflows/tests.yml`:

```yaml
name: tests
on: [pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt
      - run: pytest -q
```

**6. Sharing data without bloating the repo:** raw downloads stay on Person A's laptop. Processed files go up as a Release, and Person B pulls them:

```bash
# Person A
gh release create data-v1 data/processed/*.parquet --title "Processed data v1"
# Person B
gh release download data-v1 -D data/processed
```

Bump to `data-v2` whenever the reconstruction changes, and post the new tag in the Issue.

**7. Optional GitHub bots:**

- **Claude on GitHub:** in Claude Code run `/install-github-app`, choose a subscription token, and you can tag `@claude` in an Issue or PR to have it work there. It uses the Pro allowance of whoever created the token ([Claude Code docs](https://code.claude.com/docs/en/github-actions?asuniq=7b2efe36)). Useful overnight; skip it if limits get tight.
- **Gemini Code Assist on GitHub:** a separate GitHub app that reviews pull requests, with its own daily quota that doesn't count against your plans ([Google quotas page](https://developers.google.com/gemini-code-assist/resources/quotas)). A free second reviewer, if it is still offered to personal accounts after Google's 2026 changes; check when installing.

## AI agent setup and workflow

Each person drives one Claude Code agent on their own Pro plan; Gemini is a helper for cheap, bulk jobs; GitHub Issues are how the two sides talk.

**What your plans actually give you (checked today):**

| Plan | Gives you | Watch out for |
| --- | --- | --- |
| Claude Pro × 2 | Claude Code in the terminal and VS Code. One allowance shared between claude.ai chat and Claude Code, with a 5-hour rolling window and a weekly cap ([Claude Help Center](https://support.claude.com/en/articles/11145838)) | Chatting on claude.ai eats your coding allowance. If an `ANTHROPIC_API_KEY` variable is set on your laptop, Claude Code bills the API instead of your plan; unset it |
| Gemini Plus | The Gemini web app with higher limits and the Pro model | **Does not unlock Gemini CLI.** Google AI Plus was never supported, and since 18 June 2026 personal Google accounts can't sign in to Gemini CLI at all ([Vibecode guide, Nov 2026](https://www.vibecodeapp.com/docs/guides/gemini-cli)) |
| Free Gemini API key (Google AI Studio) | Gemini CLI as a coding agent: 250 requests a day, Flash models only | Weaker than Claude; use it for simple jobs. Each of you can make a key on your own Google account |

**So: don't buy the second Gemini Plus plan.** It adds no coding capacity. Use the existing one for research and writing in the browser, and give each laptop a free API key for Gemini CLI.

**Who does what:**

| Worker | Job |
| --- | --- |
| Person A + Claude agent "data" | Fetching, reconstruction, residual, model, dispatcher |
| Person B + Claude agent "app" | Repo setup, server, simulator and levers, dashboard |
| Gemini CLI (either laptop) | Boilerplate, test fixtures, docstrings, summarising long API docs, first drafts of CSS |
| Gemini web app (Plus) | Reading Elexon/NESO docs, explaining concepts, Devpost and pitch drafts. Keeps those chats off your Claude allowance |
| Claude on GitHub (optional) | Small overnight Issues tagged `@claude` |

**Setup on each laptop (20 minutes):**

1. Install VS Code, then the **Claude Code** extension from the marketplace. Open a terminal and run `claude`, then sign in with your Pro account. Run `echo $ANTHROPIC_API_KEY`; if anything prints, unset it.
2. Install Gemini CLI: `npm install -g @google/gemini-cli`. Create a key at Google AI Studio and put `GEMINI_API_KEY="..."` in `~/.gemini/.env`.
3. Make Gemini read the same rules as Claude: either keep `GEMINI.md` as the single line "Read and follow CLAUDE.md", or add `"context": {"fileName": ["CLAUDE.md", "GEMINI.md"]}` to `~/.gemini/settings.json`.
4. Useful VS Code extensions: Python and Pylance, Jupyter (quick plots), Data Wrangler (opens parquet files), GitHub Pull Requests (review PRs in the editor), GitLens (see who changed what), Live Server (preview `index.html`), Ruff (catches Python errors as you type).
5. Stop agents reading huge data files, which burns allowance fast. In `.claude/settings.json`: `{"permissions": {"deny": ["Read(./data/**)"]}}`. Agents should inspect data with small scripts (`df.head()`, `df.describe()`), never by opening files.

**CLAUDE.md** (commit this in the first 30 minutes; every Claude and Gemini session reads it):

```markdown
# Wasted Wind — rules for all agents
Project: reconstruct, forecast, dispatch and simulate Scottish wind curtailment.
Brief: see docs/BRIEF.md. Data formats and API shapes: docs/CONTRACT.md (never change without a PR).

## How we work
- Start of session: `gh issue list --label <your owner label> --state open`. Pick the oldest core-tier issue.
- Comment "Starting" on the issue. Work on branch <a|b>/<issue-number>-<slug>.
- Write the test first, then the code. Run `pytest -q` before every commit.
- Finish: open a PR with "Closes #N" and a 3-line summary. Then comment on the issue.
- Need something from the other side? Open an issue with their owner label, the exact
  file/function/columns you need, and an example. Never edit their folders.
- Stuck for 2 attempts? Label the issue `needs-human` and stop.

## Rules
- Parse API data against real samples in docs/samples/. Never invent field names.
- Times are UTC. Energy in MWh, power in MW. Say which in every column name.
- Never read files in data/. Inspect data with short scripts.
- Cheap bulk jobs (fixtures, docstrings, summarising docs): run `tools/ask_gemini.sh "<task>"`
  and review its output before using it.
```

**How the two agents talk, via GitHub:**

1. Humans write Issues for every task, labelled with an owner and tier.
2. Each agent pulls only its own Issues, works on its own branch and folders, and reports back as an Issue comment and a PR.
3. Cross-team needs become new Issues with the other side's label, for example: "B needs `residual.parquet` with columns zone, period\_start, residual\_mw."
4. `docs/CONTRACT.md` is the only shared agreement; both humans approve any change to it.
5. Person B builds against fake rows in `app/fixtures/` until Person A releases real data, so neither ever waits.

Both Claude agents run on different accounts, so they can't message each other directly; GitHub is the shared channel, and every message is saved for the Devpost story.

**Running more than one agent on one laptop:** Claude Code can start a session in its own git worktree so parallel sessions never edit the same files ([Claude Code docs](https://code.claude.com/docs/en/worktrees)). Example: Person A's main session builds the model while a second session in a worktree writes tests. Parallel sessions multiply usage, so do this only when you have allowance spare.

**Subagents** (helpers inside one session, defined as files in `.claude/agents/`). Two worth adding:

```markdown
---
name: quant-reviewer
description: Skeptical reviewer for leakage, unit errors and overclaiming. Use before merging model, dispatcher or simulator code.
tools: Read, Grep, Glob, Bash
---
You are a quant researcher at a trading firm judging a hackathon. Find data leakage,
MW/MWh and half-hour/hour mix-ups, untested assumptions and claims the numbers don't support.
Report problems as a numbered list with file and line. Do not edit files.
```

and a `test-writer` that only writes pytest files for a named function.

**Delegating to Gemini when Claude runs low:** add `tools/ask_gemini.sh`:

```bash
#!/usr/bin/env bash
# Runs Gemini CLI once, without a chat window, and prints the answer.
gemini -p "$1"
```

The CLAUDE.md rule above tells Claude to send cheap, bulk jobs there, saving Claude for reasoning-heavy work. Claude Code's own docs say other tools join a Claude workflow best as an MCP server; the script is the 2-minute version of that ([Claude Code docs](https://code.claude.com/docs/en/agents?utm_campaignId=18788801)). Claude doesn't sense its own remaining allowance, so you decide when to switch:

- Claude Code warns you as you approach a limit. When it does, ask it to write the current state and next steps as a comment on the Issue.
- Open Gemini CLI in the same folder (`gemini`). It reads the same rules, so tell it: "Continue issue #N from the last comment."
- Keep Gemini on well-specified, low-risk work: tests, fixtures, CSS, docstrings. Leave the reconstruction and simulator maths for when Claude resets.

**Habits that stretch your allowance:**

- Use the default Sonnet model in Claude Code, not Opus; Opus uses limits much faster.
- `/clear` between unrelated tasks; `/compact` when a session gets long.
- Point agents at files and line numbers instead of pasting code.
- Ask for a plan first on big tasks, approve it, then let it build. Rewrites cost far more than plans.
- Stagger the heaviest agent work so both of you aren't throttled at the same moment.
- Do research and writing in the Gemini web app, not claude.ai chat.

## Pitch plan

One demo, two running orders. Pitch the idea to the HRT and G-Research reps in one sentence early on, and adjust if either reacts coolly.

**HRT judges (3 minutes):**

1. The problem in one number: about 10 TWh of wind switched off in 2025, paid for twice. (20 s)
2. Signals: market bids + weather + grid limits, about 50 million data points per series. (20 s)
3. **The model:** forecast vs three baselines, SHAP chart of top signals. (40 s)
4. **The autopilot, live:** tomorrow's battery schedule with reasons, re-planning on the latest data; the scorecard showing what share of perfect hindsight our forecast captures vs a battery without it. (60 s)
5. The simulator as the decision layer: "what if B6 were 2 GW bigger, or we put 500 MW of batteries here?" (30 s)
6. Honesty: limitations in one breath. (10 s)

**G-Research judges (3 minutes):**

1. "The yearly totals are known. Where, when and why are not." (15 s)
2. **The reveal:** one storm day, planned vs actual with the gap shaded, Method A and Method B agreeing. (60 s)
3. The second hidden quantity: Scotland's net position behind each bottleneck, and that B4 binds more than the border. (30 s)
4. What we can do with it: forecast, autopilot, simulator. (60 s)
5. How others could reuse the pipeline. (15 s)

**Devpost draft headline:** "Wasted Wind: we revealed when, where and why Scotland throws away wind power, predict it a day ahead, let an autopilot run batteries to catch it, and let you test the fixes." Use the Gemini web app to draft the full write-up from this brief, then rewrite it in your own voice.

## Limitations and sources

Say these out loud before a judge asks; it reads as rigour.

- **Three zones is a simplification.** England and Wales is treated as one zone, though there are further bottlenecks south of the border.
- **No outage data in the model yet,** beyond what NESO's day-ahead limits already reflect.
- **The constraint flag on bids is imperfect,** so some ordinary balancing may be counted as waste, or the reverse.
- **Unit mapping is hand-checked** for the largest farms only; small farms are left out.
- **Forecast history starts January 2024,** when archived day-ahead weather forecasts begin.
- **The dispatcher and simulator ignore** battery costs, grid connection queues, and the effect of new batteries on prices.
- **Tool facts change fast.** The AI plan details here were checked on 7 November 2026.

**Sources:** [Elexon developer portal](https://developer.data.elexon.co.uk/) · [Elexon B1610 guide](https://www.elexon.co.uk/article/bsc-insight-how-to-get-more-detailed-metered-volume-data-from-our-insights-solution/) · [Elexon acceptances spec](https://apis.io/apis/elexon/elexon-bid-offer-acceptances-api/) · [NESO day-ahead constraint limits](https://neso.energy/data-portal/day-ahead-constraint-flows-and-limits/day_ahead_constraint_flows_and_limits) · [NESO congestion data](https://www.neso.energy/data-portal/operational-transparency-forum-network-congestion-data/network_congestion_forecast_and_actual_data) · [Open-Meteo run APIs](https://openmeteo.substack.com/p/single-runs-api) · [Montel 2025 figures](https://sustainabilitymag.com/news/infrastructure-keep-pace-renewables-supply) · [Modo Energy on Scottish constraints](https://modoenergy.com/research/en/gb-great-britain-battery-energy-storage-curtailment-wind-constraint-scotland-costs) · [Claude Code with Pro](https://support.claude.com/en/articles/11145838) · [Claude Code worktrees](https://code.claude.com/docs/en/worktrees) · [Claude Code GitHub Actions](https://code.claude.com/docs/en/github-actions?asuniq=7b2efe36) · [Gemini CLI access in 2026](https://www.vibecodeapp.com/docs/guides/gemini-cli) · [Gemini Code Assist quotas](https://developers.google.com/gemini-code-assist/resources/quotas) · [HackTheBurgh XIII](https://createwith.com/event/edinburgh-hacktheburgh-xiii-nov-2026)
