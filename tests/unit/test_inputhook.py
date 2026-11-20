"""The low-level mouse/keyboard hook: event parsing, queueing and a clean start/stop (spec 003, C005)."""

import ctypes
import time

import psutil
import pytest

from macro_studio.core import inputhook
from macro_studio.core.inputhook import RawEvent

WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x200, 0x201, 0x202
WM_RBUTTONDOWN, WM_MBUTTONUP, WM_MOUSEWHEEL = 0x204, 0x208, 0x20A
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN = 0x100, 0x101, 0x104


def mouse(message, x=10, y=20, data=0, flags=0, hook=None, drain=True):
    hook = hook or inputhook.InputHook()
    info = inputhook.MSLLHOOKSTRUCT(inputhook.POINT(x, y), data & 0xFFFFFFFF, flags, 0, 0)
    hook._mouse_proc(0, message, ctypes.addressof(info))
    return hook.drain_all() if drain else []


def key(message, vk, flags=0, hook=None):
    hook = hook or inputhook.InputHook()
    info = inputhook.KBDLLHOOKSTRUCT(vk, 0, flags, 0, 0)
    hook._key_proc(0, message, ctypes.addressof(info))
    return hook.drain_all()


def test_mouse_buttons_move_and_wheel_become_events():
    (e,) = mouse(WM_LBUTTONDOWN, 5, 6)
    assert (e.kind, e.button, e.x, e.y) == ("down", "left", 5, 6)
    (e,) = mouse(WM_RBUTTONDOWN)
    assert (e.kind, e.button) == ("down", "right")
    (e,) = mouse(WM_MBUTTONUP)
    assert (e.kind, e.button) == ("up", "middle")
    (e,) = mouse(WM_MOUSEMOVE, 100, 200)
    assert (e.kind, e.x, e.y) == ("move", 100, 200)


def test_wheel_amount_is_signed():
    (up,) = mouse(WM_MOUSEWHEEL, data=(120 << 16))
    (down,) = mouse(WM_MOUSEWHEEL, data=((-120) & 0xFFFF) << 16)
    assert up.kind == "wheel" and up.delta == 120 and down.delta == -120


def test_keys_become_events_with_their_virtual_key():
    (e,) = key(WM_KEYDOWN, 0x41)
    assert (e.kind, e.vk) == ("key_down", 0x41)
    (e,) = key(WM_KEYUP, 0x41)
    assert e.kind == "key_up"
    (e,) = key(WM_SYSKEYDOWN, 0x12)
    assert e.kind == "key_down"


def test_input_sent_by_programs_is_ignored_by_default():
    assert mouse(WM_LBUTTONDOWN, flags=0x01) == []
    assert key(WM_KEYDOWN, 0x41, flags=0x10) == []


def test_injected_input_can_be_accepted_for_tests():
    hook = inputhook.InputHook(accept_injected=True)
    assert len(mouse(WM_LBUTTONDOWN, flags=0x01, hook=hook)) == 1


def test_a_negative_code_is_passed_on_untouched():
    hook = inputhook.InputHook()
    info = inputhook.MSLLHOOKSTRUCT(inputhook.POINT(1, 1), 0, 0, 0, 0)
    hook._mouse_proc(-1, WM_LBUTTONDOWN, ctypes.addressof(info))
    assert hook.drain_all() == []


def test_the_queue_is_capped_and_counts_what_it_dropped():
    hook = inputhook.InputHook(max_queue=10)
    for _ in range(25):
        mouse(WM_MOUSEMOVE, hook=hook, drain=False)
    assert len(hook.drain_all()) == 10 and hook.dropped == 15


def test_drain_hands_events_to_a_function_in_order_and_in_batches():
    hook = inputhook.InputHook()
    for x in range(7):
        mouse(WM_MOUSEMOVE, x=x, hook=hook, drain=False)
    seen = []
    assert hook.drain(seen.append, limit=5) == 5
    assert [e.x for e in seen] == [0, 1, 2, 3, 4]
    assert hook.drain(seen.append, limit=5) == 2


def test_events_carry_a_monotonic_time():
    before = time.monotonic()
    (e,) = mouse(WM_MOUSEMOVE)
    assert before <= e.t <= time.monotonic() and isinstance(e, RawEvent)


# ------------------------------------------------------------------ the real hook
def test_start_and_stop_leave_nothing_behind():
    proc = psutil.Process()
    warm = inputhook.InputHook()
    assert warm.start() and warm.running
    warm.stop()
    before = proc.num_handles()
    for _ in range(15):
        hook = inputhook.InputHook()
        assert hook.start(), hook.error
        hook.stop()
        assert not hook.running
    assert proc.num_handles() <= before + 3


def test_stop_twice_and_stop_without_start_are_harmless():
    hook = inputhook.InputHook()
    hook.stop()
    assert hook.start()
    hook.stop()
    hook.stop()


def test_start_twice_is_refused_not_doubled():
    hook = inputhook.InputHook()
    assert hook.start()
    try:
        assert hook.start() is False
    finally:
        hook.stop()


@pytest.mark.skipif(not hasattr(ctypes, "windll"), reason="Windows only")
def test_a_real_key_reaches_the_hook():
    """F24 does nothing in any program, so it is a safe key to send for this check."""
    from macro_studio.core.sendinput import send_key
    hook = inputhook.InputHook(accept_injected=True)
    assert hook.start()
    try:
        send_key(0x87, up=False)
        send_key(0x87, up=True)
        deadline = time.monotonic() + 3
        events = []
        while time.monotonic() < deadline and len(events) < 2:
            events += hook.drain_all()
            time.sleep(0.02)
    finally:
        hook.stop()
    assert [(e.kind, e.vk) for e in events if e.vk == 0x87] == [("key_down", 0x87), ("key_up", 0x87)]
