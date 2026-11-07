"""app.data source selection: real files win per file, but farms must match the curtailment source."""
import pandas as pd
import pytest

from app import data


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    processed = tmp_path / "processed"
    processed.mkdir()
    farms = tmp_path / "farms.csv"
    farms.write_text("bmu_id,farm_id\nT_X-1,x\n")
    monkeypatch.setattr(data, "PROCESSED_DIR", processed)
    monkeypatch.setattr(data, "REAL_FARMS", farms)
    monkeypatch.setenv("WW_DATA_SOURCE", "auto")
    return processed, farms


def test_real_farms_ignored_until_real_curtailment_exists(dirs):
    # reference/farms.csv is committed early, but its farm_ids won't match fixture curtailment
    assert data.resolve("farms.csv")[1] == "fixtures"


def test_real_farms_used_with_real_curtailment(dirs):
    processed, farms = dirs
    (processed / "curtailment.parquet").write_bytes(b"x")
    assert data.resolve("farms.csv") == (farms, "processed")
    assert data.resolve("curtailment.parquet")[1] == "processed"
    assert data.resolve("forecast.parquet")[1] == "fixtures"   # still per file otherwise


def test_forced_fixtures(dirs, monkeypatch):
    processed, _ = dirs
    (processed / "curtailment.parquet").write_bytes(b"x")
    monkeypatch.setenv("WW_DATA_SOURCE", "fixtures")
    assert data.resolve("curtailment.parquet")[1] == "fixtures"
    assert data.resolve("farms.csv")[1] == "fixtures"


def test_missing_contract_column_is_a_clear_error(tmp_path):
    p = tmp_path / "residual.parquet"
    pd.DataFrame({"zone": ["north_scotland"]}).to_parquet(p)
    with pytest.raises(ValueError, match="missing contract columns"):
        data._read(p, p.stat().st_mtime, "residual.parquet")
