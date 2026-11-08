# Windfall

**Wasted wind, turned into profit.** The 33 Scottish wind farms we track were paid £738M to switch off since 2024. Windfall predicts it days ahead and turns it into battery profit.

Built at HackTheBurgh XIII (7–8 November 2026) by [parlink](https://github.com/parlink) and [aradhyak0](https://github.com/aradhyak0).

## Overview

Windfall is a live web app that reveals how much Scottish wind power is thrown away, predicts when it will happen up to six days ahead, and automatically runs batteries to catch it and sell it later. It's built entirely from free public grid, market and weather data, and every number is checked against what really happened.

On screen it's one map of Scotland with a panel beside it. Every tracked wind farm is a turbine sitting on a circle: the bigger the circle, the more wind that farm was paid to throw away. Two dashed red lines mark the power-line bottlenecks, **B4** (northern to southern Scotland) and **B6** (Scotland to England), each labelled with how often it was full. The panel tells the story in four tabs: **History** (what was wasted), **Forecast** (what's coming), **Batteries** (an autopilot that stores it and sells it later) and **Simulate** (test fixes).

## The problem

Scotland has more wind farms than its power lines can carry south to England. When the lines are full, the grid operator **pays wind farms to switch off**, then pays gas plants in England to replace the power.

Since January 2024, across the 33 Scottish wind farms we track:

| | |
|---|---|
| Clean power thrown away | **24.5 TWh**, enough for 9 million homes for a year |
| Paid to switch farms off | **£738M** |
| Gas burned instead | **£1.8bn**, emitting **9.1 Mt of CO₂** |

Two days after Storm Boelo (24–25 October 2026), these farms lost **89%** of their possible output in a single day: **115 GWh** wasted, £3.1M in switch-off payments and £8.4M of replacement gas. The yearly totals are published. When, where and why it happens, half hour by half hour, are not, and nobody forecasts it.

## Our solution

If you know today how much wind will be wasted tomorrow, you can act on it. Windfall does three things:

1. **Reveal.** We rebuilt the waste for every farm, every half hour since 2024, from the grid's own switch-off records. Then we cross-checked it a second way, using wind speed and each turbine's power curve.
2. **Predict.** A machine-learning model (LightGBM, 17 signals) forecasts each farm's waste up to 6 days ahead, with a likely range for tomorrow. It was tested only on months it had never seen, using only data available at 10:00 the day before. Its error is **2.3× smaller** than "same as two days ago" (about a fifth lower than the best simple rule), and it saw **82%** of real waste events coming. For 27 October it forecast 101 GWh, and 115 GWh happened.
3. **Act.** An autopilot plans each battery's day, choosing every half hour whether to **store** wasted wind, **wait**, or **sell** when the lines have room. Replayed against reality on months held back from tuning, a battery using our forecast earned **about 27% more** than the same battery without it.

## Features

- **Live map of Scotland:** every wind farm shown as a turbine sized by the wind it wasted. It also shows the two bottleneck power lines and how often each was full, plus hourly wind arrows. Choose satellite, light, dark or detailed views.
- **History:** any day, week, month or year since 2024, showing wind wasted, money paid, gas burned, CO₂ and which line was full. Click any farm to see its half hours.
- **Forecast:** the next six days for every farm, its accuracy against simple baselines, and the signals it relies on (SHAP). You can also look back at any day since July 2024, forecast as it would have been at the time (a walk-forward replay using only earlier data).
- **Batteries:** four real-sized batteries run by the autopilot. Each decision is shown with its price and reason, can be replayed half hour by half hour, and is re-planned live every half hour.
- **Simulate:** a grid simulator with 15 controls:
  - bigger power lines or an undersea cable;
  - batteries placed on the map;
  - heat pumps, electric cars and data centres;
  - gas and carbon prices;
  - windier weather.

  Waste, cost and CO₂ recompute instantly.
- **Explained everywhere:** intro pages, two guided tours, and a grey ⓘ beside every number saying where it comes from.

## Run locally

You need **Python 3.11 or newer** (we build with 3.13; CI tests 3.11), **git**, Chrome or Edge, and, for the data download, the **GitHub CLI** logged in (`gh auth login`). The History month and week pickers use Chromium's native calendars.

```sh
git clone https://github.com/parlink/wasted-wind.git
cd wasted-wind
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
make fetch-data                    # latest processed data and saved forecast models
make serve                         # http://localhost:8000
```

Open **http://localhost:8000**. On Windows without `gh` on your PATH: `make fetch-data GH="/c/Program Files/GitHub CLI/gh.exe"`.

**Without `make`** (e.g. on Windows), run the same steps by hand with the virtual environment active:

