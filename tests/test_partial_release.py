"""Partial data releases (e.g. data-v1 = curtailment only) must never mix real and fake data
in one number, and fake data must always be labelled."""
import os

import pandas as pd
import pytest

from app import data


@pytest.fixture
def real_curtailment_only(tmp_path, monkeypatch):
    """A 'processed' dir with curtailment (fixture rows shifted by a year) and real-looking farms."""
    processed = tmp_path / "processed"
    processed.mkdir()
    c = pd.read_parquet(data.FIXTURES_DIR / "curtailment.parquet")
    c["period_start"] = c["period_start"] + pd.Timedelta(days=364)
    c.to_parquet(processed / "curtailment.parquet", index=False)
    farms = tmp_path / "farms.csv"
    farms.write_text((data.FIXTURES_DIR / "farms.csv").read_text())
    monkeypatch.setattr(data, "PROCESSED_DIR", processed)
    monkeypatch.setattr(data, "REAL_FARMS", farms)
    monkeypatch.setenv("WW_DATA_SOURCE", "auto")
    return processed


def _client():
    from fastapi.testclient import TestClient
    from app.server import app
    return TestClient(app)


def test_group_source_needs_every_member(real_curtailment_only):
    assert data.group_source("curtailment.parquet") == "processed"
    assert data.group_source("curtailment.parquet", "residual.parquet") == "fixtures"


def test_reveal_does_not_mix_fake_residual_into_real_curtailment(real_curtailment_only):
    c = _client()
    s = c.get("/api/reveal/summary").json()
    assert s["meta"]["source"] == "processed"
    assert s["totals"]["curtailed_twh"] > 0
    assert s["totals"]["by_boundary_twh"] is None          # needs real residual (#4)
    b = c.get("/api/reveal/boundaries").json()
    assert all(x["share_periods_binding"] is None for x in b["boundaries"])
    assert "residual" in b["meta"]["note"]


def test_simulator_uses_one_consistent_dataset(real_curtailment_only):
    from sim.engine import simulate
    r = simulate({})
    assert r["data_source"] == "fixtures"                  # residual not released -> all fixtures
    assert r["window"]["start"].startswith("2025-11-01")   # fixture dates, not the shifted real ones
    resp = _client().post("/api/scenario", json={"b4": {"mode": "offset", "mw": 100}}).json()
    assert resp["meta"]["source"] == "fixtures"


def test_simulator_switches_when_residual_arrives(real_curtailment_only):
    r = pd.read_parquet(data.FIXTURES_DIR / "residual.parquet")
    r["period_start"] = r["period_start"] + pd.Timedelta(days=364)
    r.to_parquet(real_curtailment_only / "residual.parquet", index=False)
    from sim.engine import simulate
    out = simulate({})
    assert out["data_source"] == "processed"
    assert out["window"]["start"].startswith("2026-10-31")
    s = _client().get("/api/reveal/summary").json()
    assert s["totals"]["by_boundary_twh"] is not None


def test_forecast_tab_stays_labelled_fake(real_curtailment_only):
    c = _client()
    assert c.get("/api/health").json()["meta"]["source"] == "processed"
    assert c.get("/api/forecast/tomorrow").json()["meta"]["source"] == "fixtures"


@pytest.fixture(autouse=True)
def _restore_env():
    old = os.environ.get("WW_DATA_SOURCE")
    yield
    if old is None:
        os.environ.pop("WW_DATA_SOURCE", None)
    else:
        os.environ["WW_DATA_SOURCE"] = old
