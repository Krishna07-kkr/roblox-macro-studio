"""Hotkey rebinding rules and the text-size setting (spec 001, US7: FR-060, FR-061)."""

import pytest

from macro_studio.core import ui_theme as ui
from macro_studio.services.hotkeys import HotkeyConflict, HotkeyService
from macro_studio.tools.system.service import (
    ACTIONS,
    DEFAULT_KEYS,
    TEXT_SCALES,
    HotkeyBindings,
    normalize_text_scale,
)


# ------------------------------------------------------------------ text size
@pytest.mark.parametrize("value, expected", [(100, 100), (90, 90), (125, 125), (150, 150),
                                             (80, 100), (200, 100), ("125", 125), ("big", 100),
                                             (None, 100), (True, 100), (110.0, 110)])
def test_the_text_size_is_one_of_the_offered_sizes_or_100(value, expected):
    assert normalize_text_scale(value) == expected


def test_the_offered_sizes():
    assert TEXT_SCALES == (90, 100, 110, 125, 150)


def test_text_gets_bigger_and_smaller_but_never_unreadable():
    try:
        ui.set_text_scale(100)
        base = ui.sans(14)[1]
        ui.set_text_scale(150)
        assert ui.sans(14)[1] == -21 and base == -14
        ui.set_text_scale(90)
        assert ui.sans(14)[1] == -13
        ui.set_text_scale(150)
        assert ui.mono(12)[1] == -18
        ui.set_text_scale(7)                              # invalid -> back to normal
        assert ui.sans(14)[1] == -14
    finally:
        ui.set_text_scale(100)


# ------------------------------------------------------------------ hotkeys
class Actions:
    def __init__(self):
        self.fired = []

    def table(self):
        return {name: (lambda n=name: self.fired.append(n)) for name in ACTIONS}


def make(keys=None):
    hotkeys = HotkeyService(key_state=lambda vk: False)
    actions = Actions()
    return HotkeyBindings(hotkeys, actions.table), hotkeys, actions


def test_the_defaults_are_f1_to_f4():
    assert DEFAULT_KEYS == {"toggle_afk": "F1", "optimize": "F2", "arrange_grid": "F3",
                            "toggle_panel": "F4"}


def test_binding_all_defaults_works_and_reports_no_problems():
    bindings, hotkeys, _actions = make()
    assert bindings.bind_all(dict(DEFAULT_KEYS)) == {}
    assert set(hotkeys._bindings) >= set(DEFAULT_KEYS)


def test_pressing_a_bound_key_runs_that_action():
    bindings, hotkeys, actions = make()
    bindings.bind_all(dict(DEFAULT_KEYS))
    hotkeys._bindings["arrange_grid"].callback()
    assert actions.fired == ["arrange_grid"]


@pytest.mark.parametrize("key", ["F6", "F7", "Esc"])
def test_the_stop_and_pause_keys_cannot_be_used(key):
    bindings, _hk, _actions = make()
    with pytest.raises(HotkeyConflict):
        bindings.rebind("optimize", key)


def test_two_actions_cannot_share_a_key():
    bindings, _hk, _actions = make()
    bindings.bind_all(dict(DEFAULT_KEYS))
    with pytest.raises(HotkeyConflict) as err:
        bindings.rebind("optimize", "F3")
    assert "arrange_grid" in str(err.value)


def test_a_refused_rebind_keeps_the_old_key():
    bindings, hotkeys, _actions = make()
    bindings.bind_all(dict(DEFAULT_KEYS))
    with pytest.raises(HotkeyConflict):
        bindings.rebind("optimize", "F3")
    assert hotkeys._bindings["optimize"].combo == hotkeys._bindings["optimize"].combo
    assert bindings.keys["optimize"] == "F2"


def test_a_good_rebind_moves_the_action():
    bindings, hotkeys, actions = make()
    bindings.bind_all(dict(DEFAULT_KEYS))
    bindings.rebind("arrange_grid", "Ctrl+G")
    assert bindings.keys["arrange_grid"] == "Ctrl+G"
    hotkeys._bindings["arrange_grid"].callback()
    assert actions.fired == ["arrange_grid"]


def test_an_unknown_key_name_is_refused_with_a_message():
    bindings, _hk, _actions = make()
    with pytest.raises(HotkeyConflict, match="banana"):
        bindings.rebind("optimize", "banana")


def test_a_bad_saved_key_falls_back_to_the_default_and_is_reported():
    bindings, hotkeys, _actions = make()
    problems = bindings.bind_all({**DEFAULT_KEYS, "optimize": "F6"})
    assert "optimize" in problems and bindings.keys["optimize"] == "F2"
    assert "optimize" in hotkeys._bindings


def test_an_action_with_no_tool_behind_it_is_skipped_quietly():
    hotkeys = HotkeyService(key_state=lambda vk: False)
    bindings = HotkeyBindings(hotkeys, lambda: {"toggle_afk": lambda: None})
    assert bindings.bind_all(dict(DEFAULT_KEYS)) == {}
    assert set(hotkeys._bindings) == {"toggle_afk"}
