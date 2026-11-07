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
VERSION_FILE = PROCESSED_DIR / "DATA_VERSION"   # optional one-line release tag, e.g. "data-v1"


def resolve(name: str) -> tuple[Path, str]:
    """Return (path, source) for a contract file, source being 'processed' or 'fixtures'."""
    mode = os.environ.get("WW_DATA_SOURCE", "auto")
    real = REAL_FARMS if name == "farms.csv" else PROCESSED_DIR / name
    if mode != "fixtures" and real.is_file() and real.stat().st_size > 0:
        return real, "processed"
    if mode == "processed":
        raise FileNotFoundError(f"{real} missing and WW_DATA_SOURCE=processed")
    return FIXTURES_DIR / name, "fixtures"


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


def table(name: str) -> tuple[pd.DataFrame, str]:
    """A contract table (farms.csv or one of the parquet files) and where it came from."""
    path, source = resolve(name)
    return _read(path, path.stat().st_mtime, name), source


def json_file(name: str) -> tuple[dict, str]:
    path, source = resolve(name)
    return json.loads(path.read_text(encoding="utf-8")), source


def data_version(source: str) -> str:
    if source == "fixtures":
        return "fixtures"
    if VERSION_FILE.is_file():
        return VERSION_FILE.read_text().strip() or "unknown"
    return "unknown"


def signature(*names: str) -> tuple:
    """Identifies the exact data behind a result, for cache keys."""
    out = []
    for n in names:
        p, s = resolve(n)
        out.append((n, s, p.stat().st_mtime))
    return tuple(out)
