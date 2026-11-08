"""Batteries tab, simplified: one result said once, bars on the same months, details behind a link, a grouped log."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
TAB = HTML[HTML.index('id="tab-autopilot"'):HTML.index('id="tab-method"')]


def _words(html):
    return len(re.sub(r"<[^>]+>", " ", html).split())


def test_the_result_is_said_once():
    assert 'id="ap-plain"' not in TAB and 'id="ap-plain-note"' not in TAB   # the headline itself is the plain result
    body = APP[APP.index("function renderScorecard"):APP.index("async function loadApDay")]
    assert "gain.more" in body and 'countUp($("ap-headline")' in body


def test_numbers_and_rare_options_are_behind_a_click():
    numbers = TAB[TAB.index('id="ap-numbers"'):]
    assert numbers.index('id="ap-scorecard"') < numbers.index("</details>")
    more = TAB[TAB.index('id="ap-more"'):]
    assert more.index('id="ap-planner"') < more.index("</details>")


def test_the_tab_text_is_short():
    intro = re.search(r'<p class="tab-intro">(.*?)</p>', TAB, re.S).group(1)
    assert _words(intro) <= 30
    sub = APP[APP.index("function renderScorecard"):APP.index("async function loadApDay")]
    assert "Bars and table below" not in sub and "Share of the best possible earnings a battery makes" not in sub


def test_labels_are_plain_words():
    assert "optimiser" not in TAB.lower().replace("(optimiser)", "")      # only as a hint in brackets
    assert re.search(r'<option value="forecast">With our forecast</option>', TAB)
    assert re.search(r'<option value="reactive">Without a forecast</option>', TAB)


def test_the_replay_highlight_follows_grouped_log_rows():
    hl = APP[APP.index("function highlightApStep"):APP.index("function stopApReplay")]
    assert "dataset.from" in hl and "dataset.to" in hl


def test_the_tour_opens_more_options_before_choosing_a_planner():
    cfg = (STATIC / "tour-config.js").read_text(encoding="utf-8")
    step = re.search(r'\{ id: "planner", do: \[(.*?)\], show', cfg).group(1)
    assert step.index('"openMore"') < step.index('"ap.planner"')
    assert "openMore:" in cfg


NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_log_groups_merge_runs_of_the_same_row():
    fn = re.search(r"function logGroups\(keys\) \{.*?\n\}", APP, re.S).group(0)
    js = fn + '\nconsole.log(JSON.stringify(logGroups(["w", "w", "w", "s1", "s2", "w", "w"])))'
    out = subprocess.run([NODE, "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [{"from": 0, "to": 2}, {"from": 3, "to": 3}, {"from": 4, "to": 4}, {"from": 5, "to": 6}]
