"""Stand-in for Person A's dispatch.rule_based.plan_day (CONTRACT.md §6) until #6 lands.

Same signature and return shape, deliberately simple, so the simulator switches over by import.
"""
from __future__ import annotations

import numpy as np

CHARGE_THRESHOLD_MWH = 1.0   # charge when at least this much waste is expected in the half hour
TOP_PRICE_PERIODS = 8        # discharge in the day's 8 highest expected-price half hours


def plan_day(battery: dict, curtailment_mwh: np.ndarray, price_gbp_mwh: np.ndarray,
             headroom_mw: np.ndarray, soc_start_mwh: float) -> dict:
    p, e, eff = battery["power_mw"], battery["energy_mwh"], battery["efficiency"]
    n = len(curtailment_mwh)
    quiet = (curtailment_mwh <= CHARGE_THRESHOLD_MWH) & (headroom_mw > 0)
    price = np.where(quiet, np.nan_to_num(price_gbp_mwh, nan=-np.inf), -np.inf)
    top = {int(i) for i in np.argsort(-price)[:TOP_PRICE_PERIODS] if np.isfinite(price[i])}
    action, power, soc_out, reason = [], np.zeros(n), np.zeros(n), []
    soc = soc_start_mwh
    for i in range(n):
        if curtailment_mwh[i] > CHARGE_THRESHOLD_MWH and soc < e - 1e-9:
            mw = min(p, (e - soc) / (0.5 * eff), curtailment_mwh[i] / 0.5)
            soc += mw * 0.5 * eff
            action.append("charge"); power[i] = mw
            reason.append(f"charging: {curtailment_mwh[i]:.0f} MWh waste expected")
        elif i in top and soc > 1e-9:
            mw = min(p, soc / 0.5, headroom_mw[i])
            soc -= mw * 0.5
            action.append("discharge"); power[i] = mw
            reason.append("discharging: top-price half hour with boundary room")
        else:
            action.append("hold")
            reason.append("holding")
        soc_out[i] = soc
    return {"action": np.array(action), "power_mw": power, "soc_mwh": soc_out, "reason": np.array(reason)}
