"""History tab: All shows a From-To range; Year / Month / Week show a dropdown of just those periods; Day one date box."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")


def test_the_date_row_has_a_period_dropdown_between_the_arrows():
    row = HTML[HTML.index('class="filter-row date-row"'):]
    row = row[:row.index("</div>")]
    assert row.index('id="range-prev"') < row.index('id="range-period"') < row.index('id="range-next"')
    assert '<select id="range-period"' in row


def test_sync_shows_the_dropdown_for_year_month_week_and_the_range_only_for_all():
    sync = APP[APP.index("function syncDateInputs"):APP.index("function setMode")]
    assert '"range-period"' in sync and "periodList(" in sync
    assert re.search(r"\{ year: \"Year\", month: \"Month\", week: \"Week\" \}", sync)


def test_a_typed_range_that_is_exactly_one_period_switches_to_its_picker():
    body = APP[APP.index("function setRangeFromInputs"):APP.index("function stepRange")]
    assert "inferMode(" in body and '"custom"' not in body.split("inferMode(")[0].split("state.mode =")[-1]
    assert "function inferMode" in APP


def test_choosing_from_the_dropdown_loads_that_period():
    assert re.search(r'\$\("range-period"\)\.addEventListener\("change"', APP)


NODE = shutil.which("node")


def _periods(js):
    keep = [re.search(p, APP, re.S).group(0) for p in (
        r"const dayFmt = new Intl\.DateTimeFormat\([^;]*;", r"const isoDay = [^;]*;", r"const monthFmt = [^;]*;",
        r"const DAY_MS = \d+;", r"function weekLabel\(s, e\) \{.*?\n\}", r"function periodList\(mode, first, end\) \{.*?\n\}")]
    out = subprocess.run([NODE, "-e", "\n".join(keep) + f"\nconsole.log(JSON.stringify({js}))"],
                         capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_period_lists_cover_the_data_newest_first():
    first, end = 'new Date("2024-01-01T00:00:00Z")', 'new Date("2026-11-07T00:00:00Z")'   # data: 1 Jan 2024 - 6 Nov 2026
    years = _periods(f'periodList("year", {first}, {end})')
    assert [o["label"] for o in years] == ["2026 (so far)", "2025", "2024"]
    assert [o["value"] for o in years] == ["2026-01-01", "2025-01-01", "2024-01-01"]
    months = _periods(f'periodList("month", {first}, {end})')
    assert len(months) == 35 and months[0]["label"] == "November 2026 (so far)" and months[-1]["value"] == "2024-01-01"
    assert {"value": "2026-10-01", "label": "October 2026"} in months
    weeks = _periods(f'periodList("week", {first}, {end})')
    assert len(weeks) == 149 and weeks[-1] == {"value": "2024-01-01", "label": "1–7 Jan 2024"}
    assert {"value": "2026-10-26", "label": "26 Oct – 1 Nov 2026"} in weeks           # the storm's week, Monday start
    assert weeks[0]["label"].endswith("(so far)")
    across = [w for w in weeks if w["value"] == "2024-12-30"][0]
    assert across["label"] == "30 Dec 2024 – 5 Jan 2025"
