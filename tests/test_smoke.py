"""Keeps CI green before real tests exist; checks the shared scaffolding is in place."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_contract_and_rules_exist():
    assert (ROOT / "docs" / "CONTRACT.md").is_file()
    assert (ROOT / "CLAUDE.md").is_file()


def test_packages_import():
    import app, dispatch, pipeline, sim  # noqa: F401
