"""Calibration: with every lever at its historical setting, does the simulator reproduce Part 1?

    python -m sim.calibrate     (or: make sim)
"""
from __future__ import annotations

import pandas as pd

from sim.engine import H, _baseline, load_inputs


def _frames():
    inp = load_inputs()
    base = _baseline(inp)
    sim = pd.DataFrame(base["cut_mw"] * H, index=inp.periods, columns=inp.bmu)
    hist = pd.DataFrame(inp.hist_curtailed_mwh, index=inp.periods, columns=inp.bmu)
    return inp, sim, hist


def calibration_by_month() -> dict:
    """{'YYYY-MM': (historical MWh, simulated MWh)} from one consistent dataset.

    Used by GET /api/method/calibration; both sides come from the simulator's inputs, so a
    partial data release never compares real history against a fake simulation.
    """
    _, sim, hist = _frames()
    s, h = sim.sum(axis=1), hist.sum(axis=1)
    months = s.index.strftime("%Y-%m")
    return {m: (float(hv), float(sv)) for (m, hv), sv in
            zip(h.groupby(months).sum().items(), s.groupby(months).sum().to_numpy())}


def calibration_report() -> dict:
    inp, sim, hist = _frames()
    h_tot, s_tot = float(hist.to_numpy().sum()), float(sim.to_numpy().sum())
    zone = pd.Series(inp.north, index=inp.bmu).map({True: "north_scotland", False: "south_scotland"})
    by_zone = {z: {"historical_mwh": round(float(hist.loc[:, zone == z].to_numpy().sum()), 1),
                   "simulated_mwh": round(float(sim.loc[:, zone == z].to_numpy().sum()), 1)} for z in zone.unique()}
    farm = pd.Series(inp.farm, index=inp.bmu)
    by_farm = pd.DataFrame({"historical_mwh": hist.sum().groupby(farm).sum(),
                            "simulated_mwh": sim.sum().groupby(farm).sum()})
    by_farm["error_pct"] = (by_farm["simulated_mwh"] / by_farm["historical_mwh"] - 1) * 100
    daily_h, daily_s = hist.sum(axis=1).resample("1D").sum(), sim.sum(axis=1).resample("1D").sum()
    return {
        "historical_twh": h_tot / 1e6, "simulated_twh": s_tot / 1e6,
        "error_pct": (s_tot / h_tot - 1) * 100 if h_tot else None,
        "daily_correlation": float(daily_h.corr(daily_s)) if len(daily_h) > 2 else None,
        "by_zone": by_zone,
        "by_farm": by_farm.sort_values("historical_mwh", ascending=False).round(1),
    }


def main():
    r = calibration_report()
    print(f"historical (Part 1, Method A): {r['historical_twh']:.4f} TWh")
    print(f"simulated, all levers historical: {r['simulated_twh']:.4f} TWh  ({r['error_pct']:+.2f}%)")
    print(f"daily correlation: {r['daily_correlation']:.3f}")
    for z, v in r["by_zone"].items():
        print(f"  {z:<15} hist {v['historical_mwh']:>12,.0f} MWh   sim {v['simulated_mwh']:>12,.0f} MWh")
    print("\nper farm (simulated allocation is cheapest-bid-first, so farms differ more than totals):")
    print(r["by_farm"].to_string())


if __name__ == "__main__":
    main()
