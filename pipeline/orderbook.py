"""#51: order-book signals from the Balancing Mechanism bid-offer data (BOD).

The BOD bands are the grid's order book for turning wind down: each unit states, per half hour,
the price it asks to be curtailed (`bid`, usually negative = the farm is paid) for a band of MW.
NESO accepts the cheapest first, so the shape of this book says who will be switched off.

Signals per UTC day (first bid band, pairId -1; +/-9999 "never accept" placeholders dropped):
  ob_bid_median_north_gbp_mwh   median bid of units north of B4 (reference/farms.csv zones)
  ob_bid_median_south_gbp_mwh   the same for south Scotland
  ob_cheap_bid_mw_north         MW of north bands bidding above -£10/MWh (the cheapest to curtail),
                                summed per half hour, averaged over the day

A Scotland-vs-rest-of-GB spread was checked and left out: the rest of GB bids sit almost flat at
about -£100/MWh (subsidised farms lose subsidy when curtailed; daily std ~£1.4 in early 2025),
so the spread is the north median shifted by a constant.

Timing: bids for day D are final only an hour before each half hour, so at the D-1 10:00 issue
time the latest complete day is D-2. `for_hours` maps each forecast hour to its D-2 signals.
"""
from __future__ import annotations

import pandas as pd

PLACEHOLDER = 9999.0
CHEAP_BID_GBP_MWH = -10.0
SIGNALS = ["ob_bid_median_north_gbp_mwh", "ob_bid_median_south_gbp_mwh", "ob_cheap_bid_mw_north"]


def daily_orderbook(bod: pd.DataFrame, farms: pd.DataFrame) -> pd.DataFrame:
    """bod: bmUnit, timeFrom, pairId, levelFrom, bid. Returns one row per UTC day (index)."""
    zone = farms.set_index("bmu_id").zone
    b = bod[(bod.pairId == -1) & (bod.bid.abs() < PLACEHOLDER)].copy()
    b["zone"] = b.bmUnit.map(zone)
    b = b[b.zone.notna()]
    b["day"] = b.timeFrom.dt.floor("D")
    b["mw"] = b.levelFrom.abs()
    med = b.groupby(["day", "zone"]).bid.median().unstack()
    north = b[(b.zone == "north_scotland") & (b.bid >= CHEAP_BID_GBP_MWH)]
    per_period = north.groupby([north.day, north.timeFrom.dt.floor("30min")]).mw.sum()
    cheap = per_period.groupby(level=0).mean()
    out = pd.DataFrame({
        "ob_bid_median_north_gbp_mwh": med.get("north_scotland"),
        "ob_bid_median_south_gbp_mwh": med.get("south_scotland"),
    })
    out["ob_cheap_bid_mw_north"] = cheap.reindex(out.index).fillna(0.0)
    return out.sort_index()


def for_hours(daily: pd.DataFrame, hour_start: pd.Series) -> pd.DataFrame:
    """Signals for each forecast hour from day D-2, plus asof_orderbook (end of D-2 = D-1 00:00)."""
    day = pd.DatetimeIndex(hour_start).normalize()
    src = day - pd.Timedelta(days=2)
    out = daily.reindex(index=src, columns=SIGNALS).reset_index(drop=True)   # missing -> NaN
    out["asof_orderbook"] = (src + pd.Timedelta(days=1)).to_numpy()
    out.index = hour_start.index
    return out


def load_daily(farms: pd.DataFrame) -> pd.DataFrame:
    """Order-book signals from all cached BOD files (Scottish units only)."""
    from pipeline import reconstruct as rc
    bod = rc.load_raw("BOD", set(farms.bmu_id), ["bmUnit", "timeFrom", "pairId", "levelFrom", "bid"])
    return daily_orderbook(bod, farms)
