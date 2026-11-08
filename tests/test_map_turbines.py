"""Map turbines: a sharp spinning rotor, a plain black hub, and growth that carries on further when zooming in."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")


def test_no_nacelle_box_behind_the_hub():
    # the hub is one solid black circle; a rounded box beside it read as a translucent black square
    assert "nacelle" not in APP and "nacelle" not in CSS and "nacelle" not in HTML
    marker = APP[APP.index("const TURBINE_SVG"):APP.index("const turbineMarkers")]
    assert '<circle class="hub"' in marker and "<rect" in marker      # tower base only
    assert not re.search(r'class="hub"[^>]*(stroke|opacity)', marker)


def test_rotor_is_its_own_element_so_it_is_redrawn_at_its_real_size():
    # a spinning <g> inside the same SVG as the tower was drawn at its starting size and then stretched (blurry)
    marker = APP[APP.index("const TURBINE_SVG"):APP.index("const turbineMarkers")]
    assert marker.count("<svg") == 2 and 'class="tower-svg"' in marker and 'class="rotor"' in marker
    assert re.search(r"\.turbine \.rotor \{[^}]*animation: turbine-rotate", CSS)
    assert "@keyframes turbine-rotate" in CSS
    # the hub sits at (12, 10) of a 24 x 34 box, so the rotor turns about 50% / 29.41%
    assert "transform-origin: 50% 29.41%" in CSS and 'cx="12" cy="10"' in marker


def test_markers_are_smaller_than_before():
    m = re.search(r"\.turbine \{[^}]*width: calc\((\d+(?:\.\d+)?)px \* var\(--tscale", CSS)
    assert m and float(m.group(1)) <= 16          # was 20


NODE = shutil.which("node")


def _scale(js):
    block = APP[APP.index("const BASE_ZOOM"):APP.index("function zoomScaled")]
    out = subprocess.run([NODE, "-e", f"{block}\nconsole.log(JSON.stringify({js}))"], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_marker_scale_grows_with_zoom_and_keeps_growing_past_zoom_12():
    got = _scale("{ z: [3, 5.6, 7, 9, 10, 11, 12, 12.5, 13, 16, 22].map(z => markerScale(z)), cap: CAP_ZOOM, max: MAX_SCALE }")
    z = got["z"]
    assert z == sorted(z)                                   # never shrinks as you zoom in
    assert got["cap"] > 12                                  # was 11.05: the marker stopped growing there
    assert z[6] < got["max"]                                # still growing at zoom 12
    assert z[-1] == got["max"]                              # and capped so it never swamps the screen
    assert abs(z[1] - 1) < 1e-9                             # the default zoom is unchanged


def test_marker_keeps_the_map_librarys_absolute_positioning():
    # MapLibre pins a marker with position:absolute (.maplibregl-marker). A `position` on .turbine overrode it, so the
    # turbines sat in the page flow instead of on their coordinates and drifted when they resized while zooming.
    rule = re.search(r"\.turbine \{([^}]*)\}", CSS).group(1)
    assert "position" not in rule
    for sel in (".turbine .tower-svg, .turbine .rotor",):
        assert f"{sel} {{ position: absolute" in CSS         # the stacked SVGs are placed inside the marker box
