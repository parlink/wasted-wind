"""Generic turbine physics shared by the simulator."""
import numpy as np


def power_curve(v):
    """Capacity factor from 100 m wind speed (m/s): cut-in 3, rated 12.5, cut-out 25.

    A single generic curve for every farm; good enough to scale wind up or down (lever 8),
    not to predict a specific farm's output.
    """
    v = np.asarray(v, dtype=float)
    cf = np.clip((v**3 - 27.0) / (12.5**3 - 27.0), 0.0, 1.0)
    return np.where(v > 25.0, 0.0, cf) * 0.97
