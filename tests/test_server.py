"""API tests against app/fixtures (forced with WW_DATA_SOURCE=fixtures)."""
import json
import os

import pandas as pd
import pytest

os.environ["WW_DATA_SOURCE"] = "fixtures"

from fastapi.testclient import TestClient  # noqa: E402

from app import contract  # noqa: E402
from app.data import FIXTURES_DIR  # noqa: E402
from app.server import app  # noqa: E402

client = TestClient(app)


# ---------- fixtures match the contract ----------

@pytest.mark.parametrize("name", list(contract.PARQUET_FILES))
def test_fixture_parquet_matches_contract(name):
    cols, ts_cols = contract.PARQUET_FILES[name]
    df = pd.read_parquet(FIXTURES_DIR / name)
    optional = contract.RESIDUAL_OPTIONAL if name == "residual.parquet" else []
    assert list(df.columns) == cols + optional
    assert len(df) > 0
    for c in ts_cols:
        assert str(df[c].dt.tz) == "UTC", c


def test_fixture_farms_match_contract():
    df = pd.read_csv(FIXTURES_DIR / "farms.csv")
    assert list(df.columns) == contract.FARMS_COLUMNS
    assert df["bmu_id"].is_unique
    assert set(df["zone"]) <= set(contract.ZONES)


def test_fixture_curtailment_invariants():
    df = pd.read_parquet(FIXTURES_DIR / "curtailment.parquet")
    farms = pd.read_csv(FIXTURES_DIR / "farms.csv").set_index("bmu_id")
    assert (df["curtailed_mwh_a"] >= 0).all()
    assert (df["curtailed_mwh_a"] <= df["planned_mwh"] + 1e-9).all()
    cap = df["bmu_id"].map(farms["capacity_mw"])
    assert (df["planned_mwh"] <= cap * 0.5 * 1.05).all()
    assert df["curtailed_mwh_a"].sum() > 0, "fixtures should contain some waste"
    assert not df.duplicated(["bmu_id", "period_start"]).any()


def test_fixture_schedule_invariants():
    df = pd.read_parquet(FIXTURES_DIR / "schedules.parquet")
    sc = json.loads((FIXTURES_DIR / "scorecard.json").read_text())
    cap = {b["battery_id"]: b["energy_mwh"] for b in sc["batteries"]}
    assert (df["soc_mwh"] >= -1e-9).all()
    assert (df["soc_mwh"] <= df["battery_id"].map(cap) + 1e-9).all()
    assert set(df["action"]) <= {"charge", "hold", "discharge"}
    assert (df["power_mw"] >= 0).all()
    rev = {(r["strategy"], r["dispatcher"]): r["revenue_gbp"] for r in sc["results"]}
    assert rev[("perfect", "rule")] >= rev[("forecast", "rule")] >= rev[("do_nothing", "rule")]


# ---------- endpoints ----------

def _ok(path):
    r = client.get(path)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["meta"]["source"] == "fixtures"
    assert body["meta"]["generated_at"].endswith("Z")
    return body


def test_health():
    assert _ok("/api/health")["status"] == "ok"


def test_farms():
    farms = _ok("/api/farms")["farms"]
    assert farms
    f = farms[0]
    assert set(f) == {"farm_id", "farm_name", "lat", "lon", "capacity_mw", "zone", "site_type", "bmu_ids"}
    assert isinstance(f["bmu_ids"], list) and f["bmu_ids"]


def test_reveal_summary_full_and_window():
    full = _ok("/api/reveal/summary")
    t = full["totals"]
    assert t["curtailed_twh"] > 0
    assert set(t["by_boundary_twh"]) == {"B4", "B6"}
    assert t["by_boundary_twh"]["B4"] + t["by_boundary_twh"]["B6"] == pytest.approx(t["curtailed_twh"], rel=1e-6)
    assert all(0 <= f["share_wasted"] <= 1 for f in full["farms"])
    farm_sum = sum(f["curtailed_mwh"] for f in full["farms"]) / 1e6
    assert farm_sum == pytest.approx(t["curtailed_twh"], rel=1e-6)

    start = _ok("/api/health")["range"]["start"][:10]
    one_day = _ok(f"/api/reveal/summary?start={start}&end={pd.Timestamp(start) + pd.Timedelta(days=1):%Y-%m-%d}")
    assert one_day["totals"]["curtailed_twh"] <= t["curtailed_twh"]


