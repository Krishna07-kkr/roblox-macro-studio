"""The Windows side of anti-AFK. Nothing here sends a key or moves the mouse."""

import win32api
import win32con
import win32gui

from macro_studio.tools.afk import system


def test_letters_use_a_plain_scan_code_and_arrows_the_extended_flag():
    scan, flags = system.scan_and_flags(ord("W"))
    assert scan > 0 and flags & system.KEYEVENTF_SCANCODE and not flags & system.KEYEVENTF_EXTENDEDKEY
    _scan, arrow = system.scan_and_flags(0x25)
    assert arrow & system.KEYEVENTF_EXTENDEDKEY


def test_key_up_adds_the_release_flag_and_only_then():
    assert system.scan_and_flags(0x20, up=True)[1] & system.KEYEVENTF_KEYUP
    assert not system.scan_and_flags(0x20)[1] & system.KEYEVENTF_KEYUP


def test_idle_time_is_a_sensible_number():
    seconds = system.idle_seconds()
    assert 0.0 <= seconds < 10 * 365 * 24 * 3600


def test_the_safe_calls_of_the_real_io_work_on_this_machine():
    io = system.RealIO()
    assert isinstance(io.windows(), list)
    assert isinstance(io.foreground(), int)


def test_every_win32_function_the_mouse_code_calls_exists():
    # the first draft called win32gui.mouse_event, which does not exist
    for module, name in ((win32api, "mouse_event"), (win32api, "SetCursorPos"),
                         (win32gui, "GetCursorPos"), (win32gui, "ClientToScreen"),
                         (win32gui, "GetClientRect"), (win32gui, "ShowWindow"),
                         (win32gui, "IsIconic"), (win32gui, "GetForegroundWindow")):
        assert hasattr(module, name), f"{module.__name__}.{name}"
    assert hasattr(win32con, "SW_MINIMIZE") and hasattr(win32con, "SW_RESTORE")
