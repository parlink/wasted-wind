# reference/farms.csv

Columns exactly as `docs/CONTRACT.md` §1. 50 BMUs, 33 farms (34 BMUs in `north_scotland`, 16 in `south_scotland`).

**How the units were chosen.** Every Elexon BMU with `fuelType = WIND` was ranked by minutes under
constraint-flagged (`soFlag`) bid acceptances in 2025. The top 50 named Scottish units were kept, skipping
supplier aggregate units (`2__*`, `C__*`) that have no single site, and English farms (Greater Gabbard).
Seagreen, Moray East and Moray West (the Modo validation targets) are all included.

**Capacity.** `capacity_mw` is the highest Physical Notification each BMU declared, Jan 2024–Nov 2026. Elexon's
`generationCapacity` is often stale: Seagreen's six BMUs sum to 1,773 MW there, against a 1,075 MW farm, and
South Kyle shows 427 MW for ~240 MW. The observed PN peak is never above Elexon's figure by more than 0.5%,
and by construction no unit's PN exceeds 1.05 × `capacity_mw` (the CONTRACT invariant).

**Per-BMU capacities don't add up to the farm's size for two farms.** Their units take turns peaking:

| Farm | Σ `capacity_mw` | Max simultaneous PN (whole farm) | Nameplate |
| --- | --- | --- | --- |
| Seagreen | 1,158 | 1,102 | 1,075 |
| Dorenell | 226 | 177 | 177 |
| Viking | 444 | 444 | 443 |

Every other multi-BMU farm matches within 1 MW. Scaling the BMUs down to the farm total would break the
per-BMU invariant (Seagreen BMU 6 declares 358 MW), so it isn't done here. **For any farm-level capacity
(Method B potential, capacity factors, map sizes), use the farm's max simultaneous PN, not Σ `capacity_mw`.**
`pipeline/reconstruct.py` (#3) computes that; a `farm_capacity_mw` column would need a CONTRACT change.

**Zones.** `north_scotland` = SSEN Transmission area (north of B4), including Kintyre (Beinn an Tuirc) and
Shetland (Viking). Seagreen lands at Tealing, so it is north of B4. Neart na Gaoithe lands at Torness, so it is south.

**Needs a human check:**
- `lat`/`lon` are approximate farm centroids entered by hand (±~5 km). Fine for the map and Open-Meteo's
  ~10 km grid; check against REPD before quoting.
- `repd_ref_id` is empty; not matched to REPD yet.
- Units that started part-way through the period: Moray West (Apr 2024), Limekiln (Aug 2024),
  Neart na Gaoithe (Oct 2024), Viking (Jan 2024).