def test_reveal_bad_date_is_400():
    assert client.get("/api/reveal/summary?start=notadate").status_code == 400


def test_reveal_farm_series():
    farm_id = _ok("/api/farms")["farms"][0]["farm_id"]
    body = _ok(f"/api/reveal/farm/{farm_id}")
    s = body["series"]
    assert s and set(s[0]) == {"period_start", "planned_mwh", "accepted_mwh", "metered_mwh",
                               "curtailed_mwh_a", "curtailed_mwh_b"}
    assert s[0]["period_start"].endswith("Z")


def test_reveal_unknown_farm_404():
    assert client.get("/api/reveal/farm/not_a_farm").status_code == 404


def test_reveal_boundaries():
    b = _ok("/api/reveal/boundaries")["boundaries"]
    assert {x["boundary"] for x in b} == {"B4", "B6"}
    assert all(0 <= x["share_periods_binding"] <= 1 and x["approximate"] for x in b)


def test_forecast_tomorrow():
    body = _ok("/api/forecast/tomorrow")
    assert body["issued_at"].endswith("Z")
    h = body["farms"][0]["hours"]
    assert len(h) == 24
    assert set(h[0]) == {"hour_start", "pred_mwh", "p10_mwh", "p90_mwh"}


def test_forecast_backtest_farm_and_all():
    farm_id = _ok("/api/farms")["farms"][0]["farm_id"]
    one = _ok(f"/api/forecast/backtest?farm_id={farm_id}")
    assert one["farm_id"] == farm_id and one["series"]
    assert set(one["series"][0]) == {"hour_start", "pred_mwh", "actual_mwh", "persistence_mwh",
                                     "climatology_mwh", "wind_rule_mwh"}
    allf = _ok("/api/forecast/backtest")
    assert allf["farm_id"] == "all" and len(allf["series"]) == len(one["series"])


def test_forecast_metrics():
    m = _ok("/api/forecast/metrics")
    assert {"lightgbm", "persistence", "climatology", "wind_rule"} <= set(m["models"])


def test_autopilot_schedule_and_scorecard():
    s = _ok("/api/autopilot/schedule")
    assert s["batteries"] and len(s["batteries"][0]["steps"]) == 48
    assert {"period_start", "action", "power_mw", "soc_mwh", "reason"} <= set(s["batteries"][0]["steps"][0])
    bid = s["batteries"][0]["battery_id"]
    one = _ok(f"/api/autopilot/schedule?battery_id={bid}")
    assert [b["battery_id"] for b in one["batteries"]] == [bid]
    sc = _ok("/api/autopilot/scorecard")
    assert {r["strategy"] for r in sc["results"]} == set(contract.STRATEGIES)


def test_scenario_baseline_shape_and_cache():
    r1 = client.post("/api/scenario", json={})
    assert r1.status_code == 200, r1.text
    b = r1.json()
    for k in ("fingerprint", "cached", "elapsed_ms", "window", "scenario", "baseline", "delta",
              "batteries", "farms", "meta"):
        assert k in b
    for k in ("curtailed_twh", "by_boundary_twh", "payments_gbp", "replacement_gas_gbp", "co2_t", "homes_equiv"):
        assert k in b["scenario"] and k in b["baseline"] and k in b["delta"]
    # explicit historical settings normalise to the same fingerprint, and hit the cache
    r2 = client.post("/api/scenario", json={"b4": {"mode": "historical"}, "forecast_quality": "ours"})
    assert r2.json()["fingerprint"] == b["fingerprint"]
    assert r2.json()["cached"] is True


def test_scenario_rejects_bad_lever():
    assert client.post("/api/scenario", json={"b4": {"mode": "sideways"}}).status_code == 422


def test_method_calibration():
    c = _ok("/api/method/calibration")
    assert c["historical_twh"] > 0 and c["by_month"]


def test_index_served():
    r = client.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()


