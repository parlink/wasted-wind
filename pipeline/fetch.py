"""Download and cache all raw inputs under data/raw/. Safe to stop and rerun.

Sources (field names confirmed against docs/samples/):
  bmu        Elexon PN, BOALF, BOD, B1610 for every wind BMU   -> data/raw/elexon/<DS>/<YYYY-MM>/batchNNN.parquet
  gb         Elexon MID (market index), demand outturn, NDF     -> data/raw/elexon/<DS>/<YYYY-MM>.parquet
  neso       NESO day-ahead constraint limits/flows, weekly OTF -> data/raw/neso/*.csv
  openmeteo  Archive (actual) + Previous Runs (as forecast) at each farm in reference/farms.csv
  towns      Archive temperature at the towns in reference/towns.csv -> data/raw/openmeteo/archive_towns/
                                                                -> data/raw/openmeteo/<api>/<farm_id>_<YYYY>.parquet

Caching rules (see tests/test_fetch.py):
- Each record is stored in exactly one file: the window containing its start time. Elexon's
  `from`/`to` are inclusive and return overlapping records, so responses are trimmed (`in_window`).
- A window ending within the last 7 days is saved as `<name>.partial.parquet` and refetched on
  every run; once settled it is saved as `<name>.parquet` and skipped. Exactly one exists per window.
- BMU batches follow data/raw/elexon/wind_bmus.json, an append-only list, so a refreshed reference
  never moves a unit between batches.

    python -m pipeline.fetch                              # everything, Jan 2024 -> today
    python -m pipeline.fetch --sources gb neso --start 2025-01-01
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

BASE = "https://data.elexon.co.uk/bmrs/api/v1"
ROOT = Path(__file__).resolve().parent.parent
RAW_ROOT = ROOT / "data" / "raw"
RAW = RAW_ROOT / "elexon"
BMU_DATASETS = ["PN", "BOALF", "BOD", "B1610"]
TIME_COLS = ["timeFrom", "timeTo", "acceptanceTime", "halfHourEndTime", "startTime", "publishTime"]
BATCH_SIZE = 10

# GB-wide Elexon datasets: (path, style of time parameters)
GB_DATASETS = {
    "MID": ("/datasets/MID/stream", "from_to"),
    "DEMAND_OUTTURN": ("/demand/outturn/stream", "settlement_date"),
    "NDF": ("/datasets/NDF/stream", "publish_time"),
}

NESO_API = "https://api.neso.energy/api/3/action/package_show"
NESO_FILES = {  # local name: (CKAN package id, resource name prefix)
    "day_ahead_constraints.csv": ("day-ahead-constraint-flows-and-limits",
                                  "Day Ahead Constraint Flows"),
    "congestion_weekly.csv": ("operational-transparency-forum-network-congestion-data",
                              "Network congestion"),
}

OPENMETEO = {  # api: (url, hourly variables)
    "archive": ("https://archive-api.open-meteo.com/v1/archive",
                "wind_speed_100m,wind_direction_100m,temperature_2m"),
    # previous_dayN = value from the run issued N days before the valid time.
    # day2 is always issued before D-1 10:00 for every hour of day D; day1 is not (it leaks),
    # so day1 is deliberately not downloaded.
    "previous_runs": ("https://previous-runs-api.open-meteo.com/v1/forecast",
                      "wind_speed_100m_previous_day2,wind_direction_100m_previous_day2,"
                      "temperature_2m_previous_day2"),
}

# Column whose value decides which window a record belongs to; None = windows already disjoint.
WINDOW_KEY = {"PN": "timeFrom", "BOALF": "timeFrom", "BOD": "timeFrom", "B1610": "halfHourEndTime",
              "MID": "startTime", "NDF": "publishTime", "DEMAND_OUTTURN": None}
SETTLE_DAYS = 7  # Open-Meteo archive lags ~5 days; Elexon publishes within a day


class IncompleteResponse(RuntimeError):
    """An HTTP 200 whose body doesn't reach the end of the window; never cached."""

log = logging.getLogger("fetch")
_session = requests.Session()


# ---------- pure helpers (tested) ----------

