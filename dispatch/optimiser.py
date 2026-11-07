"""Perfect-hindsight ceiling for one battery: a linear programme over the whole replay period.

Knows every half hour's actual waste, price and boundary headroom, and picks charge c_t and
discharge d_t (MW) to maximise revenue  sum_t price_t * d_t * 0.5 h  subject to

    0 <= c_t <= min(power, waste_t / 0.5 h)      only waste can be stored (as in execution)
    0 <= d_t <= min(power, headroom_t), and 0 when price_t <= 0
    soc_t = soc_{t-1} + 0.5 h * (efficiency * c_t - d_t),   0 <= soc_t <= energy

Every schedule `dispatch.scorecard.execute` can produce satisfies these constraints, so no
strategy can earn more: it is a true upper bound (CONTRACT §5 `perfect`, dispatcher "lp").
It allows charging and discharging in the same half hour (a relaxation), which only raises the
bound. Solved with HiGHS via scipy.optimize.linprog; about a second for six months.
"""
from __future__ import annotations

import numpy as np
from scipy import sparse
from scipy.optimize import linprog

H = 0.5
CAPTURE_TIEBREAK = 1e-6   # tiny value per MWh captured, so ties prefer catching waste


def hindsight_bound(battery: dict, waste_mwh, price_gbp_mwh, headroom_mw, soc_start_mwh: float) -> dict:
    p, e, eff = float(battery["power_mw"]), float(battery["energy_mwh"]), float(battery["efficiency"])
    w = np.nan_to_num(np.asarray(waste_mwh, float))
    price = np.nan_to_num(np.asarray(price_gbp_mwh, float))
    head = np.maximum(np.nan_to_num(np.asarray(headroom_mw, float)), 0.0)
    T = len(w)
    # variables: [c (T), d (T), s (T)]
    cost = np.concatenate([np.full(T, -CAPTURE_TIEBREAK * H), -price * H, np.zeros(T)])
    eye = sparse.identity(T, format="csr")
    shift = sparse.eye(T, k=-1, format="csr")                     # s_{t-1}
    a_eq = sparse.hstack([-eff * H * eye, H * eye, eye - shift], format="csr")
    b_eq = np.zeros(T)
    b_eq[0] = min(max(soc_start_mwh, 0.0), e)
    bounds = ([(0.0, min(p, wt / H)) for wt in w]
              + [(0.0, min(p, h) if pr > 0 else 0.0) for h, pr in zip(head, price)]
              + [(0.0, e)] * T)
    res = linprog(cost, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if not res.success:
        raise RuntimeError(f"hindsight LP failed: {res.message}")
    c, d, s = res.x[:T], res.x[T:2 * T], res.x[2 * T:]
    c[c < 1e-9], d[d < 1e-9] = 0.0, 0.0
    action = np.where(d > 0, "discharge", np.where(c > 0, "charge", "hold"))
    reason = np.where(d > 0, [f"hindsight optimum: selling at £{x:.0f}/MWh" for x in price],
                      np.where(c > 0, [f"hindsight optimum: storing {x:.0f} MWh of waste" for x in w],
                               "hindsight optimum: holding"))
    return {"action": action, "power_mw": np.where(d > 0, d, c), "soc_mwh": np.clip(s, 0.0, e),
            "captured_curtailment_mwh": c * H, "revenue_gbp": d * H * price, "reason": reason}
