"""Monitors and the named lock, on this machine (spec 001, US4)."""

import ctypes
from ctypes import wintypes

from macro_studio.core import monitors, win32

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]


def handle_count():
    count = wintypes.DWORD()
    _kernel32.GetProcessHandleCount(_kernel32.GetCurrentProcess(), ctypes.byref(count))
    return count.value


def test_at_least_one_monitor_with_a_sensible_work_area():
    found = monitors.list_monitors()
    assert found and found[0].primary
    for mon in found:
        assert mon.work.w > 0 and mon.work.h > 0
        assert mon.work.w <= mon.full.w and mon.work.h <= mon.full.h


def test_the_primary_monitor_comes_first():
    found = monitors.list_monitors()
    assert [m.primary for m in found].count(True) == 1 and found[0].primary


def test_a_named_lock_can_be_held_and_a_second_holder_is_detected():
    name = "MacroStudioTestMutex_8f3a"
    first = win32.create_named_mutex(name)
    second = win32.create_named_mutex(name)
    try:
        assert first and second
        assert win32.last_mutex_existed() is True           # the second call found it already there
    finally:
        win32.close_handle(second)
        win32.close_handle(first)


def test_closing_a_zero_handle_is_harmless():
    win32.close_handle(0)


def test_the_lock_never_leaks_a_handle():
    win32.close_handle(win32.create_named_mutex("MacroStudioTestMutex_leak"))
    before = handle_count()
    for _ in range(300):
        win32.close_handle(win32.create_named_mutex("MacroStudioTestMutex_leak"))
    assert handle_count() - before <= 3