def month_windows(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Split [start, end) into windows that never cross a calendar-month boundary."""
    out = []
    cur = start
    while cur < end:
        nxt = datetime(cur.year + (cur.month == 12), cur.month % 12 + 1, 1, tzinfo=timezone.utc)
        out.append((cur, min(nxt, end)))
        cur = nxt
    return out


def year_windows(first: date, last: date) -> list[tuple[date, date]]:
    """Split the inclusive date range [first, last] into calendar years."""
    return [(max(first, date(y, 1, 1)), min(last, date(y, 12, 31)))
            for y in range(first.year, last.year + 1)]


def batches(ids: list[str], size: int = BATCH_SIZE) -> list[list[str]]:
    """Fixed-size chunks in the given order (use stable_unit_order for that order)."""
    return [ids[i:i + size] for i in range(0, len(ids), size)]


def stable_unit_order(existing: list[str], current: list[str]) -> list[str]:
    """Keep the stored order and append units not seen before, so old batches never change."""
    seen = set(existing)
    return list(existing) + sorted(u for u in set(current) if u not in seen)


def period_start_key(df: pd.DataFrame, ds: str) -> pd.Series | None:
    col = WINDOW_KEY[ds]
    if col is None or col not in df.columns:
        return None
    key = df[col]
    if ds == "B1610":  # halfHourEndTime is the end of the half hour (UTC, checked incl. BST)
        key = key - pd.Timedelta(minutes=30)
    return key


def in_window(df: pd.DataFrame, ds: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Keep only records that start in [start, end), so each lives in exactly one file."""
    key = period_start_key(df, ds) if len(df) else None
    if key is None:
        return df
    return df[(key >= pd.Timestamp(start)) & (key < pd.Timestamp(end))].reset_index(drop=True)


def is_final(window_end: datetime, now: datetime) -> bool:
    """A window is settled once it ended more than SETTLE_DAYS ago."""
    return window_end <= now - timedelta(days=SETTLE_DAYS)


def cache_paths(out: Path) -> tuple[Path, Path]:
    return out, out.with_name(out.stem + ".partial.parquet")


def check_complete(df: pd.DataFrame, ds: str, start: datetime, end: datetime) -> None:
    """GB-wide series are continuous: reject an empty body or one that stops >1 day early."""
    if not len(df):
        raise IncompleteResponse(f"{ds} {start:%Y-%m-%d}: empty response")
    key = period_start_key(df, ds)
    key = df["startTime"] if key is None else key  # DEMAND_OUTTURN has no trimming key
    if key.max() < pd.Timestamp(end) - pd.Timedelta(days=1):
        raise IncompleteResponse(f"{ds} {start:%Y-%m-%d}: data stops at {key.max()}, window ends {end}")


def wind_bmus(reference: list[dict]) -> list[str]:
    """Elexon BMU ids whose fuelType is WIND, from /reference/bmunits/all."""
    return sorted({u["elexonBmUnit"] for u in reference
                   if (u.get("fuelType") or "").upper() == "WIND" and u.get("elexonBmUnit")})


def records_to_frame(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame.from_records(records)
    for c in TIME_COLS:
        if c in df.columns:
            # B1610 halfHourEndTime has no 'Z' but is UTC: checked on BST and clock-change days.
            df[c] = pd.to_datetime(df[c], utc=True)
    return df


def gb_params(ds: str, start: datetime, end: datetime) -> dict:
    """Query params for a GB-wide dataset over [start, end)."""
    style = GB_DATASETS[ds][1]
    if style == "from_to":
        return {"from": f"{start:%Y-%m-%dT%H:%MZ}", "to": f"{end:%Y-%m-%dT%H:%MZ}"}
    if style == "publish_time":
        return {"publishDateTimeFrom": f"{start:%Y-%m-%dT%H:%MZ}",
                "publishDateTimeTo": f"{end:%Y-%m-%dT%H:%MZ}"}
    # settlement_date: inclusive dates; the endpoint ignores from/to entirely.
    return {"settlementDateFrom": f"{start:%Y-%m-%d}",
            "settlementDateTo": f"{end - timedelta(days=1):%Y-%m-%d}"}


def neso_resource_url(package: dict, name_prefix: str) -> str:
    return next(r["url"] for r in package["resources"] if r["name"].startswith(name_prefix))


def openmeteo_to_frame(resp: dict, farm_id: str) -> pd.DataFrame:
    """Hourly block of a single-location Open-Meteo response (requested with timezone=GMT)."""
    df = pd.DataFrame(resp["hourly"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df.insert(0, "farm_id", farm_id)
    return df


# ---------- network ----------

def get_json(url: str, params, tries: int = 6):
    for i in range(tries):
        try:
            r = _session.get(url, params=params, timeout=180)
            if r.status_code == 200:
                return r.json()
            if r.status_code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"{r.status_code} {r.url}: {r.text[:200]}")
            log.warning("retry %d %s: HTTP %d", i + 1, url, r.status_code)
        except requests.RequestException as e:
            log.warning("retry %d %s: %s", i + 1, url, e)
        time.sleep(min(2 ** i, 60))
    raise RuntimeError(f"gave up on {url} {params}")


def write_parquet(df: pd.DataFrame, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(out)  # atomic: a half-written file is never mistaken for a finished one


def write_cached(df: pd.DataFrame, out: Path, final: bool) -> None:
    """Write a window's file; final -> `<name>.parquet`, else `<name>.partial.parquet`."""
    final_path, partial_path = cache_paths(out)
    write_parquet(df, final_path if final else partial_path)
    if final:
        partial_path.unlink(missing_ok=True)


def load_wind_bmus() -> list[str]:
    """Wind BMUs in a stable, append-only order (data/raw/elexon/wind_bmus.json)."""
    ref_path, order_path = RAW / "reference_bmunits.json", RAW / "wind_bmus.json"
    if not ref_path.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        ref_path.write_text(json.dumps(get_json(f"{BASE}/reference/bmunits/all", {})))
    existing = json.loads(order_path.read_text()) if order_path.exists() else []
    order = stable_unit_order(existing, wind_bmus(json.loads(ref_path.read_text())))
    if order != existing:
        order_path.write_text(json.dumps(order, indent=0))
    return order


def fetch_bmu(ds: str, start: datetime, end: datetime, batch_i: int, units: list[str]) -> str:
    out = RAW / ds / f"{start:%Y-%m}" / f"batch{batch_i:03d}.parquet"
    final = is_final(end, datetime.now(timezone.utc))
    if final and out.exists():
        return "skip"
    params = [("from", f"{start:%Y-%m-%dT%H:%MZ}"), ("to", f"{end:%Y-%m-%dT%H:%MZ}")]
    params += [("bmUnit", u) for u in units]
    df = in_window(records_to_frame(get_json(f"{BASE}/datasets/{ds}/stream", params)), ds, start, end)
    write_cached(df, out, final)  # empty is legitimate here: units not yet built, no acceptances
    return f"{len(df)} rows"


def fetch_gb(ds: str, start: datetime, end: datetime) -> str:
    out = RAW / ds / f"{start:%Y-%m}.parquet"
    now = datetime.now(timezone.utc)
    final = is_final(end, now)
    if final and out.exists():
        return "skip"
    df = records_to_frame(get_json(f"{BASE}{GB_DATASETS[ds][0]}", gb_params(ds, start, end)))
    df = in_window(df, ds, start, end)
    check_complete(df, ds, start, min(end, now))
    write_cached(df, out, final)
    return f"{len(df)} rows"


def fetch_openmeteo(api: str, farm_id: str, lat: float, lon: float, first: date, last: date,
                    out_dir: str | None = None) -> str:
    out = RAW_ROOT / "openmeteo" / (out_dir or api) / f"{farm_id}_{first.year}.parquet"
    window_end = datetime(last.year, last.month, last.day, tzinfo=timezone.utc) + timedelta(days=1)
    final = is_final(window_end, datetime.now(timezone.utc))
    if final and out.exists():
        return "skip"
    url, hourly = OPENMETEO[api]
    resp = get_json(url, {"latitude": lat, "longitude": lon, "start_date": f"{first}",
                          "end_date": f"{last}", "hourly": hourly, "wind_speed_unit": "ms",
                          "timezone": "GMT"})
    df = openmeteo_to_frame(resp, farm_id)
    write_cached(df, out, final)
    time.sleep(0.5)  # stay well inside the free-tier rate limit
    return f"{len(df)} rows"


def fetch_neso(refresh: bool = False) -> None:
    for name, (pkg, prefix) in NESO_FILES.items():
        out = RAW_ROOT / "neso" / name
        # Each NESO file is a full snapshot that grows daily: refresh once it is a day old.
        if out.exists() and not refresh and time.time() - out.stat().st_mtime < 86400:
            log.info("neso %s: fresh, skip", name)
            continue
        url = neso_resource_url(get_json(NESO_API, {"id": pkg})["result"], prefix)
        r = _session.get(url, timeout=600)
        r.raise_for_status()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.with_suffix(".tmp").write_bytes(r.content)
        out.with_suffix(".tmp").replace(out)
        (out.parent / f"{name}.source.txt").write_text(url + "\n")
        log.info("neso %s: %.1f MB from %s", name, len(r.content) / 1e6, url)


def run_jobs(label: str, fn, jobs: list[tuple], workers: int) -> int:
    done = failed = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(fn, *j): j for j in jobs}
        for f in as_completed(futs):
            try:
                res = f.result()
                done += 1
            except Exception as e:  # noqa: BLE001 - keep going; a rerun retries failures
                failed += 1
                res = f"FAILED {e}"
            if done % 100 == 0 or res.startswith("FAILED"):
                log.info("%s [%d/%d] %s %s", label, done + failed, len(jobs), futs[f][:2], res)
    log.info("%s finished: %d ok, %d failed", label, done, failed)
    return failed


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--sources", nargs="+", default=["bmu", "gb", "neso", "openmeteo", "towns"])
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", default=None, help="exclusive; default = start of today UTC")
    p.add_argument("--datasets", nargs="+", default=BMU_DATASETS, help="for --sources bmu")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--refresh-neso", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    start = datetime.fromisoformat(a.start).replace(tzinfo=timezone.utc)
    end = (datetime.fromisoformat(a.end).replace(tzinfo=timezone.utc) if a.end
           else datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0))
    failed = 0

    if "bmu" in a.sources:
        units = load_wind_bmus()
        bs = batches(units)
        jobs = [(ds, s, e, i, b) for ds in a.datasets for s, e in month_windows(start, end)
                for i, b in enumerate(bs)]
        log.info("bmu: %d wind BMUs, %d batches, %d jobs", len(units), len(bs), len(jobs))
        failed += run_jobs("bmu", fetch_bmu, jobs, a.workers)

    if "gb" in a.sources:
        jobs = [(ds, s, e) for ds in GB_DATASETS for s, e in month_windows(start, end)]
        failed += run_jobs("gb", fetch_gb, jobs, a.workers)

    if "neso" in a.sources:
        fetch_neso(a.refresh_neso)

    if "openmeteo" in a.sources:
        farms = (pd.read_csv(ROOT / "reference" / "farms.csv")
                 .drop_duplicates("farm_id")[["farm_id", "lat", "lon"]])
        last_day = (end - timedelta(days=1)).date()
        jobs = [(api, f.farm_id, f.lat, f.lon, y0, y1) for api in OPENMETEO
                for f in farms.itertuples() for y0, y1 in year_windows(start.date(), last_day)]
        failed += run_jobs("openmeteo", fetch_openmeteo, jobs, workers=2)

    if "towns" in a.sources:      # population centres for the heat-pump temperature (residual.py)
        towns = pd.read_csv(ROOT / "reference" / "towns.csv")
        last_day = (end - timedelta(days=1)).date()
        jobs = [("archive", tw.town, tw.lat, tw.lon, y0, y1, "archive_towns") for tw in towns.itertuples()
                for y0, y1 in year_windows(start.date(), last_day)]
        failed += run_jobs("towns", fetch_openmeteo, jobs, workers=2)

    if failed:
        raise SystemExit(f"{failed} downloads failed; rerun to retry them")


if __name__ == "__main__":
    main()
