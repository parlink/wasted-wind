"""Forecast tab: the 6-day strip follows the selected farm and says what it shows; tiny values stay readable."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")


def test_strip_has_a_scope_label_and_asks_for_the_selected_farm():
    assert 'id="week-scope"' in HTML
    body = APP[APP.index("async function loadWeek"):APP.index("function renderWeek")]
    assert "farm_id=" in body and "state.farm" in body
    render = APP[APP.index("function renderWeek"):APP.index("async function selectLead")]
    assert "\"week-scope\"" in render and "all farms" in render.lower()


def test_choosing_a_farm_on_the_forecast_tab_reloads_the_strip():
    sel = APP[APP.index("function selectFarm"):APP.index("// ---------- window controls")]
    assert "loadWeek" in sel


NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_axis_decimals_keep_tiny_values_readable():
    fn = re.search(r"function axisDecimals\(maxAbs\) \{.*?\n\}", APP, re.S).group(0)
    out = subprocess.run([NODE, "-e", fn + "\nconsole.log(JSON.stringify([50, 4, 0.3, 0.04, 0.004, 0].map(axisDecimals)))"],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [0, 1, 2, 3, 4, 0]     # 0.004 MWh must not read "0"


def test_farm_table_heading_names_the_chosen_day():
    # the table under the chart shows the chosen day; its heading must not keep saying "Tomorrow"
    assert 'id="pred-table-title"' in HTML and '$("pred-table-title").textContent' in APP
