"""Anti-AFK key sequences and the key recorder (spec 001, US5, FR-041 and FR-044)."""

import pytest

from macro_studio.tools.afk.keys import PRESETS, BadKey, KeyRecorder, format_sequence, parse_sequence


# ------------------------------------------------------------------ parsing
def test_a_sequence_is_comma_separated_and_case_insensitive():
    tokens = parse_sequence("Space, W ,s")
    assert [t.name for t in tokens] == ["space", "w", "s"]
    assert all(t.kind == "key" and t.vk for t in tokens)


def test_mouse_buttons_and_the_wheel_are_mouse_tokens():
    tokens = parse_sequence("LButton, wheelup, XButton1")
    assert [t.kind for t in tokens] == ["mouse"] * 3


def test_letters_digits_arrows_and_modifiers_are_known():
    assert [t.name for t in parse_sequence("a,5,left,right,up,down,lshift,lctrl,enter,tab")] == [
        "a", "5", "left", "right", "up", "down", "lshift", "lctrl", "enter", "tab"]


def test_blank_items_are_ignored_but_an_empty_list_is_refused():
    assert [t.name for t in parse_sequence("space,,  ,w")] == ["space", "w"]
    with pytest.raises(BadKey, match="at least one"):
        parse_sequence("  ,  ")


def test_an_unknown_key_is_named_in_the_message():
    with pytest.raises(BadKey, match="'banana'"):
        parse_sequence("space,banana")


@pytest.mark.parametrize("key", ["F6", "f7", "Esc", "escape", "ESC"])
def test_the_studios_own_stop_keys_cannot_be_sent(key):
    # The stop hotkeys read the keyboard state, which includes injected keys: sending F6, F7
    # or Esc from here would stop the macro or the app by itself.
    with pytest.raises(BadKey, match="stop"):
        parse_sequence(f"space,{key}")


def test_a_sequence_round_trips_to_text():
    text = "space,w,lbutton"
    assert format_sequence(parse_sequence(text)) == text


def test_every_preset_is_a_valid_sequence():
    assert {"Space (jump)", "W then S", "Left Shift", "Left then Right", "Space + W",
            "Left Click", "Space + Left Click"} <= set(PRESETS)
    for name, text in PRESETS.items():
        assert parse_sequence(text), name


# ------------------------------------------------------------------ the recorder
class Keyboard:
    """Scripted keyboard: set `down` to the keys held now."""

    def __init__(self):
        self.down = set()
        self.now = 0.0
        self.foreground = 1


def recorder(kb, studio=99):
    return KeyRecorder(is_down=lambda vk: vk in kb.down, now=lambda: kb.now,
                       foreground=lambda: kb.foreground, studio_hwnd=studio, timeout_s=20)


def press(rec, kb, *names):
    from macro_studio.tools.afk.keys import vk_for
    kb.down = {vk_for(n) for n in names}
    rec.tick()
    kb.down = set()
    rec.tick()


def test_pressed_keys_are_recorded_in_order():
    kb = Keyboard()
    rec = recorder(kb)
    press(rec, kb, "space")
    press(rec, kb, "w")
    press(rec, kb, "lbutton")
    assert format_sequence(rec.tokens()) == "space,w,lbutton"


def test_a_held_key_is_one_press_not_many():
    kb = Keyboard()
    rec = recorder(kb)
    from macro_studio.tools.afk.keys import vk_for
    kb.down = {vk_for("space")}
    for _ in range(10):
        rec.tick()
    assert format_sequence(rec.tokens()) == "space"


def test_esc_ends_the_recording_and_is_not_recorded():
    kb = Keyboard()
    rec = recorder(kb)
    press(rec, kb, "a")
    from macro_studio.tools.afk.keys import VK_ESCAPE
    kb.down = {VK_ESCAPE}
    rec.tick()
    assert rec.finished and format_sequence(rec.tokens()) == "a"
    kb.down = set()
    press(rec, kb, "b")
    assert format_sequence(rec.tokens()) == "a"                  # nothing after it ended


def test_the_recording_stops_by_itself_after_20_seconds():
    kb = Keyboard()
    rec = recorder(kb)
    press(rec, kb, "a")
    kb.now = 20.5
    rec.tick()
    assert rec.finished and rec.timed_out


def test_clicks_on_the_studio_window_are_not_recorded():
    kb = Keyboard()
    rec = recorder(kb, studio=99)
    kb.foreground = 99                                            # the player is clicking Record
    press(rec, kb, "lbutton")
    kb.foreground = 5
    press(rec, kb, "lbutton")
    assert format_sequence(rec.tokens()) == "lbutton"


def test_the_stop_keys_are_never_recorded_into_a_sequence():
    kb = Keyboard()
    rec = recorder(kb)
    for raw_vk in (0x75, 0x76):                  # F6 and F7 as the keyboard reports them
        kb.down = {raw_vk}
        rec.tick()
        kb.down = set()
        rec.tick()
    press(rec, kb, "w")
    assert format_sequence(rec.tokens()) == "w"
