# Data and API contract

The only shared agreement between Person A (data) and Person B (app). Both sides build against
this file. **Change it only through a PR approved by both humans**, and bump the data release tag
(`data-vN`) when a file's columns change.

## Global conventions

| Thing | Rule |
| --- | --- |
| Time | All timestamps are UTC, tz-aware (`datetime64[ns, UTC]` in pandas). Never store local time. |
| Half-hour key | `period_start` = start of the settlement period in UTC. Periods are `[period_start, period_start + 30 min)`. |
| Hour key | `hour_start` = start of the hour in UTC (forecast only). |
| Settlement period | Kept for traceability as `settlement_date` (UK local date, `date`) + `settlement_period` (int 1–50; 46 or 50 on clock-change days). `period_start` is the join key, not these. |
| Units | Every numeric column ends in its unit: `_mw` (power), `_mwh` (energy), `_gbp`, `_gbp_mwh`, `_ms` (m/s), `_t` (tonnes). |
| Zones | Exactly one of `north_scotland`, `south_scotland`, `england_wales`. |
| Boundaries | `B4` (north_scotland → south_scotland), `B6` (south_scotland → england_wales). |
| IDs | `bmu_id` = Elexon BM unit ID exactly as Elexon writes it (e.g. `T_SGRWO-1`). `farm_id` = lowercase slug (e.g. `seagreen`); one farm has one or more BMUs. `battery_id` = lowercase slug. |
| Missing values | `NaN`/null, never 0 or -1. 0 means a real zero. |
| Files | Parquet written with pyarrow, no index column. Paths are relative to repo root. |

## 1. `reference/farms.csv` (A writes, committed to git)

One row per **BMU**. Hand-checked for the top 25–50 Scottish wind units.

| Column | Type | Example | Notes |
| --- | --- | --- | --- |
| `bmu_id` | str | `T_SGRWO-1` | Primary key. |
| `farm_id` | str | `seagreen` | Groups BMUs into a farm for the map. |
| `farm_name` | str | `Seagreen` | Display name. |
| `lat` | float | `56.58` | WGS84 degrees, farm centroid. |
| `lon` | float | `-1.76` | WGS84 degrees. |
| `capacity_mw` | float | `1075.0` | Capacity of **this BMU**, not the whole farm. |
| `zone` | str | `north_scotland` | See zones above. |
| `site_type` | str | `offshore` | `onshore` or `offshore`. |
| `repd_ref_id` | str, nullable | `7543` | Renewable Energy Planning Database ID, if matched. |

## 2. `data/processed/curtailment.parquet` (A writes, Part 1)

One row per `bmu_id` per `period_start`. Primary key: (`bmu_id`, `period_start`).

| Column | Type | Notes |
| --- | --- | --- |
| `bmu_id` | str | Joins to `farms.csv`. |
| `farm_id` | str | Denormalised from `farms.csv` for speed. |
| `zone` | str | Denormalised from `farms.csv`. |
| `period_start` | timestamp UTC | |
| `settlement_date` | date | Traceability only. |
| `settlement_period` | int | Traceability only. |
| `planned_mwh` | float | Integral of PN over the half hour. |
| `accepted_mwh` | float | Integral of the accepted level (BOAL, or PN where no acceptance) over the half hour. |
| `curtailed_mwh_a` | float | Method A: Σ max(0, PN − BOAL) / 60, constraint-flagged acceptances only. 0 ≤ value ≤ `planned_mwh`. |
| `potential_mwh_b` | float, nullable | Method B: capacity × power curve(100 m wind). Null where Method B isn't run. |
| `curtailed_mwh_b` | float, nullable | Method B: max(0, `potential_mwh_b` − `metered_mwh`). Null where not run (Core: 5 farms only). |
| `metered_mwh` | float, nullable | B1610 metered output. |
| `bid_price_gbp_mwh` | float, nullable | Volume-weighted price of accepted bids that period; null if no accepted bid. Usually negative for wind (farm is paid). |
| `wind_speed_100m_ms` | float, nullable | Open-Meteo **actual** (archive) 100 m wind speed at the farm, hourly value repeated for both half hours. Used by simulator lever 8. |

