"""Load contract files, preferring Person A's real data and falling back to app/fixtures/.

Per file: data/processed/<name> (or reference/farms.csv) if it exists, else the fixture.
Set WW_DATA_SOURCE=fixtures to force fixtures, or =processed to fail instead of falling back.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

import pandas as pd

from app import contract

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "app" / "fixtures"
PROCESSED_DIR = ROOT / "data" / "processed"
REAL_FARMS = ROOT / "reference" / "farms.csv"
# data/processed/DATA_VERSION: optional one-line release tag, e.g. "data-v1"


def _usable(p: Path) -> bool:
    return p.is_file() and p.stat().st_size > 0


def resolve(name: str, source: str | None = None) -> tuple[Path, str]:
    """Return (path, source) for a contract file, source being 'processed' or 'fixtures'.

    reference/farms.csv is only used once real curtailment exists, because its farm_ids
    don't match the fixture curtailment (the map would show farms with no data).
    Pass source= to pin a file to one side (see group_source).
    """
    if source == "fixtures":
        return FIXTURES_DIR / name, "fixtures"
    mode = os.environ.get("WW_DATA_SOURCE", "auto")
    if name == "farms.csv":
        real = REAL_FARMS
        ok = _usable(REAL_FARMS) and _usable(PROCESSED_DIR / "curtailment.parquet")
    else:
        real = PROCESSED_DIR / name
        ok = _usable(real)
    if mode != "fixtures" and ok:
        return real, "processed"
    if mode == "processed" or source == "processed":
        raise FileNotFoundError(f"{real} missing (WW_DATA_SOURCE={mode}, source={source})")
    return FIXTURES_DIR / name, "fixtures"


def group_source(*names: str) -> str:
    """'processed' only if every file in the group is real, else 'fixtures'.

    Numbers that combine several files (B4/B6 split, the simulator) must take them all from
    one side: real curtailment with fake residual would be meaningless.
    """
    return "processed" if all(resolve(n)[1] == "processed" for n in names) else "fixtures"


def _check_columns(df: pd.DataFrame, required: list[str], path: Path) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing contract columns {missing} (see docs/CONTRACT.md)")


@lru_cache(maxsize=16)
def _read(path: Path, mtime: float, name: str) -> pd.DataFrame:
    if name == "farms.csv":
        df = pd.read_csv(path, dtype={"repd_ref_id": "string"})
        _check_columns(df, contract.FARMS_COLUMNS, path)
        return df
    cols, ts_cols = contract.PARQUET_FILES[name]
    df = pd.read_parquet(path)
    _check_columns(df, cols, path)
    for c in ts_cols:
        df[c] = pd.to_datetime(df[c], utc=True)
    return df


def table(name: str, source: str | None = None) -> tuple[pd.DataFrame, str]:
    """A contract table (farms.csv or one of the parquet files) and where it came from."""
    path, source = resolve(name, source)
    return _read(path, path.stat().st_mtime, name), source


def json_file(name: str) -> tuple[dict, str]:
    path, source = resolve(name)
    return json.loads(path.read_text(encoding="utf-8")), source


def data_version(source: str) -> str:
    if source == "fixtures":
        return "fixtures"
    version_file = PROCESSED_DIR / "DATA_VERSION"
    if version_file.is_file():
        return version_file.read_text().strip() or "unknown"
    return "unknown"


def signature(*names: str, source: str | None = None) -> tuple:
    """Identifies the exact data behind a result, for cache keys."""
    out = []
    for n in names:
        p, s = resolve(n, source)
        out.append((n, s, p.stat().st_mtime))
    return tuple(out)


def lead_days(fc: pd.DataFrame) -> pd.Series:
    """#49: days between a forecast's issue date and the day it is for (1 = the day-ahead).
    The `lead_days` column if present, else derived (fixtures and files before data-v15)."""
    if "lead_days" in fc:
        return fc["lead_days"].astype(int)
    return (fc["hour_start"].dt.normalize() - fc["issued_at"].dt.normalize()).dt.days
