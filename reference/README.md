# reference/farms.csv

Columns exactly as `docs/CONTRACT.md` §1. 50 BMUs, 33 farms (34 BMUs in `north_scotland`, 16 in `south_scotland`).

**How the units were chosen.** Every Elexon BMU with `fuelType = WIND` was ranked by minutes under
constraint-flagged (`soFlag`) bid acceptances in 2025. The top 50 named Scottish units were kept, skipping
supplier aggregate units (`2__*`, `C__*`) that have no single site, and English farms (Greater Gabbard).
Seagreen, Moray East and Moray West (the Modo validation targets) are all included.

**Capacity.** `capacity_mw` is Elexon's `generationCapacity`, except where the highest Physical Notification
seen Jan 2024–Nov 2026 is below 85% of it. Those Elexon values are stale, so the observed PN max is used:

| BMU | Elexon MW | Used MW |
| --- | --- | --- |
| T_SGRWO-1 | 358.0 | 300 |
| T_SGRWO-2 | 220.0 | 80 |
| T_SGRWO-3 | 374.6 | 160 |
| T_SGRWO-5 | 300.0 | 120 |
| T_GORDW-1 | 86.8 | 70 |
| T_GORDW-2 | 58.7 | 40 |
| T_SOKYW-1 | 426.9 | 235 |
| T_AKGLW-3 | 163.4 | 81 |

(Elexon's six Seagreen BMUs sum to 1,773 MW; the farm is 1,075 MW.) No unit's PN exceeds 1.05 × the capacity used.

**Zones.** `north_scotland` = SSEN Transmission area (north of B4), including Kintyre (Beinn an Tuirc) and
Shetland (Viking). Seagreen lands at Tealing, so it is north of B4. Neart na Gaoithe lands at Torness, so it is south.

**Needs a human check:**
- `lat`/`lon` are approximate farm centroids entered by hand (±~5 km). Fine for the map and Open-Meteo's
  ~10 km grid; check against REPD before quoting.
- `repd_ref_id` is empty; not matched to REPD yet.
- Units that started part-way through the period: Moray West (Apr 2024), Limekiln (Aug 2024),
  Neart na Gaoithe (Oct 2024), Viking (Jan 2024).
