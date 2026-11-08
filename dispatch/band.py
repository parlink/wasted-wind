"""Uncertainty-aware planner (#52): plan against the forecast's band, not just its point.

The day-ahead waste forecast runs low and its misses are storms ABOVE the p10-p90 band (#41: on hours
with waste, 29% land above p90, 0.1% below p10). A battery that plans only on the point forecast is then
too often still holding energy when a storm it under-forecast arrives. This planner plans the day on

    planned waste = point + w * (p90 - point)

i.e. somewhere between the likely waste (w = 0, the existing LP planner) and the high end of the band
(w = 1). Everything else is the LP planner (dispatch.optimiser.plan_day), same CONTRACT §6 signature, so
execution, the sale-intent slots and the scorecard are unchanged. `w` is chosen in dispatch.scorecard on
the tuning months only (May-Jul 2026), like PLAN_WASTE_FRACTION; Aug-Oct stay a hold-out.

Why one weight rather than a full scenario LP: the plan is executed once against what really happens, and
the band only moves the waste forecast, so a blended signal captures the effect (be emptier before a
possible storm) in one transparent number that can be tuned and reported honestly.
"""
from __future__ import annotations

import numpy as np

from dispatch import optimiser

WEIGHT_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)
DEFAULT_WEIGHT = 0.5      # used where no tuned weight is given (the simulator, before a tuned release)
NAME = "lp_band"          # the dispatcher value in schedules.parquet / scorecard.json / scenarios


def band_signal(point, p90, weight: float) -> np.ndarray:
    """point + weight * (p90 - point), with p90 falling back to the point where it is missing.
    Never below the point (a band narrower than the point is ignored) and never negative."""
    pt = np.nan_to_num(np.asarray(point, float))
    hi = np.asarray(p90, float) if p90 is not None else np.full_like(pt, np.nan)
    hi = np.where(np.isfinite(hi), np.maximum(hi, pt), pt)
    return np.maximum(pt + float(weight) * (hi - pt), 0.0)


def plan_day(battery: dict, curtailment_mwh: np.ndarray, price_gbp_mwh: np.ndarray,
             headroom_mw: np.ndarray, soc_start_mwh: float) -> dict:
    """CONTRACT §6 signature. The caller passes the band signal (band_signal) as curtailment_mwh;
    this is then the LP planner, with its reasons saying it planned for the band."""
    plan = optimiser.plan_day(battery, curtailment_mwh, price_gbp_mwh, headroom_mw, soc_start_mwh)
    plan["reason"] = np.array([r.replace("optimiser:", "optimiser (planning for the high end):", 1)
                               for r in plan["reason"]], dtype=str)
    return plan
