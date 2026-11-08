"""Apply scenario levers to the simulator's input series. Core levers: 1 (B4), 2 (B6), 8 (weather);
full levers: 3 (subsea link), 6 (Scottish demand growth).

Batteries (4), time window (12), forecast quality (13) and the economics (9, 10, 11) are handled in sim.engine.
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


# ---------- lever 6: Scottish demand growth ----------

# Typical Scottish monthly mean air temperature, deg C: Met Office UK climate averages 1991-2020, Scotland
# district (https://www.metoffice.gov.uk/research/climate/maps-and-data/uk-climate-averages), approximate.
# Only a fallback now: heat pumps follow residual.parquet temperature_2m_c (#45) where it exists. The
# monthly table alone OVERSTATES what heat pumps absorb (cold Scottish days tend to be calm, windy days mild).
SCOTLAND_MONTHLY_TEMP_C = np.array([3.3, 3.6, 4.9, 7.0, 9.7, 12.3, 13.9, 13.6, 11.7, 8.6, 5.6, 3.5])
HEATING_BASE_C = 15.5      # UK degree-day base: below this, homes need heating

# Hour-of-day shapes (UTC), relative; each is normalised to mean 1 below.
HEAT_PUMP_HOURLY = np.array([0.8, 0.8, 0.8, 0.8, 0.9, 1.1, 1.4, 1.5, 1.4, 1.1, 1.0, 0.9,
                             0.9, 0.9, 0.9, 1.0, 1.1, 1.3, 1.4, 1.3, 1.2, 1.1, 1.0, 0.9])
EV_HOURLY = np.array([1.6, 1.8, 1.8, 1.7, 1.4, 1.0, 0.6, 0.4, 0.4, 0.5, 0.5, 0.5,
                      0.5, 0.5, 0.5, 0.6, 0.8, 1.1, 1.3, 1.4, 1.5, 1.5, 1.5, 1.5])


def _mean_one(x: np.ndarray) -> np.ndarray:
    m = float(np.mean(x))
    return x / m if m > 0 else np.ones_like(x)


def demand_growth_mw(periods, zone: dict, temperature_c: np.ndarray | None = None) -> np.ndarray:
    """Lever 6: extra demand in one zone, MW per half hour, for {heat_pumps_gw, evs_gw, data_centres_gw}.

    Each GW figure is the average over the period; the shapes only move it around in time.
    Heat pumps: heating degrees (15.5 C minus the temperature) x a morning/evening shape, so mostly
    winter and mostly on cold days. temperature_c is the zone's real temperature per period
    (residual.parquet temperature_2m_c, population-weighted); where it is missing, or not given at
    all, the month's typical temperature is used. EVs: overnight and evening charging. Data centres: flat.
    """
    if not zone or not any(float(v or 0) for v in zone.values()):
        return np.zeros(len(periods))
    zone = {k: float(v or 0) for k, v in zone.items()}
    hour = np.asarray(periods.hour)
    month = np.asarray(periods.month) - 1
    temp = SCOTLAND_MONTHLY_TEMP_C[month]
    if temperature_c is not None:
        t = np.asarray(temperature_c, float)[: len(temp)]
        temp = np.where(np.isfinite(t), t, temp)
    heat = _mean_one(np.maximum(HEATING_BASE_C - temp, 0.0) * HEAT_PUMP_HOURLY[hour])
    ev = _mean_one(EV_HOURLY[hour])
    return 1000.0 * (float(zone.get("heat_pumps_gw", 0)) * heat + float(zone.get("evs_gw", 0)) * ev
                     + float(zone.get("data_centres_gw", 0)))


# ---------- lever 3: subsea link ----------

def subsea_link_mw(link: dict | None) -> tuple[float, float]:
    """Lever 3: (north MW, south MW) of link capacity straight to England, skipping B4 and B6.
    Availability derates the capacity on average (outages are not modelled one by one)."""
    if not link:
        return 0.0, 0.0
    zone = link.get("zone", "north_scotland")
    if zone not in ("north_scotland", "south_scotland"):
        raise ValueError(f"subsea_link.zone must be north_scotland or south_scotland, not {zone!r}")
    mw = 1000.0 * float(link.get("gw") or 0.0) * float(1.0 if link.get("availability") is None else link["availability"])
    return (mw, 0.0) if zone == "north_scotland" else (0.0, mw)
