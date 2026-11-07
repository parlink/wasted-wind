"""Part 3, optimising dispatcher (#13) and the perfect-hindsight ceiling, both linear programmes.

One battery, half hours t, charge c_t and discharge d_t (MW), state of charge s_t (MWh):

    maximise  sum_t price_t * d_t * 0.5 h   (+ a tiny tie-break for storing waste)
    0 <= c_t <= min(power, waste_t / 0.5 h)      only waste can be stored (as in execution)
    0 <= d_t <= min(power, headroom_t), and 0 when price_t <= 0
    s_t = s_{t-1} + 0.5 h * (efficiency * c_t - d_t),   0 <= s_t <= energy

`plan_day` (CONTRACT §6 signature, the same as dispatch.rule_based) solves it for one day on the
FORECAST waste, expected price and expected headroom. Like the rule dispatcher it then adds sale
"intent" slots: so waste nobody forecast, caught live, can still be sold that day, but only at
prices at least as good as the plan's own sales. There is no value on energy left at midnight,
so the plan empties the battery each day (as the rule dispatcher aims to). A solve takes ~5 ms.

`hindsight_bound` solves the same LP over the whole replay on the ACTUAL waste, price and headroom.
Every schedule `dispatch.scorecard.execute` can produce satisfies these constraints, so no strategy
can earn more: it is a true upper bound (CONTRACT §5 `perfect`, dispatcher "lp"). It allows
charging and discharging in the same half hour (a relaxation), which only raises the bound.
"""
from __future__ import annotations

import numpy as np
from scipy import sparse
from scipy.optimize import linprog

H = 0.5
CAPTURE_TIEBREAK = 1e-6   # tiny value per MWh captured, so ties prefer catching waste
MIN_ACTION_MWH = 0.05     # planned charges below this are shown as "hold" (execution still reacts live)


def _solve(p: float, e: float, eff: float, w, price, head, soc0: float):
    """Return (c, d, s) arrays solving the LP above. Inputs are cleaned arrays of equal length."""
    T = len(w)
    cost = np.concatenate([np.full(T, -CAPTURE_TIEBREAK * H), -price * H, np.zeros(T)])
    eye = sparse.identity(T, format="csr")
    shift = sparse.eye(T, k=-1, format="csr")                     # s_{t-1}
    a_eq = sparse.hstack([-eff * H * eye, H * eye, eye - shift], format="csr")
    b_eq = np.zeros(T)
    b_eq[0] = min(max(soc0, 0.0), e)
    bounds = ([(0.0, min(p, wt / H)) for wt in w]
              + [(0.0, min(p, h) if pr > 0 else 0.0) for h, pr in zip(head, price)]
              + [(0.0, e)] * T)
    res = linprog(cost, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if not res.success:
        raise RuntimeError(f"battery LP failed: {res.message}")
    c, d, s = res.x[:T].copy(), res.x[T:2 * T].copy(), res.x[2 * T:].copy()
    c[c < 1e-9], d[d < 1e-9] = 0.0, 0.0
    return c, d, np.clip(s, 0.0, e)


def _clean(battery, waste, price, head):
    p, e, eff = float(battery["power_mw"]), float(battery["energy_mwh"]), float(battery["efficiency"])
    w = np.maximum(np.nan_to_num(np.asarray(waste, float)), 0.0)
    pr = np.nan_to_num(np.asarray(price, float))          # NaN price (no history) = 0: never sell
    h = np.maximum(np.nan_to_num(np.asarray(head, float)), 0.0)
    return p, e, eff, w, pr, h


def plan_day(battery: dict, curtailment_mwh: np.ndarray, price_gbp_mwh: np.ndarray,
             headroom_mw: np.ndarray, soc_start_mwh: float) -> dict:
    p, e, eff, w, price, head = _clean(battery, curtailment_mwh, price_gbp_mwh, headroom_mw)
    if len(w) == 0:          # nothing left to plan (e.g. a live re-plan after 23:30)
        return {"action": np.array([], dtype=str), "power_mw": np.zeros(0), "soc_mwh": np.zeros(0),
                "reason": np.array([], dtype=str)}
    boundary = "B6" if battery.get("zone") == "south_scotland" else "B4"
    c, d, s = _solve(p, e, eff, w, price, head, soc_start_mwh)
    n = len(w)
    c = np.where(c * H < MIN_ACTION_MWH, 0.0, c)      # don't label tie-break crumbs as "charge"
    action = np.where(d > 0, "discharge", np.where(c > 0, "charge", "hold")).astype(object)
    power = np.where(d > 0, d, c)
    reason = np.full(n, "", dtype=object)
    for i in range(n):
        if action[i] == "charge":
            reason[i] = f"optimiser: storing {c[i] * H:.1f} MWh of forecast waste behind {boundary}"
        elif action[i] == "discharge":
            reason[i] = f"optimiser: selling at £{price[i]:.0f}/MWh, {head[i]:.0f} MW room on {boundary}"

    # Sale intent for unforecast waste: idle slots priced at least as well as the plan's own sales.
    floor = price[d > 0].min() if (d > 0).any() else 0.0
    planned_out = float((d * H).sum())
    left = max(e - planned_out, 0.0)
    idle = (action == "hold") & (price > 0) & (price >= floor) & (head > 0) & (w <= 0)
    for i in np.flatnonzero(idle)[np.argsort(-price[idle], kind="stable")]:
        if left <= 1e-9:
            break
        action[i], power[i] = "discharge", min(p, head[i])
        left -= power[i] * H
        reason[i] = f"optimiser: sell slot if charged (£{price[i]:.0f}/MWh, {head[i]:.0f} MW room on {boundary})"
    for i in np.flatnonzero(action == "hold"):
        reason[i] = ("optimiser: holding, no room on " + boundary if head[i] <= 0 else
                     "optimiser: holding, better prices later" if s[i] > 1e-6 else "optimiser: holding, empty")
    return {"action": action.astype(str), "power_mw": power, "soc_mwh": s, "reason": reason.astype(str)}


def planned_revenue(plan: dict, price: np.ndarray, soc_start_mwh: float = 0.0) -> float:
    """Revenue the plan itself expects: sales that its own SoC path pays for (not intent slots)."""
    s = np.asarray(plan["soc_mwh"], float)
    out = np.maximum(np.r_[soc_start_mwh, s[:-1]] - s, 0.0)
    return float((out * np.nan_to_num(np.asarray(price, float)) * (plan["action"] == "discharge")).sum())


def hindsight_bound(battery: dict, waste_mwh, price_gbp_mwh, headroom_mw, soc_start_mwh: float) -> dict:
    p, e, eff, w, price, head = _clean(battery, waste_mwh, price_gbp_mwh, headroom_mw)
    c, d, s = _solve(p, e, eff, w, price, head, soc_start_mwh)
    action = np.where(d > 0, "discharge", np.where(c > 0, "charge", "hold"))
    reason = np.where(d > 0, [f"hindsight optimum: selling at £{x:.0f}/MWh" for x in price],
                      np.where(c > 0, [f"hindsight optimum: storing {x:.0f} MWh of waste" for x in w],
                               "hindsight optimum: holding"))
    return {"action": action, "power_mw": np.where(d > 0, d, c), "soc_mwh": s,
            "captured_curtailment_mwh": c * H, "revenue_gbp": d * H * price, "reason": reason}
