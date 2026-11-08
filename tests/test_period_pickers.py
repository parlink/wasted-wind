"""History tab: All shows a From-To range; Day one date picker; Month and Week a month / week picker (or, in browsers
without one, the date picker snapping to the month or week of the day picked); Year a short list of years."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")


def _row():
    row = HTML[HTML.index('class="filter-row date-row"'):]
    return row[:row.index("</div>")]


def test_the_date_row_has_a_year_list_and_month_and_week_pickers_between_the_arrows():
    row = _row()
    for ident in ("range-period", "range-month", "range-week", "range-start"):
        assert row.index('id="range-prev"') < row.index(f'id="{ident}"') < row.index('id="range-next"'), ident
    assert '<input type="month" id="range-month"' in row and '<input type="week" id="range-week"' in row


def test_each_mode_shows_its_own_control():
    sync = APP[APP.index("function syncDateInputs"):APP.index("function setMode")]
    for ident in ('"range-period"', '"range-month"', '"range-week"', '"range-start"', '"range-end"'):
        assert ident in sync, ident
    assert "NATIVE[" in sync                         # month / week pickers only where the browser has them


def test_without_a_month_or_week_picker_the_date_picker_snaps_to_the_period():
    handler = APP[APP.index('$("range-start").addEventListener("change"'):]
    handler = handler[:handler.index("\n  $(")]
    assert '"month"' in handler and '"week"' in handler and "showPeriod(state.mode" in handler
    assert "state.range = defaultRange(mode, day)" in APP          # showPeriod snaps the day to its period


def test_a_typed_range_that_is_exactly_one_period_switches_to_its_picker():
    body = APP[APP.index("function setRangeFromInputs"):APP.index("function stepRange")]
    assert "inferMode(" in body and "function inferMode" in APP


def test_choosing_a_year_month_or_week_loads_that_period():
    for ident in ("range-period", "range-month", "range-week"):
        assert re.search(rf'\$\("{ident}"\)\.addEventListener\("change"', APP), ident


NODE = shutil.which("node")


def _run(js, *funcs):
    keep = [re.search(r"const DAY_MS = \d+;", APP).group(0), re.search(r"const isoDay = [^;]*;", APP).group(0)]
    keep += [re.search(rf"function {f}\(.*?\n\}}", APP, re.S).group(0) for f in funcs]
    out = subprocess.run([NODE, "-e", "\n".join(keep) + f"\nconsole.log(JSON.stringify({js}))"],
                         capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_iso_weeks_match_the_apps_monday_blocks():
    d = lambda s: f'new Date("{s}T00:00:00Z")'  # noqa: E731
    got = _run("[" + ", ".join(f"isoWeekOf({d(x)})" for x in
               ("2024-01-01", "2026-10-26", "2026-10-27", "2026-11-01", "2024-12-30", "2026-12-28")) + "]", "isoWeekOf")
    # 1 Jan 2024 (the data's first day) is a Monday and starts ISO week 1, so the app's 7-day blocks are ISO weeks
    assert got == ["2024-W01", "2026-W44", "2026-W44", "2026-W44", "2025-W01", "2026-W53"]
    back = _run('["2024-W01", "2026-W44", "2025-W01", "2026-W53"].map((w) => isoDay(mondayOfIsoWeek(w)))', "mondayOfIsoWeek")
    assert back == ["2024-01-01", "2026-10-26", "2024-12-30", "2026-12-28"]


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_year_list_covers_the_data_newest_first():
    got = _run('periodList("year", new Date("2024-01-01T00:00:00Z"), new Date("2026-11-07T00:00:00Z"))', "periodList")
    assert [o["label"] for o in got] == ["2026 (so far)", "2025", "2024"]
    assert [o["value"] for o in got] == ["2026-01-01", "2025-01-01", "2024-01-01"]
