"""#70: the guided tours. The config is read as text, so a UI change that breaks a step fails here, not on stage."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CONFIG = (STATIC / "tour-config.js").read_text(encoding="utf-8")
APP = (STATIC / "app.js").read_text(encoding="utf-8")


def _block(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i)]


TARGETS_BLOCK = _block(CONFIG, "const TARGETS = {", "\n  };")
FUNCS_BLOCK = _block(CONFIG, "const FUNCS = {", "\n  };")
TOURS_BLOCK = CONFIG[CONFIG.index("const TOURS = {"):]
TARGETS = dict(re.findall(r'^\s+"([\w.]+)": "((?:[^"\\]|\\.)*)",\s*$', TARGETS_BLOCK, re.M))
FUNCS = set(re.findall(r"^    (\w+):", FUNCS_BLOCK, re.M))


def _tours():
    """{ "1": text of tour 1, "2": text of tour 2 }"""
    a, b = TOURS_BLOCK.index('"1": { steps'), TOURS_BLOCK.index('"2": { steps')
    return {"1": TOURS_BLOCK[a:b], "2": TOURS_BLOCK[b:]}


def _unescape(s):
    return s.replace('\\"', '"')


def test_there_are_exactly_two_tours_with_ids_and_labels():
    tours = _tours()
    assert set(tours) == {"1", "2"}
    for name, text in tours.items():
        ids = re.findall(r'\bid: "([\w-]+)"', text)
        labels = re.findall(r'label: "((?:[^"\\]|\\.)*)"', text)
        assert len(ids) >= 10, f"tour {name} is too short to be a demo"
        assert len(ids) == len(set(ids)), f"tour {name}: duplicate step ids"
        assert len(labels) == len(ids) and all(l.strip() for l in labels)


def test_every_target_selector_still_exists_in_the_page():
    """Every #id and [data-attr="value"] used by a selector must be in index.html (dynamic .classes are not checked)."""
    assert len(TARGETS) > 25
    for name, sel in TARGETS.items():
        sel = _unescape(sel)
        for ident in re.findall(r"#([A-Za-z][\w-]*)", sel):
            assert f'id="{ident}"' in HTML, f"target {name}: #{ident} is not in index.html"
        for attr, val in re.findall(r'\[(data-[\w-]+)="([^"]+)"\]', sel):
            assert f'{attr}="{val}"' in HTML, f"target {name}: [{attr}={val}] is not in index.html"


def test_every_step_refers_to_a_defined_target_tab_and_function():
    tabs = set(re.findall(r'data-tab="(\w+)"', HTML))
    for name, text in _tours().items():
        for m in re.finditer(r'show: (\[[^\]]*\]|"[^"]*")', text):
            for t in re.findall(r'"([\w.]+)"', m.group(1)):
                assert t in TARGETS, f"tour {name}: show target {t!r} is not defined"
        for kind, t in re.findall(r'\["(click|set)", "([\w.]+)"', text):
            assert t in TARGETS, f"tour {name}: {kind} target {t!r} is not defined"
        for t in re.findall(r'\["tab", "(\w+)"\]', text):
            assert t in tabs, f"tour {name}: unknown tab {t!r}"
        for f in re.findall(r'\["call", "(\w+)"', text):
            assert f in FUNCS, f"tour {name}: unknown function {f!r}"


def test_engine_helpers_exist_in_the_app():
    # tour-config.js calls these app functions; if one is renamed, this says which
    for fn in ("setTab", "stopApReplay", "toggleApReplay", "selectFarm", "setPlacing", "onMapClickPlace"):
        assert re.search(rf"\bfunction {fn}\(", APP), f"app.js no longer has {fn}()"
    for fn in ("tab", "isRealData", "isReady", "beforeStep"):
        assert fn in FUNCS, f"the engine needs FUNCS.{fn}"


def test_set_values_match_real_options_and_dates_are_iso():
    for name, text in _tours().items():
        for t, v in re.findall(r'\["set", "([\w.]+)", (?:D\.\w+|"([^"]*)")\]', text):
            if t.endswith(".zone"):
                assert v in re.findall(r'<option value="(\w*)"', HTML), f"{v!r} is not a {t} option"
            if t == "ap.planner":
                assert v in re.findall(r'<option value="(\w+)"', HTML)
    for key, v in re.findall(r"(\w+): \"(\d{4}-\d{2}-\d{2})\"", CONFIG):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", v)


def test_the_site_shows_only_tour_1_and_tour_2_and_no_track_names():
    menu = _block(HTML, 'id="tour-menu"', "</header>")
    text = re.sub(r"<[^>]+>", " ", menu)
    assert "Tour 1" in text and "Tour 2" in text
    banned = re.compile(r"hrt|g-research|g research|judge|pitch", re.I)
    shown = [text] + [_unescape(l) for t in _tours().values()
                      for l in re.findall(r'label: "((?:[^"\\]|\\.)*)"', t)]
    for s in shown:
        assert not banned.search(s), f"track or pitch wording on the site: {s!r}"


def test_page_loads_the_tour_files_after_the_app():
    assert HTML.index('href="tour.css"') < HTML.index("</head>")
    assert HTML.index('src="app.js"') < HTML.index('src="tour-config.js"') < HTML.index('src="tour.js"')


# ---------- geometry, run under node when it is installed ----------

NODE = shutil.which("node")


