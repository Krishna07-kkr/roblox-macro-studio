"""HotkeyService: one poller for every global hotkey (Constitution II)."""

import pytest

from macro_studio.services.hotkeys import HotkeyConflict, HotkeyService, parse_key


class FakeKeyboard:
    """Stands in for GetAsyncKeyState: a set of virtual-key codes held down."""

    def __init__(self):
        self.down = set()

    def __call__(self, vk):
        return vk in self.down


VK_F1, VK_F3, VK_F6, VK_CONTROL, VK_G = 0x70, 0x72, 0x75, 0x11, 0x47


def test_parse_key():
    assert parse_key("F3") == ((), VK_F3)
    assert parse_key("ctrl+g") == ((VK_CONTROL,), VK_G)
    assert parse_key("Esc") == ((), 0x1B)
    with pytest.raises(ValueError):
        parse_key("NotAKey")


def test_callback_fires_once_per_press():
    kb, calls = FakeKeyboard(), []
    hk = HotkeyService(key_state=kb)
    hk.bind("arrange_grid", "F3", lambda: calls.append(1))
    kb.down = {VK_F3}
    hk.poll_once()
    hk.poll_once()  # still held: no repeat
    kb.down = set()
    hk.poll_once()
    kb.down = {VK_F3}
    hk.poll_once()
    assert calls == [1, 1]


def test_modifier_combo_needs_the_modifier():
    kb, calls = FakeKeyboard(), []
    hk = HotkeyService(key_state=kb)
    hk.bind("act", "Ctrl+G", lambda: calls.append(1))
    kb.down = {VK_G}
    hk.poll_once()
    assert calls == []
    kb.down = set()
    hk.poll_once()
    kb.down = {VK_CONTROL, VK_G}
    hk.poll_once()
    assert calls == [1]


def test_reserved_keys_rejected_for_tools():
    hk = HotkeyService(key_state=FakeKeyboard())
    for key in ("F6", "Esc", "F7"):
        with pytest.raises(HotkeyConflict) as err:
            hk.bind("toggle_afk", key, lambda: None)
        assert "emergency" in str(err.value).lower() or "pause" in str(err.value).lower()


def test_conflict_names_the_other_action():
    hk = HotkeyService(key_state=FakeKeyboard())
    hk.bind("optimize", "F2", lambda: None)
    with pytest.raises(HotkeyConflict) as err:
        hk.bind("arrange_grid", "f2", lambda: None)
    assert err.value.other_action == "optimize"


def test_rebinding_same_action_replaces_it():
    kb, calls = FakeKeyboard(), []
    hk = HotkeyService(key_state=kb)
    hk.bind("arrange_grid", "F3", lambda: calls.append("old"))
    hk.bind("arrange_grid", "F1", lambda: calls.append("new"))
    kb.down = {VK_F3}
    hk.poll_once()
    kb.down = {VK_F1}
    hk.poll_once()
    assert calls == ["new"]


def test_system_binding_may_use_reserved_keys():
    kb, calls = FakeKeyboard(), []
    hk = HotkeyService(key_state=kb)
    hk.bind_system("emergency_stop", "F6", lambda: calls.append("stop"))
    kb.down = {VK_F6}
    hk.poll_once()
    assert calls == ["stop"]


def test_unbind_and_failing_callback_does_not_break_others():
    kb, calls, errors = FakeKeyboard(), [], []
    hk = HotkeyService(key_state=kb, on_error=lambda a, e: errors.append(a))
    hk.bind("bad", "F1", lambda: 1 / 0)
    hk.bind("good", "F3", lambda: calls.append(1))
    kb.down = {VK_F1, VK_F3}
    hk.poll_once()
    assert calls == [1] and errors == ["bad"]
    hk.unbind("good")
    kb.down = set()
    hk.poll_once()
    kb.down = {VK_F3}
    hk.poll_once()
    assert calls == [1]
