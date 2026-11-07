"""Apply scenario levers to the simulator's input series. Core levers: 1 (B4), 2 (B6), 8 (weather).

Batteries (4), time window (12) and forecast quality (13) are handled in sim.engine.
"""
from __future__ import annotations

import numpy as np

from sim.physics import power_curve

MIN_CF = 0.02   # below this the power curve can't explain the farm's output, so don't rescale it


def boundary_mw(historical: np.ndarray, lever: dict) -> np.ndarray:
    """Lever 1/2: keep the historical limit series, replace it with a fixed MW, or offset it."""
    mode, mw = lever.get("mode", "historical"), float(lever.get("mw", 0.0))
    if mode == "fixed":
        return np.full_like(historical, max(mw, 0.0))
    if mode == "offset":
        return np.maximum(historical + mw, 0.0)
    return historical


def potential_mw(planned_mw: np.ndarray, wind_ms: np.ndarray, cap_mw: np.ndarray, wind_scale: float) -> np.ndarray:
    """Lever 8: scale wind *speed*, then re-run the power curve.

    At scale 1 this is exactly the planned output (so history is reproduced). Where the curve
    explains the output (cf >= MIN_CF), planned output is multiplied by curve(scale x v) / curve(v).
    Below that (near cut-in) a ratio is unstable, so the curve's *change* is added instead:
    planned + cap x (curve(scale x v) - curve(v)). Both forms equal planned at scale 1, so the
    result is continuous in the scale. No wind data: planned output scaled linearly.
    """
    if wind_scale == 1.0:
        return planned_mw
    if wind_scale == 0.0:
        return np.zeros_like(planned_mw)
    v = np.nan_to_num(wind_ms)
    cf_now, cf_new = power_curve(v), power_curve(v * wind_scale)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio_form = planned_mw * cf_new / cf_now
    additive_form = planned_mw + cap_mw[None, :] * (cf_new - cf_now)
    out = np.where(cf_now >= MIN_CF, ratio_form, additive_form)
    out = np.where(np.isnan(wind_ms), planned_mw * wind_scale, out)
    return np.clip(out, 0.0, cap_mw[None, :])
