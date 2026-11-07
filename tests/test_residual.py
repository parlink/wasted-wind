"""Tests for pipeline.residual. Hand-made cases; the check on the real output skips until built."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pipeline import residual as rs

ROOT = Path(__file__).resolve().parent.parent


# ---------- NESO local time -> UTC ----------

def test_neso_times_to_utc_handles_both_clock_changes():
    raw = pd.DataFrame({
        "Constraint Group": ["SCOTEX"] * 6 + ["SSE_SP2"] * 2,
        "Date_ Time GMT_BST": [
            "2025-03-30T00:30:00", "2025-03-30T02:00:00",            # spring: 01:00-01:59 skipped
            "2025-10-26T01:00:00", "2025-10-26T01:00:00",            # autumn: first is BST, second GMT
            "2025-10-26T01:30:00", "2025-10-26T01:30:00",
            "2024-04-20T23:30", "2025-07-01T12:00:00"],               # NESO mixes two time formats
        "Limit_MW": [1, 2, 3, 4, 5, 6, 7, 8], "Flow_MW": [0] * 8})
    out = rs.parse_neso(raw)
    t = dict(zip(out.limit_mw, out.period_start))
    assert t[1] == pd.Timestamp("2025-03-30T00:30Z")
    assert t[2] == pd.Timestamp("2025-03-30T01:00Z")
    assert t[3] == pd.Timestamp("2025-10-26T00:00Z")
    assert t[4] == pd.Timestamp("2025-10-26T01:00Z")
    assert t[5] == pd.Timestamp("2025-10-26T00:30Z")
    assert t[6] == pd.Timestamp("2025-10-26T01:30Z")
    assert t[7] == pd.Timestamp("2024-04-20T22:30Z")
    assert t[8] == pd.Timestamp("2025-07-01T11:00Z")


def test_b4_series_switches_from_sse_sp_to_sse_sp2():
    neso = pd.DataFrame({"group": ["SSE_SP", "SSE_SP2", "SCOTEX"],
                         "period_start": pd.to_datetime(["2024-04-20T22:30Z", "2024-04-20T23:00Z",
                                                         "2024-04-20T23:00Z"]),
                         "limit_mw": [2400.0, 2000.0, 4400.0], "flow_mw": [1.0, 2.0, 3.0]})
    b = rs.boundary_series(neso)
    assert b.loc[pd.Timestamp("2024-04-20T22:30Z"), "b4_limit_mw"] == 2400
    assert b.loc[pd.Timestamp("2024-04-20T23:00Z"), "b4_limit_mw"] == 2000
    assert b.loc[pd.Timestamp("2024-04-20T23:00Z"), "b6_limit_mw"] == 4400


# ---------- residual formulas (issue #21) ----------

def one_period(**kw):
    base = dict(wind_n_mwh=1200.0, wind_s_mwh=500.0, curt_n_mwh=300.0, curt_s_mwh=50.0,
                b4_limit_mw=2400.0, b4_flow_mw=2600.0, b6_limit_mw=2600.0, b6_flow_mw=3000.0)
    base.update(kw)
    return pd.DataFrame({k: [v] for k, v in base.items()},
                        index=pd.DatetimeIndex([pd.Timestamp("2025-03-01T00:00Z")], name="period_start"))


def test_issue_21_example_both_boundaries_binding():
    # N: delivered 2400 MW (1200 MWh in the half hour) at a 2400 MW limit -> residual_N = 0.
    # S: delivered 1000 MW, limit 2600, binding -> residual_S = 2600 - 1000 - 2400 = -800.
    r = rs.compute_residual(one_period()).set_index("zone")
    assert r.loc["north_scotland", "residual_mw"] == pytest.approx(0.0)
    assert r.loc["south_scotland", "residual_mw"] == pytest.approx(-800.0)
    assert set(r.residual_method) == {"binding"}


def test_not_binding_uses_day_ahead_flow_capped_at_limit():
    r = rs.compute_residual(one_period(curt_n_mwh=0.0, curt_s_mwh=0.0, b4_flow_mw=1500.0,
                                       b6_flow_mw=9999.0)).set_index("zone")
    assert r.loc["north_scotland", "residual_mw"] == pytest.approx(1500 - 2400)
    # B6 flow above its limit while nothing was curtailed: treat the flow as the limit,
    # otherwise the simulator would create waste that never happened.
    assert r.loc["south_scotland", "residual_mw"] == pytest.approx(2600 - 1000 - 1500)
    assert set(r.residual_method) == {"flow_estimate"}


def test_south_subtracts_b4_limit_when_north_binding_and_flow_otherwise():
    bind_n = rs.compute_residual(one_period(curt_s_mwh=0.0, b6_flow_mw=2000.0)).set_index("zone")
    assert bind_n.loc["south_scotland", "residual_mw"] == pytest.approx(2000 - 1000 - 2400)


def test_missing_limits_are_filled_and_marked_fallback():
    df = pd.concat([one_period(), one_period(b4_limit_mw=np.nan, b4_flow_mw=np.nan)])
    df.index = pd.DatetimeIndex(["2025-03-01T00:00Z", "2025-03-01T00:30Z"], name="period_start")
    filled = rs.fill_boundaries(df, weekly=None)
    assert filled.b4_limit_mw.notna().all() and filled.b4_filled.tolist() == [False, True]
    r = rs.compute_residual(filled)
    north = r[r.zone == "north_scotland"].set_index("period_start")
    assert north.residual_method.tolist() == ["binding", "fallback"]
    assert north.residual_mw.notna().all()


# ---------- market price ----------

def test_market_price_uses_apx_and_ignores_zero_volume_n2ex():
    mid = pd.DataFrame({"startTime": pd.to_datetime(["2025-03-01T00:00Z"] * 2 + ["2025-03-01T00:30Z"]),
                        "dataProvider": ["APXMIDP", "N2EXMIDP", "N2EXMIDP"],
                        "price": [94.88, 0.0, 80.0], "volume": [921.3, 0.0, 10.0]})
    p = rs.market_price(mid)
    assert p[pd.Timestamp("2025-03-01T00:00Z")] == pytest.approx(94.88)
    assert p[pd.Timestamp("2025-03-01T00:30Z")] == pytest.approx(80.0)  # only N2EX traded


# ---------- assembled output ----------

def test_assemble_has_both_zones_every_period_and_contract_columns():
    idx = pd.date_range("2025-03-01T00:00Z", periods=4, freq="30min", name="period_start")
    zone = pd.DataFrame({"wind_n_mwh": 1000.0, "wind_s_mwh": 400.0, "curt_n_mwh": 0.0,
                         "curt_s_mwh": 0.0}, index=idx)
    bounds = pd.DataFrame({"b4_limit_mw": 2400.0, "b4_flow_mw": 2000.0, "b6_limit_mw": 4400.0,
                           "b6_flow_mw": 3000.0}, index=idx[[0, 1, 3]])     # one period missing
    price = pd.Series(50.0, index=idx)
    demand = pd.Series(30000.0, index=idx[:2])
    out = rs.assemble(zone, bounds, price, demand, weekly=None)
    rs.check_output(out)
    assert len(out) == 8
    assert out.groupby("period_start").zone.nunique().eq(2).all()
    assert out.gb_demand_mw.isna().sum() == 4  # nullable, never invented


OUT = ROOT / "data" / "processed" / "residual.parquet"


@pytest.mark.skipif(not OUT.exists(), reason="residual.parquet not built")
def test_built_output_matches_contract():
    rs.check_output(pd.read_parquet(OUT))