Invariants (tested in `tests/`): `curtailed_mwh_a >= 0`, `curtailed_mwh_a <= planned_mwh`,
`planned_mwh <= capacity_mw * 0.5 * 1.05`.

## 3. `data/processed/residual.parquet` (A writes, Part 1)

One row per Scottish `zone` per `period_start` (zones `north_scotland` and `south_scotland` only).
Primary key: (`zone`, `period_start`).

| Column | Type | Notes |
| --- | --- | --- |
| `zone` | str | `north_scotland` or `south_scotland`. |
| `period_start` | timestamp UTC | |
| `residual_mw` | float | Non-wind generation minus local demand in the zone (positive = zone exports even without wind). |
| `residual_method` | str | `binding` (the zone's farms were curtailed, so the boundary was full: flow = limit), `flow_estimate` (NESO day-ahead flow, capped at ± the limit) or `fallback` (limits/flows filled from weekly data or interpolation). Either way `residual_N = flow_B4 − wind_N` and **`residual_S = flow_B6 − wind_S − flow_B4`** (the B4 inflow is not part of the south zone, #21). |
| `boundary` | str | The boundary on this zone's southern edge: `B4` for north_scotland, `B6` for south_scotland. |
| `boundary_limit_mw` | float | NESO day-ahead limit for that boundary (weekly data as fallback). Simulator lever 1/2 baseline. |
| `boundary_flow_mw` | float, nullable | NESO day-ahead flow for that boundary. |
| `wind_delivered_mwh` | float | Σ `accepted_mwh` of farms in this zone that period. |
| `market_price_gbp_mwh` | float | GB market index price that period. GB-wide, so the **same value in both zone rows**. Used for battery revenue. |
| `gb_demand_mw` | float, nullable | GB demand outturn. Same value in both zone rows. |
| `temperature_2m_c` | float, nullable | 2 m air temperature, °C, **population-weighted over the zone's main towns** (`reference/towns.csv`), from the Open-Meteo archive: **realised** weather, for historical replays, not a forecast. Hourly spot values interpolated to half hours (:30 = mean of the hours either side). Null where the archive has no value yet (it lags ~5 days). For the simulator's heat-pump lever (BRIEF lever 6). |

## 4. `data/processed/forecast.parquet` (A writes, Part 2)

One row per `farm_id` per `hour_start` per forecast issue. Holds both the walk-forward backtest
and the live "tomorrow" forecast. Primary key: (`farm_id`, `hour_start`, `issued_at`).

| Column | Type | Notes |
| --- | --- | --- |
| `farm_id` | str | Forecast is per farm (sum of its BMUs). |
| `zone` | str | |
| `hour_start` | timestamp UTC | Hour being predicted. |
| `issued_at` | timestamp UTC | When the forecast was made. Every signal must be timestamped ≤ this. `backtest` rows: D-1 10:00 UTC. `live` rows: the refresh time (hourly), so their lead time is shorter than the backtest's. |
| `split` | str | `backtest` (walk-forward test month) or `live`. |
| `curtailed_mwh_pred` | float | Tweedie point forecast for the hour. ≥ 0. |
| `curtailed_mwh_p10` | float, nullable | 10th percentile (Full tier). The p10–p90 band always contains `curtailed_mwh_pred`. |
| `curtailed_mwh_p90` | float, nullable | 90th percentile (Full tier). |
| `curtailed_mwh_actual` | float, nullable | Σ `curtailed_mwh_a` over the hour; null for `live` rows in the future. |
| `baseline_persistence_mwh` | float | Same hour two days earlier. |
| `baseline_climatology_mwh` | float | Farm's mean for that hour-of-day and month. |
| `baseline_wind_rule_mwh` | float | One-line threshold rule on Scottish forecast potential. |
| `model_version` | str | e.g. `lgbm-2026-11-07a`. |

The dispatcher works in half hours: split each hourly value equally into its two half hours.

### `data/processed/model_metrics.json` (A writes, Part 2)

```json
{
  "model_version": "lgbm-2026-11-07a",
  "validation": "walk-forward by month, last 6 months",
  "event_threshold_mwh": 10,
  "models": {
    "lightgbm":     {"mae_mwh": 3.1, "precision": 0.72, "recall": 0.65, "monthly_total_error_pct": 8.0},
    "persistence":  {"mae_mwh": 5.4, "precision": 0.51, "recall": 0.50, "monthly_total_error_pct": 21.0},
    "climatology":  {"mae_mwh": 4.9, "precision": 0.40, "recall": 0.30, "monthly_total_error_pct": 12.0},
    "wind_rule":    {"mae_mwh": 4.2, "precision": 0.60, "recall": 0.55, "monthly_total_error_pct": 15.0}
  },
  "shap_top_signals": [{"signal": "b4_excess_potential_mw", "mean_abs_shap": 1.8}]
}
```

Also present (added in #5/#14, extra keys are allowed): `ablations` (e.g. `lightgbm_without_neso_limits`,
same metric keys as `models`), `interval` (`coverage_p10_p90`, `coverage_on_waste_hours`, …) and
`notes` (list of strings). `shap_top_signals` is mean |SHAP| on each fold's held-out month.

## 5. `data/processed/schedules.parquet` (A writes, Part 3)

One row per `battery_id` per `strategy` per `period_start` (per plan issue in live mode).
Primary key: (`battery_id`, `strategy`, `dispatcher`, `period_start`, `plan_issued_at`).

| Column | Type | Notes |
| --- | --- | --- |
| `battery_id` | str | Joins to `batteries` in `scorecard.json`. |
| `farm_id` | str | Farm the battery sits behind. |
| `zone` | str | |
| `strategy` | str | `do_nothing`, `reactive`, `forecast` (ours), `perfect`. |
| `dispatcher` | str | `rule` or `lp`. |
| `period_start` | timestamp UTC | |
| `plan_issued_at` | timestamp UTC | When this plan was made. Backtest: D-1 10:00 UTC, one value per day (the plan's market inputs are fixed then; at midnight it is re-run from the actual SoC). Live (`dispatch/live.py`): the 30-min re-plan time; the newest plan per half hour wins. |
| `action` | str | `charge`, `hold`, `discharge`. |
| `power_mw` | float | ≥ 0. Direction comes from `action`; 0 when `hold`. |
| `soc_mwh` | float | State of charge at the **end** of the period. 0 ≤ value ≤ energy capacity. |
| `captured_curtailment_mwh` | float | Energy taken in that would otherwise have been wasted (uses actual curtailment, not forecast). |
| `revenue_gbp` | float | Discharge MWh × `market_price_gbp_mwh` minus any grid charging cost. |
| `reason` | str | One line, e.g. `charging: 180 MWh waste forecast behind B4`. |

### `data/processed/live_executed.parquet` (A writes, `dispatch/live.py`)

What the live batteries actually did, one row per `battery_id` per elapsed `period_start` (primary key
(`battery_id`, `period_start`); a later run overwrites the same half hour). Same columns as `schedules.parquet`,
`strategy` = `forecast`, `dispatcher` = `lp`, `plan_issued_at` = the plan that was executed (**nullable**: null for a half hour no plan covered, which then just reacted to live waste). `action`, `power_mw`,
`soc_mwh`, `captured_curtailment_mwh` and `revenue_gbp` are **executed** values (planned rows in
`schedules.parquet` keep 0 there). For the Autopilot timeline: executed rows for past half hours, the newest plan
for future ones. Live plans cover a rolling 24 h from the next half hour.

### `data/processed/scorecard.json` (A writes, Part 3)

```json
{
  "period": {"start": "2026-05-01T00:00:00Z", "end": "2026-11-01T00:00:00Z"},
  "batteries": [{"battery_id": "seagreen_bess", "farm_id": "seagreen", "zone": "north_scotland",
                 "power_mw": 100, "energy_mwh": 200, "efficiency": 0.88}],
  "results": [{"strategy": "forecast", "dispatcher": "rule", "captured_curtailment_mwh": 12000,
               "revenue_gbp": 950000, "payments_avoided_gbp": 610000, "capture_ratio": 0.71}]
}
```

`results` has one entry per (`strategy`, `dispatcher`): `do_nothing/rule`, `reactive/rule|lp`, `forecast/rule|lp`,
`perfect/rule` (the rules given perfect knowledge) and **`perfect/lp`** (the perfect-hindsight optimum from
`dispatch.optimiser.hindsight_bound`, a true ceiling). `capture_ratio` = revenue ÷ **`perfect/lp`** revenue, so
`perfect/lp` = 1.0, `perfect/rule` < 1 and `do_nothing` = 0.0. Extra keys: `capture_ratio_basis`, `holdout`
(`months`, `capture_ratio`, `revenue_gbp` for months the planning threshold was not tuned on), `plan_waste_fraction`
and `caveats`. `schedules.parquet` likewise has `perfect/lp` rows.

## 6. Python interfaces across the A/B boundary

The simulator (B) runs batteries using A's dispatcher, so this signature is part of the contract.

```python
# dispatch/rule_based.py and dispatch/optimiser.py (A)
def plan_day(
    battery: dict,                    # {"battery_id", "power_mw", "energy_mwh", "efficiency"}
    curtailment_mwh: np.ndarray,      # shape (48,) forecast (or actual, for "perfect") waste at its site
    price_gbp_mwh: np.ndarray,        # shape (48,) expected price
    headroom_mw: np.ndarray,          # shape (48,) spare boundary capacity south of the battery
    soc_start_mwh: float,
) -> dict:                            # {"action": (48,) str, "power_mw": (48,), "soc_mwh": (48,), "reason": (48,) str}

# sim/engine.py (B)
def simulate(scenario: dict) -> dict  # scenario/results exactly as POST /api/scenario below
```

Fixed day length of 48 is assumed; clock-change days are handled by the caller padding/trimming. `dispatch/live.py` passes the **remaining** half hours of
today instead (shape `(n,)`, n ≤ 48); both planners accept any length, including 0. `dispatch.optimiser.plan_day`
(the LP) has the same signature and return shape as `dispatch.rule_based.plan_day`; both may return a `discharge`
with `power_mw > 0` where the planned SoC is 0 (sale intent), so callers cap by actual SoC.

## 7. HTTP API (B writes `app/server.py`)

FastAPI, JSON, all times ISO 8601 with `Z`. Query params `start`/`end` are ISO dates or datetimes
(UTC), `end` exclusive; default = full available range. Until real data is released the server
serves rows from `app/fixtures/` with the same columns, and every response says so.

Every response body includes:

```json
{"meta": {"data_version": "data-v1", "source": "processed" | "fixtures", "generated_at": "2026-11-07T18:00:00Z", "estimated": true}}
```

| Method & path | Returns |
| --- | --- |
| `GET /api/health` | `{"status": "ok", "meta": {...}}` |
| `GET /api/farms` | `{"farms": [{"farm_id", "farm_name", "lat", "lon", "capacity_mw", "zone", "site_type", "bmu_ids": [..]}]}` |
| `GET /api/reveal/summary?start&end` | `{"totals": {"curtailed_twh", "payments_gbp", "replacement_gas_gbp", "co2_t", "homes_equiv", "by_boundary_twh": {"B4", "B6"}}, "farms": [{"farm_id", "curtailed_mwh", "potential_mwh", "share_wasted"}]}` (`potential_mwh` = Σ `planned_mwh`, so `share_wasted` is the share of planned output) |
| `GET /api/reveal/farm/{farm_id}?start&end` | `{"farm_id", "series": [{"period_start", "planned_mwh", "accepted_mwh", "metered_mwh", "curtailed_mwh_a", "curtailed_mwh_b"}]}` |
| `GET /api/reveal/boundaries?start&end` | `{"boundaries": [{"boundary": "B4", "share_periods_binding": 0.42, "approximate": true}]}` |
| `GET /api/forecast/tomorrow` | `{"issued_at", "farms": [{"farm_id", "hours": [{"hour_start", "pred_mwh", "p10_mwh", "p90_mwh"}]}]}` |
| `GET /api/forecast/backtest?farm_id&start&end` | `{"farm_id", "series": [{"hour_start", "pred_mwh", "actual_mwh", "persistence_mwh", "climatology_mwh", "wind_rule_mwh"}]}` |
| `GET /api/forecast/metrics` | contents of `model_metrics.json` |
| `GET /api/autopilot/schedule?date&battery_id` | `{"plan_issued_at", "live": bool, "batteries": [{"battery_id", "steps": [{"period_start", "action", "power_mw", "soc_mwh", "reason"}]}]}` |
| `GET /api/autopilot/scorecard` | contents of `scorecard.json` |
| `POST /api/scenario` | scenario results, below |
| `GET /api/method/calibration` | `{"historical_twh", "simulated_twh", "error_pct", "by_month": [{"month", "historical_mwh", "simulated_mwh"}]}` |

### `POST /api/scenario` request

All fields optional; omitted = historical setting. `{}` returns the baseline.

```json
{
  "b4":  {"mode": "historical" | "fixed" | "offset", "mw": 0},
  "b6":  {"mode": "historical" | "fixed" | "offset", "mw": 0},
  "batteries": [{"battery_id": "user_1", "lat": 57.5, "lon": -4.2, "zone": "north_scotland",
                 "power_mw": 100, "energy_mwh": 200, "efficiency": 0.88, "dispatcher": "rule" | "lp",
                 "site": "grid" | "farm", "radius_km": 20}],
  "weather": {"wind_scale": 1.0, "replay_start": null, "replay_end": null},
  "window": {"start": "2025-01-01", "end": "2026-01-01"},
  "forecast_quality": "none" | "ours" | "perfect",

  "subsea_link": {"zone": "north_scotland", "gw": 2.0, "availability": 0.95},
  "demand_growth": {"north_scotland": {"heat_pumps_gw": 0, "evs_gw": 0, "data_centres_gw": 0},
                    "south_scotland": {"heat_pumps_gw": 0, "evs_gw": 0, "data_centres_gw": 0}},
  "gas_price_p_therm": 80, "carbon_price_gbp_t": 50,
  "switch_off_price": {"mode": "multiplier" | "flat", "value": 1.0},
  "carbon_intensity_t_mwh": 0.37
}
```

Battery `site` (default `"grid"`): a grid battery can store waste anywhere in its zone (north of B4: B4 and B6
waste; between B4 and B6: B6 waste). A `"farm"` battery shares a connection with every farm within `radius_km`
(default 20) in its own zone, else the nearest farm in that zone, and can store only those farms' would-be waste;
`farm_ids` in the response names them (empty for a grid battery).

Levers 1, 2, 4, 8, 12, 13 (first block) are Core; 3, 6, 9, 10, 11 (second block) are Full; levers 5
and 7 (electrolysers, fleet editing) are Stretch and will be added here when built.

### `POST /api/scenario` response

```json
{
  "fingerprint": "sha1-of-normalised-request", "cached": false, "elapsed_ms": 412,
  "window": {"start": "...", "end": "..."},
  "scenario": {"curtailed_twh": 7.1, "by_boundary_twh": {"B4": 5.0, "B6": 2.1},
               "payments_gbp": 2.4e8, "replacement_gas_gbp": 7.0e8, "co2_t": 2.6e6, "homes_equiv": 2.4e6},
  "baseline": {"...same keys...": 0},
  "delta":    {"...same keys, scenario minus baseline...": 0},
  "batteries": [{"battery_id", "captured_curtailment_mwh", "revenue_gbp", "cycles", "forecast_fallback_periods",
                 "site", "farm_ids"}],
  "farms": [{"farm_id", "curtailed_mwh", "baseline_curtailed_mwh"}],
  "meta": {...}
}
```

`homes_equiv` = curtailed MWh ÷ 2.7 MWh per home per year (scaled to the window length).
