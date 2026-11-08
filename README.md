# Windfall

**Wasted wind, turned into profit.**

> The 33 Scottish wind farms we track were paid £738M to switch off since 2024. Windfall predicts it days ahead and turns it into battery profit.

Built at HackTheBurgh XIII (7–8 November 2026) by [parlink](https://github.com/parlink) and [aradhyak0](https://github.com/aradhyak0).

---

## Overview

Windfall is a live web app that reveals how much Scottish wind power is thrown away, predicts when it will happen up to six days ahead, and automatically runs batteries to catch it and sell it later. It's built entirely from free public grid, market and weather data, and every number is checked against what really happened.

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

## How it works

```
Elexon (market) ─┐
NESO (grid)     ─┼─▶ pipeline/ ─▶ versioned data releases ─▶ app/server.py (FastAPI) ─▶ map + panel
Open-Meteo      ─┘    rebuild · forecast · battery optimiser                              (MapLibre, Chart.js)
```

| Source | What we use |
|---|---|
| **Elexon BMRS** (Britain's electricity market) | For 50 wind units at 33 farms: planned output (PN), switch-off orders (BOALF), switch-off prices (BOD), metered output (B1610). Also GB prices (MID), demand and the day-ahead demand forecast (NDF) |
| **NESO** (the grid operator) | Day-ahead limits and flows on the B4 and B6 boundaries |
| **Open-Meteo** | Wind at 100 m and temperature at every farm, including weather forecasts exactly as they were issued 2–7 days earlier (a 1-day-old run isn't complete by the 10:00 issue time, so it isn't used) |

- **Waste:** planned output minus what the grid allowed, cross-checked with wind through each farm's power curve.
- **Costs:**
  - switch-off payments = waste × each farm's own bid;
  - replacement gas ≈ £73/MWh (80p/therm at 50% efficiency, plus £50/t carbon × 0.37 t/MWh);
  - CO₂ = 0.37 t/MWh.
- **Forecast:** LightGBM, with a Tweedie loss and 10th/90th-percentile models for the range. It is trained walk-forward, month by month, with tests that no signal is used before it was known.
- **Batteries:** a linear program (SciPy/HiGHS) plans 48 half hours in about 5 ms. It is then replayed against the real waste, price and line room.

Every figure's method and limitations are in the app's **Method** tab and in [docs/BRIEF.md](docs/BRIEF.md).

## Run it

```bash
git clone https://github.com/parlink/wasted-wind.git
cd wasted-wind
pip install -r requirements.txt
make fetch-data          # latest processed data and saved models (needs the GitHub CLI, logged in)
make serve               # http://localhost:8000
```

**No GitHub CLI?** Download every file from the [latest release](https://github.com/parlink/wasted-wind/releases/latest) into `data/processed/`, unzip `models.zip` there, then run `make serve` (or `python -m uvicorn app.server:app --port 8000`). Without any data, the app still runs on small built-in sample data and says so in a yellow banner.

Without `gh` on your PATH (Windows): `make fetch-data GH="/c/Program Files/GitHub CLI/gh.exe"`. Without `make`, run the commands in the [Makefile](Makefile) directly. Use Chrome: the History month and week pickers use its native calendars.

To rebuild everything from raw data (needs network access; `make data` downloads 2+ years of records and takes a few hours the first time, `make model` about 15 minutes):

```bash
make data                # fetch from Elexon, NESO, Open-Meteo; rebuild the waste
make model               # train the forecasts, price forecast, battery backtest
make test                # run the test suite
```

## Repository

| Path | What's in it |
|---|---|
| `pipeline/` | Data fetching, waste reconstruction, features and forecast training |
| `dispatch/` | Battery planners (rules, optimiser) and the scorecard |
| `sim/` | The grid simulator behind the Simulate tab |
| `app/` | FastAPI server and the web app (`app/static/`) |
| `tests/` | The automated test suite |
| `docs/CONTRACT.md` | Data formats and API shapes shared by pipeline and app |
| `docs/BRIEF.md` | The full project brief |

## Limitations

- **Coverage:** 33 Scottish farms (50 wind units), chosen by time spent switched off, not every farm in Scotland.
- **Pinch points:** which line was full is an estimate; public records don't say which line caused each switch-off.
- **Battery results:** an operating comparison (more revenue from the same battery), not a business case. Build cost and degradation aren't included. The +27% comes from 3 held-out months, so its likely range is wide (0–61%).
- **Simulator:** gains from changes north of B4 are best cases.
