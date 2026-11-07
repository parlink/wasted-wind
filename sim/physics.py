"""Generic turbine physics shared by the simulator."""
import numpy as np


def power_curve(v, cut_out: bool = False):
    """Capacity factor from 100 m wind speed (m/s): cut-in 3, rated 12.5, optional cut-out at 25.

    A single generic curve for every farm; good enough to scale wind up or down (lever 8),
    not to predict a specific farm's output. Cut-out is off by default: our wind is an hourly
    mean at one point per farm, and an hourly mean above 25 m/s doesn't mean every turbine
    stopped for the whole hour, so applying it would make a windier storm waste *less*.
    """
    v = np.asarray(v, dtype=float)
    cf = np.clip((v**3 - 27.0) / (12.5**3 - 27.0), 0.0, 1.0) * 0.97
    return np.where(v > 25.0, 0.0, cf) if cut_out else cf