def _node(js):
    script = f"""
    const vm = require('vm'), fs = require('fs');
    const sb = {{ console, URLSearchParams, setTimeout, clearTimeout, location: {{ search: '' }},
                  document: {{ getElementById: () => null, addEventListener() {{}} }} }};
    sb.window = sb; vm.createContext(sb);
    for (const f of ['tour-config.js', 'tour.js']) vm.runInContext(fs.readFileSync({json.dumps(str(STATIC))} + '/' + f, 'utf8'), sb);
    const g = sb.TourEngine._geometry;
    {js}
    """
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_card_stays_bottom_left_unless_it_would_cover_the_spotlight():
    got = _node("""
    const vp = {w: 1600, h: 900}, card = {w: 380, h: 200}, gap = 16;
    const panelTarget = {left: 1000, top: 100, width: 500, height: 400};
    const coversCorner = {left: 0, top: 600, width: 500, height: 300};
    const everywhere = {left: 0, top: 0, width: 1600, height: 900};
    console.log(JSON.stringify({
      panel: g.chooseCorner('bottom-left', card, vp, gap, panelTarget).corner,
      covered: g.chooseCorner('bottom-left', card, vp, gap, coversCorner).corner,
      none: g.chooseCorner('bottom-left', card, vp, gap, null).corner,
      worst: g.chooseCorner('bottom-left', card, vp, gap, everywhere).corner,
      right: g.chooseCorner('bottom-right', card, vp, gap, panelTarget).corner,
    }));""")
    assert got == {"panel": "bottom-left", "covered": "bottom-right", "none": "bottom-left",
                   "worst": "bottom-left", "right": "bottom-right"}


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_card_geometry_is_independent_of_the_screen_size():
    # the same layout at laptop and projector sizes (everything scaled) picks the same corner
    got = _node("""
    const out = [];
    for (const k of [1, 1.5, 2.4]) {
      const vp = {w: 1366 * k, h: 768 * k}, card = {w: 330 * k, h: 170 * k}, gap = 14 * k;
      const t = {left: 880 * k, top: 120 * k, width: 460 * k, height: 300 * k};
      out.push(g.chooseCorner('bottom-left', card, vp, gap, t).corner);
      const r = g.cardRectAt('bottom-left', card, vp, gap);
      out.push(Math.round(r.left / k), Math.round((vp.h - r.top - r.height) / k));
    }
    console.log(JSON.stringify(out));""")
    assert got == ["bottom-left", 14, 14] * 3


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_union_inflate_and_clamp():
    got = _node("""
    const u = g.unionRect([{left: 10, top: 20, width: 100, height: 50}, {left: 60, top: 100, width: 100, height: 40}]);
    const c = g.clampRect({left: -20, top: 880, width: 100, height: 100}, {w: 1600, h: 900});
    console.log(JSON.stringify({u, i: g.inflate({left: 10, top: 10, width: 20, height: 20}, 5), c}));""")
    assert got["u"] == {"left": 10, "top": 20, "width": 150, "height": 120}
    assert got["i"] == {"left": 5, "top": 5, "width": 30, "height": 30}
    assert got["c"] == {"left": 0, "top": 880, "width": 80, "height": 20}


def test_spotlight_border_stays_visible_on_lightly_dimmed_steps():
    # the border is white; on steps with dim ~0.1 the page around it is near white, so a dark outline is drawn outside it
    css = (STATIC / "tour.css").read_text(encoding="utf-8")
    spot = re.search(r"\.tour-spot \{([^}]*)\}", css).group(1)
    assert "var(--tour-spot-outline)" in spot and "--tour-spot-outline:" in css


def _tabs_per_step(text):
    """The tab each step opens (its first ["tab", ...] action)."""
    return re.findall(r'\{ id: "[\w-]+", do: \[\["tab", "(\w+)"\]', text)


def test_each_tour_follows_the_agreed_shape():
    # aradhyak0's structure: History 2-3 cards, Forecast ~4, Batteries 3-4, Simulate ~3, Method 1-2; 15 or fewer
    for name, text in _tours().items():
        tabs = _tabs_per_step(text)
        ids = re.findall(r'\bid: "([\w-]+)"', text)
        assert len(tabs) == len(ids), f"tour {name}: every step must start by opening its tab"
        assert len(ids) <= 15, f"tour {name}: {len(ids)} cards"
        n = {t: tabs.count(t) for t in ("reveal", "predict", "autopilot", "simulate", "method")}
        assert 2 <= n["reveal"] <= 3 and 3 <= n["predict"] <= 5 and 3 <= n["autopilot"] <= 4
        assert 2 <= n["simulate"] <= 4 and 1 <= n["method"] <= 2, f"tour {name}: {n}"
        order = [t for i, t in enumerate(tabs) if i == 0 or t != tabs[i - 1]]
        assert order == ["reveal", "predict", "autopilot", "simulate", "method"], f"tour {name}: {order}"


def test_cards_are_short():
    for name, text in _tours().items():
        for label in re.findall(r'label: "((?:[^"\\]|\\.)*)"', text):
            assert len(label.split()) <= 24, f"tour {name}: too long for a card: {label!r}"


def test_simulate_places_batteries_next_to_wind_farms():
    for name, text in _tours().items():
        assert len(re.findall(r'\["call", "placeBatteryNear", "\w+"\]', text)) >= 2, f"tour {name}"