def test_autopilot_schedule_reports_available_days_and_step_results():
    s = _ok("/api/autopilot/schedule?strategy=reactive")
    first, last = s["available"]["first"], s["available"]["last"]
    assert first <= s["date"] <= last
    step = s["batteries"][0]["steps"][0]
    assert {"captured_curtailment_mwh", "revenue_gbp"} <= set(step)
    earlier = _ok(f"/api/autopilot/schedule?strategy=reactive&date={first}")
    assert earlier["date"] == first and earlier["batteries"]


def test_autopilot_schedule_unknown_date_is_empty_not_error():
    s = _ok("/api/autopilot/schedule?date=1999-01-01")
    assert s["batteries"] == [] and s["available"]["first"]


def test_wind_endpoint_shapes_and_caches(monkeypatch):
    from app import server
    calls = []

    def fake(lats, lons, day, recent):
        calls.append(day)
        return [{"hourly": {"wind_speed_100m": [10.0] * 24, "wind_direction_100m": [225.0] * 24}} for _ in lats]
    monkeypatch.setattr(server, "fetch_wind", fake)
    server._wind_cache.clear()
    r = client.get("/api/wind?date=2025-11-06")
    assert r.status_code == 200
    body = r.json()
    assert len(body["hours"]) == 24 and body["farms"] and len(body["farms"][0]["speed_ms"]) == 24
    client.get("/api/wind?date=2025-11-06")
    assert calls == ["2025-11-06"]                       # cached


def test_wind_endpoint_offline_is_503(monkeypatch):
    from app import server

    def boom(*a):
        raise ConnectionError("no wifi")
    monkeypatch.setattr(server, "fetch_wind", boom)
    server._wind_cache.clear()
    assert client.get("/api/wind?date=2025-11-07").status_code == 503


def test_schedule_marks_executed_half_hours_on_the_live_day():
    day = client.get("/api/autopilot/schedule?strategy=forecast").json()
    steps = day["batteries"][0]["steps"]
    done = [s for s in steps if s["executed"]]
    assert done and len(done) < len(steps)                       # some done, the rest still a plan
    assert all(s["period_start"] < steps[len(done)]["period_start"] for s in done)
    past = client.get("/api/autopilot/schedule?strategy=perfect").json()
    assert not any(s["executed"] for b in past["batteries"] for s in b["steps"])


def test_forecast_metrics_include_skill_by_days_ahead():
    # #49: one flat row per lead (pipeline.lead_skill "chart"), for the Predict tab's chart
    rows = _ok("/api/forecast/metrics")["skill_by_lead"]
    assert [r["lead_days"] for r in rows] == list(range(1, len(rows) + 1))
    assert {"lead_days", "mae_mwh", "baseline_mae_mwh", "wind_rule_mae_mwh", "recall", "n"} <= set(rows[0])

def test_week_endpoint_and_tomorrow_stays_the_day_ahead():
    # #49: fixtures have one live day (lead 1); /tomorrow is lead 1 and /week lists every live day
    t = _ok("/api/forecast/tomorrow")
    w = _ok("/api/forecast/week")
    assert w["days"] and w["days"][0]["lead_days"] == 1
    d0 = w["days"][0]
    assert {"date", "lead_days", "pred_mwh", "p10_mwh", "p90_mwh", "kind"} <= set(d0)
    total = sum(h["pred_mwh"] for f in t["farms"] for h in f["hours"])
    assert d0["pred_mwh"] == pytest.approx(total)
    assert _ok("/api/forecast/tomorrow?lead_days=1")["farms"] == t["farms"]


def test_week_can_follow_one_farm():
    # the 6-day strip shows the same scope as the big number: all farms, or the selected farm
    t = _ok("/api/forecast/tomorrow")
    f = t["farms"][0]
    w = _ok(f"/api/forecast/week?farm_id={f['farm_id']}")
    assert w["farm_id"] == f["farm_id"]
    assert w["days"][0]["pred_mwh"] == pytest.approx(sum(h["pred_mwh"] for h in f["hours"]))
    assert _ok("/api/forecast/week")["farm_id"] is None
    assert client.get("/api/forecast/week?farm_id=no_such_farm").status_code == 404
