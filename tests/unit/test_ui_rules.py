"""UI rules that need no window: profile names, number fields, colour contrast."""

import pytest

from macro_studio.core import ui_theme as ui
from macro_studio.tools.macro.tool import FieldError, clean_profile_name, read_number


@pytest.mark.parametrize("raw, expected", [
    ("Alt account", "Alt_account"),
    ("  Goliath-2 ", "Goliath-2"),
])
def test_profile_names_that_work(raw, expected):
    assert clean_profile_name(raw, ["Goliath", "Mutation"]) == (expected, None)


@pytest.mark.parametrize("raw", ["", "   ", "../outside", "a/b", "C:x", "x" * 41, "con?",
                                 "CON", "lpt1"])
def test_profile_names_that_would_break_the_file(raw):
    name, problem = clean_profile_name(raw, [])
    assert name is None and problem


def test_profile_name_never_replaces_another_profile():
    name, problem = clean_profile_name("mutation", ["Goliath", "Mutation"])
    assert name is None and "already exists" in problem


class _Box:
    def __init__(self, text):
        self.text = text

    def get(self):
        return self.text


def test_number_fields_name_the_bad_value():
    with pytest.raises(FieldError) as err:
        read_number(_Box("abc"), "Round delay", float)
    assert "Round delay" in str(err.value) and "abc" in str(err.value)
    with pytest.raises(FieldError, match="less than 0.5"):
        read_number(_Box("-5"), "Round delay", float, minimum=0.5)
    assert read_number(_Box(""), "Pop-up close X", blank=0) == 0
    assert read_number(_Box(" 12 "), "Gap", minimum=0) == 12


def _luminance(hex_color):
    rgb = [int(hex_color.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("fg, bg, minimum", [
    (ui.MUTE, ui.CANVAS, 4.5),          # sidebar labels
    (ui.MUTE, ui.SURFACE, 4.5),         # captions and hints on cards
    (ui.MUTE, ui.SURFACE_2, 4.5),       # placeholders, disabled text
    (ui.MUTE, ui.mix(ui.MUTE, ui.CANVAS, 0.16), 4.5),   # Idle status pill
    (ui.ERROR_TEXT, ui.SURFACE_2, 4.5),  # Stop button text
    (ui.BODY, ui.SURFACE_2, 4.5),       # console text
    (ui.BORDER_INPUT, ui.SURFACE, 3.0),  # input and switch outlines
    (ui.FOCUS, ui.CANVAS, 3.0),         # focus ring
    (ui.FOCUS, ui.SURFACE_2, 3.0),
    (ui.INK, ui.ACCENT_FILL, 4.5),       # white text and focus ring on the blue primary button
])
def test_text_and_controls_meet_wcag_contrast(fg, bg, minimum):
    assert contrast(fg, bg) >= minimum, f"{fg} on {bg}: {contrast(fg, bg):.2f}"


def test_number_fields_enforce_a_maximum_too():
    from macro_studio.tools.macro.validation import FieldError, read_number
    assert read_number(_Box("8000"), "Memory limit", minimum=50, maximum=8000) == 8000
    with pytest.raises(FieldError, match="more than 8000"):
        read_number(_Box("8001"), "Memory limit", minimum=50, maximum=8000)
    with pytest.raises(FieldError, match="less than 50"):
        read_number(_Box("49"), "Memory limit", minimum=50, maximum=8000)
