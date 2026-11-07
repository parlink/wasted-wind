"""Part 3: rule-based battery dispatcher. `plan_day` has the exact CONTRACT §6 signature; the
simulator (sim/engine.py) imports it automatically.

For one battery and one day of 48 half hours (index 0 = 00:00 UTC), given forecast waste at its
site (MWh per half hour), expected price, and spare boundary capacity south of it (MW):

1. Charge in every half hour with meaningful forecast waste (> max(1 MWh, 25% of power x 0.5 h)),
   up to power, space and the waste itself.
2. Before the first forecast waste, empty ahead of it: sell just enough in the best-priced
   earlier half hours to make room for what the day's waste can fill.
3. From the first forecast waste on (or all day, if none is forecast), sell in the top-price
   half hours without forecast waste, as many as it takes to sell what it holds plus what it
   expects to catch, so it is empty again before the next spike.
Never sell at a price <= 0 or without boundary room: energy that can't cross the boundary is
exactly what was being wasted.

Units: power_mw is grid-side MW for the half hour. Efficiency is applied once, on charging:
soc += MW x 0.5 h x efficiency; discharging removes MW x 0.5 h. soc_mwh is at the END of the period.
"""
from __future__ import annotations

import numpy as np

H = 0.5                       # hours per settlement period
CHARGE_THRESHOLD_MWH = 1.0    # floor: forecast waste worth planning around, per half hour
# ...and at least this share of what the battery can take in a half hour (power x 0.5 h). A
# forecast of a few MWh at a 1 GW farm is noise; treating it as waste blocks selling all day.
# Sensitivity on the May-Oct 2026 replay (capture ratio of the forecast battery; reactive 0.35):
# 1 MWh 0.30, 5 MWh 0.36, 10 MWh 0.38, 25 MWh 0.41. 0.25 gives 12.5 MWh for a 100 MW battery.
PLAN_WASTE_FRACTION = 0.25


def _hhmm(i: int) -> str:
    return f"{i // 2:02d}:{30 * (i % 2):02d}"


def _sell_order(price: np.ndarray, head: np.ndarray, allowed: np.ndarray) -> np.ndarray:
    """Indices where selling is possible (allowed, price > 0, room > 0), best price first."""
    ok = allowed & (price > 0) & (head > 0)
    idx = np.flatnonzero(ok)
    return idx[np.argsort(-price[idx], kind="stable")]


def plan_day(battery: dict, curtailment_mwh: np.ndarray, price_gbp_mwh: np.ndarray,
             headroom_mw: np.ndarray, soc_start_mwh: float) -> dict:
    p, e, eff = float(battery["power_mw"]), float(battery["energy_mwh"]), float(battery["efficiency"])
    boundary = "B6" if battery.get("zone") == "south_scotland" else "B4"
    w = np.nan_to_num(np.asarray(curtailment_mwh, float), nan=0.0)
    price = np.nan_to_num(np.asarray(price_gbp_mwh, float), nan=0.0)
    head = np.maximum(np.nan_to_num(np.asarray(headroom_mw, float), nan=0.0), 0.0)
    n = len(w)
    waste = w > max(CHARGE_THRESHOLD_MWH, PLAN_WASTE_FRACTION * p * H)
    sell_mw = np.zeros(n)                 # planned discharge per half hour
    sell_why = np.full(n, "", dtype=object)

    soc0 = min(max(float(soc_start_mwh), 0.0), e)
    if waste.any():
        first = int(np.argmax(waste))
        absorbable = min(e, float(np.minimum(p * H, w[waste]).sum()) * eff)
        need = max(0.0, soc0 - (e - absorbable))           # energy to sell before the first waste
        total = w[waste].sum()
        pre_sold = need
        for i in _sell_order(price, head, np.arange(n) < first):
            if need <= 1e-9:
                break
            mw = min(p, head[i], need / H)
            sell_mw[i], need = mw, need - mw * H
            sell_why[i] = f"emptying ahead of {total:.0f} MWh waste forecast from {_hhmm(first)}"
        pre_sold -= need
        # From the first waste on, sell what is left plus what it expects to catch, in the best
        # non-waste half hours (between spikes too), so it is not still full for the next one.
        later = (np.arange(n) >= first) & ~waste
        to_sell = soc0 - pre_sold + absorbable
    else:
        later, to_sell = np.ones(n, bool), soc0 if soc0 > 0 else e
    k = int(np.ceil(max(to_sell, 0.0) / (p * H) - 1e-9))
    for i in _sell_order(price, head, later & (sell_mw == 0))[:k]:
        sell_mw[i] = min(p, head[i])
        sell_why[i] = f"discharging: top-price half hour (£{price[i]:.0f}/MWh), {head[i]:.0f} MW room on {boundary}"

    action = np.full(n, "hold", dtype=object)
    power, soc_out, reason = np.zeros(n), np.zeros(n), np.full(n, "", dtype=object)
    soc = soc0
    for i in range(n):
        if waste[i] and soc < e - 1e-9:
            mw = min(p, (e - soc) / (H * eff), w[i] / H)
            soc += mw * H * eff
            action[i], power[i] = "charge", mw
            reason[i] = f"charging: {w[i]:.0f} MWh waste forecast behind {boundary}"
        elif sell_mw[i] > 0 and soc > 1e-9:
            mw = min(sell_mw[i], soc / H)
            soc -= mw * H
            action[i], power[i], reason[i] = "discharge", mw, sell_why[i]
        elif waste[i]:
            reason[i] = f"holding: full, {w[i]:.0f} MWh waste forecast behind {boundary}"
        elif head[i] <= 0:
            reason[i] = f"holding: no room on {boundary} to export"
        elif soc <= 1e-9:
            reason[i] = "holding: empty"
        else:
            reason[i] = f"holding: £{price[i]:.0f}/MWh is not among the day's best prices"
        soc_out[i] = soc
    return {"action": action.astype(str), "power_mw": power, "soc_mwh": soc_out,
            "reason": reason.astype(str)}
