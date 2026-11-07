# Real API samples

One real response per endpoint, trimmed to a few records. Each JSON file holds
`{"_request": <exact URL>, "response": <body>}`. Fetched 2026-11-07 by `pipeline/fetch.py`'s endpoints.

**Elexon time bounds are inclusive and overlapping.** `from=06:00&to=07:00` returns PN rows starting 05:30
(they overlap 06:00) and 07:00, and MID rows for 06:00, 06:30 *and* 07:00. `pipeline/fetch.py` trims every
response so each record is stored once, in the file whose window contains its start (`fetch.in_window`).
Loaders can concatenate files with no dedupe. (`/demand/outturn/stream` uses settlement-date windows, which
are already disjoint, so it is not trimmed.)

| File | Endpoint | Notes for parsing |
| --- | --- | --- |
| `elexon_pn_stream.json` | `/datasets/PN/stream` | `timeFrom`/`timeTo` UTC with `Z`; `levelFrom`/`levelTo` in MW; linear between them. |
| `elexon_boalf_stream.json` | `/datasets/BOALF/stream` | **Constraint flag is `soFlag`** (bool). ~93% of wind acceptances in 2025 have `soFlag=true`. One acceptance (`acceptanceNumber`) spans several rows; a later acceptance overrides an earlier one for overlapping minutes (use the highest `acceptanceNumber`/latest `acceptanceTime`). |
| `elexon_bod_stream.json` | `/datasets/BOD/stream` | `bid`/`offer` in GBP/MWh per `pairId`. Negative `pairId` bands cover levels below the notified output (turn-down), so the relevant price for curtailment is `bid` on those bands. Wind bids are usually negative (farm is paid); ±9999/10000 are placeholder "never accept" prices. |
| `elexon_b1610_stream.json` | `/datasets/B1610/stream` | `quantity` in MWh per half hour; `halfHourEndTime` (end of the period) has no `Z` but **is UTC**, checked in BST: settlement date 2025-07-01 period 1 ends 2025-06-30T23:30, and clock-change days have 46 and 50 periods. The stream returns one row per unit per period, from the latest settlement run available (`settlementRunType`: RF for 2024–25, R2/R3 mid-2026, SF/II for the last few weeks). Recent months get revised: refetch them before a final data release. |
| `elexon_mid_stream.json` | `/datasets/MID/stream` | Two providers per period: `APXMIDP` (has volume) and `N2EXMIDP` (often price 0, volume 0). Use APX, or volume-weight. |
| `elexon_demand_outturn_stream.json` | `/demand/outturn/stream` | Takes `settlementDateFrom`/`settlementDateTo` (inclusive); **ignores `from`/`to`**. MW. |
| `elexon_ndf_stream.json` | `/datasets/NDF/stream` | National demand forecast, `publishDateTimeFrom/To`. Use `publishTime` ≤ issue time for leak-free signals. |
| `elexon_reference_bmunits.json` | `/reference/bmunits/all` | `fuelType`, `generationCapacity` (MW, sometimes stale: see `reference/README.md`). Transmission units have `gspGroupId: null`. |
| `neso_day_ahead_constraints.csv` | NESO CKAN `day-ahead-constraint-flows-and-limits` | **Times are UK local (`GMT_BST`)**, no offset: 23 or 25 rows on clock-change days. Convert to UTC. B4 ≈ `SSE_SP` (to 2024-04-20) then `SSE_SP2`; B6 ≈ `SCOTEX`. |
| `neso_congestion_weekly.csv` | NESO CKAN `operational-transparency-forum-network-congestion-data` | Weekly MW; B4 column is `Min of B4 B5_Actual`, B6 is `B6_Actual`. Fallback only. |
| `openmeteo_archive.json` | Open-Meteo archive | Requested with `timezone=GMT`, `wind_speed_unit=ms`. Hourly. |
| `openmeteo_previous_runs.json` | Open-Meteo previous runs | `*_previous_dayN` = value from the run N days before the valid time. For a forecast issued D-1 10:00 UTC, only `previous_day2` is guaranteed leak-free for every hour of day D, so `pipeline/fetch.py` downloads **only day2** (the sample shows day1 too, for reference). **Values are null before ~2024-03-07** (not January 2024 as the brief says), so forecast training starts in March 2024. |
