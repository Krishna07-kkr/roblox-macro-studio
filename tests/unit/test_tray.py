"""The tray icon: real Win32 calls, a hidden message window, no leaks (spec 001, US7)."""

import ctypes
import threading
from ctypes import wintypes

import pytest

from macro_studio.core import tray as tray_mod
from macro_studio.core.tray import TrayIcon

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.IsWindow.argtypes = [wintypes.HWND]


def handle_count():
    count = wintypes.DWORD()
    _kernel32.GetProcessHandleCount(_kernel32.GetCurrentProcess(), ctypes.byref(count))
    return count.value


def make(**kwargs):
    shown = threading.Event()
    clicked = []
    icon = TrayIcon("Test tray", on_show=shown.set,
                    menu_items=[("Show", lambda: clicked.append("show")),
                                ("Exit", lambda: clicked.append("exit"))], **kwargs)
    return icon, shown, clicked


def test_the_icon_starts_with_a_real_hidden_window_and_stops_cleanly():
    icon, _shown, _clicked = make()
    assert icon.start() is True
    try:
        assert icon.hwnd and _user32.IsWindow(icon.hwnd)
    finally:
        icon.stop()
    assert not _user32.IsWindow(icon.hwnd)
    assert icon.thread is None or not icon.thread.is_alive()


def test_a_click_on_the_icon_asks_to_show_the_window():
    icon, shown, _clicked = make()
    icon.start()
    try:
        _user32.PostMessageW(icon.hwnd, tray_mod.CALLBACK_MESSAGE, 0, tray_mod.WM_LBUTTONUP)
        assert shown.wait(3)
    finally:
        icon.stop()


def test_a_double_click_also_shows_it():
    icon, shown, _clicked = make()
    icon.start()
    try:
        _user32.PostMessageW(icon.hwnd, tray_mod.CALLBACK_MESSAGE, 0, tray_mod.WM_LBUTTONDBLCLK)
        assert shown.wait(3)
    finally:
        icon.stop()


def test_menu_items_run_their_own_callbacks_and_unknown_ids_do_nothing():
    icon, _shown, clicked = make()
    assert icon.menu_labels() == ["Show", "Exit"]
    icon.run_command(2)
    icon.run_command(1)
    icon.run_command(99)
    icon.run_command(0)
    assert clicked == ["exit", "show"]


def test_a_failing_callback_is_reported_and_does_not_kill_the_icon():
    errors = []

    def boom():
        raise RuntimeError("bad menu item")
    icon = TrayIcon("x", on_show=boom, menu_items=[("Boom", boom)], on_error=errors.append)
    icon.start()
    try:
        _user32.PostMessageW(icon.hwnd, tray_mod.CALLBACK_MESSAGE, 0, tray_mod.WM_LBUTTONUP)
        icon.run_command(1)
        assert _wait(lambda: len(errors) >= 2)
        assert _user32.IsWindow(icon.hwnd)
    finally:
        icon.stop()


def _wait(condition, timeout=3.0):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_the_tooltip_can_change_and_is_cut_to_the_limit():
    icon, _shown, _clicked = make()
    icon.start()
    try:
        icon.set_tooltip("2 clients, 800 MB")
        assert _wait(lambda: icon.tooltip == "2 clients, 800 MB")
        icon.set_tooltip("x" * 500)
        assert _wait(lambda: len(icon.tooltip) <= 127 and icon.tooltip.startswith("xxx"))
    finally:
        icon.stop()


def test_stopping_twice_and_stopping_a_never_started_icon_are_harmless():
    icon, _shown, _clicked = make()
    icon.stop()
    icon.start()
    icon.stop()
    icon.stop()


def test_starting_and_stopping_many_times_leaks_no_handle():
    icon, _s, _c = make()
    icon.start()
    icon.stop()
    before = handle_count()
    for _ in range(25):
        icon, _s, _c = make()
        assert icon.start() is True
        icon.stop()
    assert handle_count() - before <= 8


def test_two_icons_can_exist_at_once_without_clashing():
    one, _s1, _c1 = make()
    two, _s2, _c2 = make()
    try:
        assert one.start() and two.start() and one.hwnd != two.hwnd
    finally:
        one.stop()
        two.stop()


@pytest.mark.parametrize("bad", [None, 12])
def test_a_missing_callback_is_refused_early(bad):
    with pytest.raises(TypeError):
        TrayIcon("x", on_show=bad, menu_items=[])
