"""Tests for the pure helpers in pipeline.fetch (no network)."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from pipeline import fetch

ROOT = Path(__file__).resolve().parent.parent


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_month_windows_cover_range_without_gaps():
    w = fetch.month_windows(utc(2024, 1, 15), utc(2024, 3, 2))
    assert w == [
        (utc(2024, 1, 15), utc(2024, 2, 1)),
        (utc(2024, 2, 1), utc(2024, 3, 1)),
        (utc(2024, 3, 1), utc(2024, 3, 2)),
    ]


def test_month_windows_empty_when_end_not_after_start():
    assert fetch.month_windows(utc(2024, 1, 1), utc(2024, 1, 1)) == []


def test_batches_chunk_in_given_order():
    ids = ["T_A-1", "T_B-1", "T_C-1", "T_D-1", "T_E-1"]
    assert fetch.batches(ids, size=2) == [["T_A-1", "T_B-1"], ["T_C-1", "T_D-1"], ["T_E-1"]]


def test_stable_unit_order_appends_new_units_so_old_batches_never_shift():
    # Review #19 point 5: a new BMU in a refreshed reference must not move units between batches.
    old = ["T_A-1", "T_C-1", "T_E-1"]
    new = fetch.stable_unit_order(old, ["T_B-1", "T_A-1", "T_C-1", "T_E-1"])
    assert new == ["T_A-1", "T_C-1", "T_E-1", "T_B-1"]
    assert fetch.batches(new, size=2)[0] == fetch.batches(old, size=2)[0]
    assert fetch.stable_unit_order([], ["T_B-1", "T_A-1"]) == ["T_A-1", "T_B-1"]


# ---------- review #19 point 1: Elexon `from`/`to` are inclusive and return overlapping records ----------

def test_in_window_keeps_each_record_in_exactly_one_month():
    # Real behaviour (2025-03-01 probe): from=06:00&to=07:00 returned PN rows starting 05:30 and 07:00.
    df = pd.DataFrame({"timeFrom": pd.to_datetime(
        ["2025-03-01T05:30Z", "2025-03-01T06:00Z", "2025-03-01T06:59Z", "2025-03-01T07:00Z"])})
    kept = fetch.in_window(df, "PN", utc(2025, 3, 1, 6), utc(2025, 3, 1, 7))
    assert kept["timeFrom"].tolist() == [pd.Timestamp("2025-03-01T06:00Z"),
                                         pd.Timestamp("2025-03-01T06:59Z")]


def test_in_window_b1610_uses_period_start():
    # halfHourEndTime is the END of the period: 07:00 end = 06:30 start, inside [06:00, 07:00).
    df = pd.DataFrame({"halfHourEndTime": pd.to_datetime(
        ["2025-03-01T06:00Z", "2025-03-01T06:30Z", "2025-03-01T07:00Z", "2025-03-01T07:30Z"])})
    kept = fetch.in_window(df, "B1610", utc(2025, 3, 1, 6), utc(2025, 3, 1, 7))
    assert kept["halfHourEndTime"].tolist() == [pd.Timestamp("2025-03-01T06:30Z"),
                                                pd.Timestamp("2025-03-01T07:00Z")]


def test_in_window_demand_outturn_untouched():
    # Settlement-date windows are already disjoint; a BST day starts at 23:00 UTC the day before.
    df = pd.DataFrame({"startTime": pd.to_datetime(["2025-06-30T23:00Z", "2025-07-01T00:00Z"])})
    assert len(fetch.in_window(df, "DEMAND_OUTTURN", utc(2025, 7, 1), utc(2025, 8, 1))) == 2


def test_in_window_empty_frame():
    assert fetch.in_window(pd.DataFrame(), "PN", utc(2025, 3, 1), utc(2025, 4, 1)).empty


# ---------- review #19 point 2: windows that may still change are never cached as final ----------

def test_window_is_final_only_once_settled():
    now = utc(2026, 11, 7, 18)
    assert fetch.is_final(utc(2026, 10, 1), now)            # > 7 days ago
    assert not fetch.is_final(utc(2026, 11, 1), now)        # recent month end
    assert not fetch.is_final(utc(2026, 11, 7), now)        # today


def test_cache_paths_mark_partial_files(tmp_path):
    final, partial = fetch.cache_paths(tmp_path / "PN" / "2026-11" / "batch000.parquet")
    assert final.name == "batch000.parquet"
    assert partial.name == "batch000.partial.parquet"


def test_write_cached_final_replaces_partial(tmp_path):
    out = tmp_path / "x.parquet"
    df = pd.DataFrame({"a": [1]})
    fetch.write_cached(df, out, final=False)
    assert (tmp_path / "x.partial.parquet").exists() and not out.exists()
    fetch.write_cached(df, out, final=True)
    assert out.exists() and not (tmp_path / "x.partial.parquet").exists()


# ---------- review #19 point 4: a truncated GB-wide response is never cached ----------

def test_check_complete_rejects_empty_and_truncated():
    end = utc(2025, 4, 1)
    ok = pd.DataFrame({"startTime": pd.to_datetime(["2025-03-31T23:30Z"])})
    fetch.check_complete(ok, "MID", utc(2025, 3, 1), end)
    for bad in (pd.DataFrame(), pd.DataFrame({"startTime": pd.to_datetime(["2025-03-20T00:00Z"])})):
        try:
            fetch.check_complete(bad, "MID", utc(2025, 3, 1), end)
        except fetch.IncompleteResponse:
            pass
        else:
            raise AssertionError("should have rejected")


def test_wind_bmus_from_reference_filters_wind_and_dedupes():
    ref = [
        {"elexonBmUnit": "T_SGRWO-1", "fuelType": "WIND"},
        {"elexonBmUnit": "T_SGRWO-1", "fuelType": "WIND"},
        {"elexonBmUnit": "T_DAMC-1", "fuelType": "CCGT"},
        {"elexonBmUnit": None, "fuelType": "WIND"},
        {"elexonBmUnit": "E_X-1", "fuelType": None},
    ]
    assert fetch.wind_bmus(ref) == ["T_SGRWO-1"]


def test_records_to_frame_parses_times_as_utc():
    # Shape copied from a real PN stream response (docs/samples/elexon_pn_stream.json).
    recs = [{"dataset": "PN", "settlementDate": "2025-03-01", "settlementPeriod": 2,
             "timeFrom": "2025-03-01T00:30:00Z", "timeTo": "2025-03-01T00:59:00Z",
             "levelFrom": 223, "levelTo": 223, "nationalGridBmUnit": "SGRWO-1",
             "bmUnit": "T_SGRWO-1"}]
    df = fetch.records_to_frame(recs)
    assert len(df) == 1
    assert str(df["timeFrom"].dt.tz) == "UTC"
    assert df.loc[0, "timeTo"] == pd.Timestamp("2025-03-01T00:59:00Z")


def test_records_to_frame_empty():
    assert fetch.records_to_frame([]).empty


# ---------- GB-wide Elexon datasets: each endpoint names its time params differently ----------

def test_gb_params_mid_uses_from_to():
    p = fetch.gb_params("MID", utc(2025, 3, 1), utc(2025, 4, 1))
    assert p == {"from": "2025-03-01T00:00Z", "to": "2025-04-01T00:00Z"}


def test_gb_params_demand_outturn_uses_inclusive_settlement_dates():
    # /demand/outturn/stream ignores from/to; settlementDateTo is inclusive.
    p = fetch.gb_params("DEMAND_OUTTURN", utc(2025, 3, 1), utc(2025, 4, 1))
    assert p == {"settlementDateFrom": "2025-03-01", "settlementDateTo": "2025-03-31"}


def test_gb_params_ndf_uses_publish_time():
    p = fetch.gb_params("NDF", utc(2025, 3, 1), utc(2025, 4, 1))
    assert p == {"publishDateTimeFrom": "2025-03-01T00:00Z", "publishDateTimeTo": "2025-04-01T00:00Z"}


# ---------- NESO ----------

def test_neso_resource_url_picks_by_name_prefix():
    meta = {"resources": [
        {"name": "Network Diagram Scotland", "url": "https://x/diagram.pdf"},
        {"name": "Day Ahead Constraint Flows and Limits", "url": "https://x/da-v1.5.csv"},
    ]}
    assert fetch.neso_resource_url(meta, "Day Ahead Constraint Flows") == "https://x/da-v1.5.csv"


# ---------- Open-Meteo ----------

def test_openmeteo_to_frame_from_real_sample():
    sample = json.loads((ROOT / "docs/samples/openmeteo_previous_runs.json").read_text())
    df = fetch.openmeteo_to_frame(sample["response"], "seagreen")
    assert list(df.columns[:2]) == ["farm_id", "time"]
    assert (df["farm_id"] == "seagreen").all()
    assert str(df["time"].dt.tz) == "UTC"
    assert "wind_speed_100m_previous_day1" in df.columns
    assert len(df) == len(sample["response"]["hourly"]["time"])


def test_previous_runs_request_has_no_leaky_day1_fields():
    # Review #19 point 6: previous_day1 is issued after D-1 10:00 for later hours of day D.
    assert "previous_day1" not in fetch.OPENMETEO["previous_runs"][1]
    assert "wind_speed_100m_previous_day2" in fetch.OPENMETEO["previous_runs"][1]


def test_year_windows_clip_to_range():
    w = fetch.year_windows(date(2024, 1, 1), date(2025, 6, 30))
    assert w == [(date(2024, 1, 1), date(2024, 12, 31)), (date(2025, 1, 1), date(2025, 6, 30))]


# ---------- reference/farms.csv (CONTRACT.md section 1) ----------

FARM_COLS = ["bmu_id", "farm_id", "farm_name", "lat", "lon", "capacity_mw", "zone",
             "site_type", "repd_ref_id"]


def test_farms_csv_matches_contract():
    farms = pd.read_csv(ROOT / "reference/farms.csv", dtype={"repd_ref_id": "string"})
    assert list(farms.columns) == FARM_COLS
    assert 25 <= len(farms) <= 50
    assert farms["bmu_id"].is_unique
    assert set(farms["zone"]) <= {"north_scotland", "south_scotland"}
    assert set(farms["site_type"]) <= {"onshore", "offshore"}
    assert (farms["capacity_mw"] > 0).all()
    assert farms["farm_id"].str.fullmatch(r"[a-z0-9_]+").all()
    # Scotland and its waters, including Shetland.
    assert farms["lat"].between(54.6, 61.0).all() and farms["lon"].between(-8.0, -0.5).all()
    # A farm sits in exactly one zone, with one name and one location.
    per_farm = farms.groupby("farm_id")[["zone", "farm_name", "lat", "lon"]].nunique()
    assert (per_farm == 1).all().all()


def test_farms_csv_includes_modo_validation_farms():
    farms = pd.read_csv(ROOT / "reference/farms.csv")
    assert {"seagreen", "moray_east", "moray_west"} <= set(farms["farm_id"])
