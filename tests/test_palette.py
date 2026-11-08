"""One meaning per colour on every tab (see the palette note at the top of app/static/app.js):
raspberry = wind that was actually wasted, teal = what we produce (forecast, model, scenario, battery actions),
grey = baselines and plans. Paired splits use two tones of one colour."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def colours(label, key="color"):
    """The C.<name> used with a given label, in every place the label is drawn (legend or chart series)."""
    found = re.findall(rf'label: "{re.escape(label)}"[^}}]*?\b{key}: (C\.\w+)', APP)
    found += re.findall(rf'label: "{re.escape(label)}", data: [^}}]*?\b{key}: (C\.\w+)', APP)
    return set(found)


def test_palette_defines_the_two_families_and_nothing_still_uses_the_old_name():
    assert "C.planned" not in APP and "planned:" not in APP[:APP.index("function el(")]
    for name in ("ours", "oursDeep", "oursWash", "waste", "wasteDeep", "wasteWash", "wind"):
        assert re.search(rf"\b{name}: ", APP[:APP.index("function el(")]), name


def test_really_wasted_is_always_the_waste_colour():
    assert colours("Really wasted") == {"C.waste"}
    assert colours("actual", "borderColor") == {"C.waste"}


def test_our_forecast_model_and_scenario_are_always_ours():
    assert colours("Our forecast, made the day before") == {"C.ours"}
    assert colours("forecast", "borderColor") == {"C.ours"}
    assert colours("Expected waste") == {"C.ours"}
    assert colours("Our model") == {"C.ours"}
    assert re.findall(r'background: (C\.\w+) \} \}\), "Your scenario"', APP) == ["C.ours"]    # Simulate's legend
    assert 'by_boundary_twh[k] / max) * 100}%`, background: C.ours' in APP                     # and its bars


def test_paired_splits_use_two_tones_of_the_waste_colour():
    assert colours("Method A", "borderColor") == {"C.waste"} and colours("Method B", "borderColor") == {"C.wasteDeep"}
    assert 'b4, C.waste]' in APP and 'b6, C.wasteDeep]' in APP


def test_baselines_and_plans_are_grey_and_raspberry_is_not_used_for_battery_actions():
    assert colours("Simple windy-day rule") == {"C.neutral"}
    assert "C.ink2" in colours("Could have made (its plan)")
    m = re.search(r"const AP_COLORS = \{([^}]*)\}", APP).group(1)
    assert "C.waste" not in m and "charge: C.ours" in m and "discharge: C.oursDeep" in m


def test_map_circles_and_week_bars_follow_the_same_rule():
    assert re.search(r'"circle-color", predict \|\| simulate \? C\.ours', APP)     # forecast / scenario circles
    assert "classList.toggle(\"ours\"" in APP and ".map-legend.ours .dot" in CSS    # and the legend dot
    assert re.search(r"\.week-day \.wd-bar \{[^}]*background: var\(--planned\)", CSS)   # forecast bars are teal
