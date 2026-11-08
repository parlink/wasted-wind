"""Day-block bootstrap for "is this difference real?" (#51 review).

Half hours within a day are strongly correlated (one windy day moves all 48), so resampling single
half hours would make intervals far too narrow. Whole UTC days are resampled with replacement.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

N_BOOT = 2000


def ci95(stat, values: pd.Series, day_of, n: int = N_BOOT, seed: int = 0) -> tuple[float, float]:
    """95% interval of stat(values) when whole days are resampled. day_of: the day of each value."""
    days = pd.DatetimeIndex(day_of).normalize()
    groups = [g for _, g in values.groupby(days)]
    rng = np.random.default_rng(seed)
    out = [stat(pd.concat([groups[i] for i in rng.integers(0, len(groups), len(groups))])) for _ in range(n)]
    lo, hi = np.percentile(out, [2.5, 97.5])
    return float(lo), float(hi)


def capture_diff_ci95(b: pd.Series, a: pd.Series, ceiling: pd.Series, n: int = N_BOOT,
                      seed: int = 0) -> tuple[float, float]:
    """95% interval of (sum b - sum a) / sum ceiling, all indexed by day, resampling days."""
    df = pd.concat({"a": a, "b": b, "c": ceiling}, axis=1).fillna(0.0).to_numpy()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(df), (n, len(df)))
    s = df[idx].sum(axis=1)                                   # n x 3
    diff = (s[:, 1] - s[:, 0]) / s[:, 2]
    lo, hi = np.percentile(diff, [2.5, 97.5])
    return float(lo), float(hi)