```sh
gh release download -D data/processed --clobber -p "*"          # = make fetch-data, part 1
python -c "import zipfile; zipfile.ZipFile('data/processed/models.zip').extractall('data/processed')"   # part 2
python -m uvicorn app.server:app --port 8000                     # = make serve
```

**No GitHub CLI?** Download every file from the [latest release](https://github.com/parlink/wasted-wind/releases/latest) into `data/processed/` and unzip `models.zip` there.

**Live mode.** The forecast refreshes every hour and the batteries re-plan every half hour only while these two run, each in its own terminal next to the server. Both need internet access.

```sh
python -m pipeline.live_forecast --every 60    # tomorrow and the next 5 days, from the newest weather
python -m dispatch.live --every 30             # the batteries' live plans
```

Without them the app shows the last saved forecast and plans; the forecast is marked "stale" after 36 hours.

Without any processed data, the app still starts, using the small sample data in `app/fixtures/`. A yellow banner says which parts are sample data. Set `WW_DATA_SOURCE=processed` to fail instead of falling back, or `WW_DATA_SOURCE=fixtures` to force the samples.

No API keys are needed. Every source below is public.

## Map

The map opens on **satellite** view. The dropdown at the top left switches between **Satellite**, **Light map**, **Detailed map** and **Dark map**. Farms, power lines, wind arrows and battery markers are kept when you switch.

- **Turbines:** each wind farm is a turbine on a circle. Circle size is the wind wasted in the selected period, and colour is the share of the farm's possible output that was wasted. The rotor spins faster when the farm is generating more. Markers grow with zoom until about zoom 12, then stay the same size. Click a farm to open its day.
- **Bottlenecks:** B4 and B6 are drawn as dashed red lines, each labelled with the share of half hours it was full in the selected period.
- **Wind:** the **Wind** button shows arrows at every farm for one day. They show 100 m wind speed and direction from Open-Meteo, with an hour slider and play. They need one day selected, either on History (Day) or on the Forecast tab.
- **Colours:** what we produce (forecasts, your scenarios, battery actions) is **teal**. Wind that was really wasted is **raspberry**. Baselines are grey.

## Interface

The panel has five tabs: **1 History**, **2 Forecast**, **3 Batteries**, **4 Simulate** and **Method**. The line under the title says what the project is, and **How it works and the data** opens the intro. The badge at the top right shows the data range ("Real grid data · to 6 Nov 2026"); hover it for the release name.

**Grey ⓘ icons:** every number has one beside its label. Hover or tab to it to see where the number comes from and how it is computed.

### History
- **Periods:** **All · Year · Month · Week · Day** pick the period.
  - Year is a list of years.
  - Month and Week use the browser's month and week calendars.
  - Day is a date box.
  - All, or any typed range, shows From–To boxes.
- **Stepping:** ‹ › step to the previous or next period, and ▶ plays through them.
- **The headline:** wasted wind for the period, with four tiles: £ paid to switch off, gas burned instead, CO₂ from that gas, and homes it could have powered. Numbers count up when the period changes.
- **"Which pinch point was full":** splits the waste between B4 and B6, marked *approximate*.
- **Farms:** listed by wind wasted. Clicking one, here or on the map, opens its half-hour chart of planned against allowed output, with both estimation methods side by side.

### Forecast
- **Six-day strip:** for all farms or the selected one. Days 1–3 are forecasts; days 4–6 are dashed and labelled *outlook*. Click a day to redraw everything below.
- **Tomorrow:** hour by hour, with a likely range (10th–90th percentile, tomorrow only), plus a farm-by-farm table.
- **How accurate it is:** our model against three simple baselines, then error by days ahead.
- **Look back at any past day:** any day since July 2024, with the forecast as it would have been made at 10:00 the day before, next to what really happened and what the batteries did. It's a walk-forward replay that uses only earlier data. **Biggest storm** jumps to 27 October 2026.
- **What the forecast pays attention to:** a SHAP ranking of the signals.

### Batteries
- **The headline:** how much more a battery earns with our forecast than without one (**+27%**), measured on Aug–Oct 2026, months never used to tune the planner, with the likely range underneath (0–61%). Bars compare *knows the future* (100%), *with our forecast* (35%) and *without a forecast* (27%).
- **Show the numbers:** opens the full table: wind stored, £ earned and switch-off payments saved.
- **Watch one battery's day:** pick a date (‹ ›), a battery and a plan (with our forecast, without, or knowing the future). **More options** chooses the planner: *Best plan (optimiser)*, *Best plan, ready for extra wind* or *Simple rules*.
  - **Replay the day** fills the timeline half hour by half hour.
  - The strip and the "How full the battery is" chart show **store / wait / sell**.
  - The decision log groups identical half hours. Each row says what happened ("Sold 50 MWh at £163/MWh = £8.2k") and, smaller, the plan's reason and expected price.
  - Today's plan is marked **live** and re-plans every half hour.

### Simulate
The simulator replays the real Jan 2024 – Nov 2026 history with your changes. With every lever untouched it reproduces the measured waste. Fifteen controls:

| Group | Controls |
| --- | --- |
| Grid pinch points | B4 and B6: *as it was*, *bigger / smaller by*, or *set to* a size. An undersea cable from north of B4 or from between B4 and B6, straight to England, in GW, drawn as a curve on the map. |
| Batteries | **Place a battery on the map**, then click anywhere in Scotland. Beside a farm it can only store that farm's waste; on the grid it can store waste from its whole zone. Each battery card sets power (MW), storage (MWh), efficiency (%), how it's connected and how it's planned. *What batteries know about tomorrow*: no forecast, our forecast, or knows the future. |
| New electricity demand | Where (north of B4 or between B4 and B6), plus heat pumps (following real temperatures), electric car charging (evening and overnight) and data centres (flat), in GW. |
| Prices | Gas price, carbon price, CO₂ per MWh of gas power, and switch-off payments (a multiplier on each farm's bid, or a flat £/MWh). These change only the £ and CO₂ figures. |
| Weather | Wind speed scale, applied through each farm's power curve. |
| Time window | The period to replay. |

Results recompute instantly:
- wasted wind and the four tiles, each with its change against reality;
- a B4/B6 comparison;
- teal circles on the map.

**Reset to what really happened** clears every lever.

### Method
How every number is made, what it was checked against, the data sources, and the limitations.

### Intro and tours
- **Intro pages:** three short pages (the problem, the solution, what's inside) open on a first visit. Reopen them with **How it works and the data**.
- **Tours:** the **▶ Tour** button plays **Tour 1** or **Tour 2**, 15 cards each, which drive the real site on screen.
  - **Presenter** mode waits for → or a clicker.
  - **Auto** mode advances on a timer.
  - **Esc** exits and restores the page.
  - Open `?tour=1` or `?tour=2` to start one directly. `?notour` skips the intro.

## Data sources

| Service | Configuration | Used for |
| --- | --- | --- |
| Elexon BMRS (`data.elexon.co.uk/bmrs/api/v1`) | None (public). | For 50 wind units at 33 farms: physical notifications (planned output), bid-offer acceptances (switch-off orders), bid-offer data (switch-off prices), B1610 metered output. GB-wide: market index price (MID), demand outturn and the national demand forecast (NDF). |
| NESO data portal | None (public). | Day-ahead constraint limits and flows on the B4 and B6 boundaries. |
| Open-Meteo archive and previous-runs APIs | None (free). | 100 m wind speed and direction and 2 m temperature at every farm. Includes weather forecasts exactly as they were issued 2–7 days earlier (a 1-day-old run isn't complete by the 10:00 issue time, so it isn't used), so backtests never use hindsight. |
| Open-Meteo forecast API | None (free). | The live forecast refresh and the map's wind arrows (the server caches each day). |
| OpenFreeMap and Esri World Imagery | None. | Map tiles (light, detailed and dark styles; satellite imagery). |

`reference/farms.csv` lists the 33 farms (50 wind units), chosen by time spent switched off.

| Setting | Default / behaviour |
| --- | --- |
| Runtime | Python 3.11+ (built with 3.13), `pip install -r requirements.txt` (pandas, LightGBM, SHAP, SciPy, FastAPI). |
| Start | `make serve`, or `python -m uvicorn app.server:app --port 8000`; open `http://localhost:8000`. |
| Data | `data/processed/` from the latest GitHub release (`data-v15`: Jan 2024 – 6 Nov 2026). Not committed to Git. |
| Data source | `WW_DATA_SOURCE=auto` (default: real data, else samples), `processed` or `fixtures`. |
| Live forecast | `python -m pipeline.live_forecast --every 60` refreshes the next six days hourly from the newest weather run. |
| Live batteries | `python -m dispatch.live --every 30` re-plans each battery every half hour and records what it executed. |
| Rebuild | Needs network access. `make data` fetches and rebuilds the waste; it downloads 2+ years of records and takes a few hours the first time. `make model` (train, price, accuracy by days ahead, battery backtest) takes about 15 minutes. |
| Tests | `make test` (pytest, about 360 tests). `node tools/tour_screenshots.js --tour 1 --url http://localhost:8000` replays a tour in headless Chrome and saves a screenshot per step. |

## How it works

- **Wasted wind.** Physical notification minus the level the grid allowed after accepted bids, for each wind unit and half hour, summed per farm. We call this Method A. Method B independently estimates the same thing from 100 m wind speed through each farm's power curve. Both appear side by side on each farm's chart.
- **Costs.**
  - Switch-off payments = waste × each farm's own bid price, a net figure (a few farms pay to switch off when power prices go negative).
  - Replacement gas = waste × (gas p/therm ÷ 2.93 ÷ 50% efficiency + carbon £/t × 0.37), about £73/MWh at 80p/therm and £50/t.
  - CO₂ = waste × 0.37 t/MWh.
  - Homes = waste ÷ 2.7 MWh a year.
- **Which line was full.** For each half hour we estimate the flow over B4 and B6 from Scottish demand and generation plus delivered wind, and compare it with NESO's limit. Waste while B4 is full counts as B4 and the rest as B6. Approximate, because the records don't say which line caused each switch-off.
- **Forecast.** LightGBM with a Tweedie loss (500 trees) predicts each farm's waste per hour from 17 signals:
  - expected wind output at the farm and north and south of B4;
  - B4 and B6 limits, and how far wind exceeds them;
  - the demand forecast;
  - the farm's switch-off price;
  - recent waste;
  - hour, weekday, month and farm.

  For tomorrow, two quantile models give the 10th–90th percentile range. There is one model per day ahead (1–6). From day 2 on there are no line limits (NESO publishes them the day before) and no range.
- **No hindsight.** The backtest is walk-forward. Each month is predicted by a model trained only on earlier months, using only what was known at 10:00 the day before. Every signal carries a timestamp, and tests fail if any was known later.
- **Accuracy.** On May–Oct 2026, 19.1 MWh average error per farm-hour, against:
  - 43.3 for "same as two days ago" (2.3× smaller);
  - 38.3 for the usual value for that hour;
  - 24.1 for a simple windy-day rule (about a fifth lower).

  82% of hours with more than 10 MWh wasted were flagged the day before. For 27 October 2026 the forecast was 101 GWh, against 115 GWh actual.
- **Batteries.** A linear program (SciPy, HiGHS solver, about 5 ms) plans 48 half hours at 10:00 the day before, from four inputs:
  - forecast waste;
  - expected price (last week's average for that half hour);
  - expected room on the line;
  - the battery's size, efficiency and state.

  Its rules: store only wind that would be wasted; sell only with room and a positive price; stay between empty and full; empty by midnight. The plan is then replayed against reality: real waste is stored when there is space, and sales are paid the real market price. The four example batteries are Seagreen and Moray East (100 MW / 200 MWh) and Viking and South Kyle (50 MW / 100 MWh), all 88% efficient.
- **Scoring.** *Knows the future* is the same optimiser run on the real waste, prices and line room, so it is the most any battery could earn. On Aug–Oct 2026, never used for tuning, batteries with our forecast earned 35% of that and without it 27%, so about 27% more. A day-block bootstrap gives a 95% range of 0–61%.
- **Simulator.** `sim/engine.py` re-allocates each half hour's waste after your changes:
  - bigger lines or a cable raise the limit;
  - extra demand absorbs wind before the bottleneck;
  - batteries store and sell under the same rules;
  - weather scales wind through each power curve.

  It reproduces history exactly with no changes. Gains north of B4 are best cases.

## Limitations

- **Coverage:** 33 Scottish farms (50 wind units), the ones most often switched off, not every farm in Scotland.
- **Which line was full** is an estimate.
- **Batteries:** an operating comparison (more revenue from the same battery), not a business case. Build cost, degradation and grid-service income aren't modelled. The +27% comes from three held-out months.
- **Price forecast:** our separate price forecast gave no detectable gain over last week's average, so the battery planner uses last week's average.
- **Simulator** results north of B4 are upper bounds. The undersea cable is assumed available 95% of the time.

## Repository

| Path | Contents |
| --- | --- |
| `pipeline/` | Fetching, waste reconstruction (`reconstruct`), line flows (`residual`), features, forecast training, live forecast |
| `dispatch/` | Battery planners (rules, optimiser, high-end band), backtest scorecard, live re-planning |
| `sim/` | The grid simulator behind Simulate |
| `app/` | FastAPI server (`server.py`), data loading, the web app (`static/`) and sample data (`fixtures/`) |
| `tests/` | The test suite |
| `tools/` | Tour screenshot replay and helper scripts |
| `docs/CONTRACT.md` | Every data file's columns and every API response shape |
| `docs/BRIEF.md` | The full project brief |
