"""No Win32 handle may leak from the desktop-attach call (spec 002, A1, finding H1).

Measured on the real process: GetProcessHandleCount before and after thousands of calls.
Before the fix every call leaked one desktop handle (3,000 calls: 322 -> 3,322).
"""

import ctypes
import gc
import threading
from ctypes import wintypes

import pytest

CALLS = 2000
ALLOWED_GROWTH = 5

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel32.GetProcessHandleCount.restype = wintypes.BOOL


def handle_count():
    count = wintypes.DWORD()
    assert _kernel32.GetProcessHandleCount(_kernel32.GetCurrentProcess(), ctypes.byref(count))
    return count.value


def growth_over(action, calls=CALLS):
    action()                       # warm-up: first call may create thread-local state
    gc.collect()
    before = handle_count()
    for _ in range(calls):
        action()
    gc.collect()
    return handle_count() - before


def in_thread(fn):
    """Run fn in a fresh thread (no windows, like the scheduler or a runner) and return its result."""
    box = {}

    def target():
        box["result"] = fn()
    worker = threading.Thread(target=target)
    worker.start()
    worker.join()
    return box["result"]


def test_helper_does_not_leak_in_a_worker_thread():
    from macro_studio.core import win32
    assert in_thread(lambda: growth_over(win32.attach_input_desktop)) <= ALLOWED_GROWTH


def test_helper_does_not_leak_on_the_main_thread():
    from macro_studio.core import win32
    assert growth_over(win32.attach_input_desktop) <= ALLOWED_GROWTH


def test_helper_never_raises_and_reports_success_as_a_bool():
    from macro_studio.core import win32
    assert isinstance(win32.attach_input_desktop(), bool)


def test_a_thread_that_ends_gives_its_desktop_handle_back():
    from macro_studio.core import win32
    gc.collect()
    before = handle_count()
    for _ in range(50):
        in_thread(win32.attach_input_desktop)
    gc.collect()
    assert handle_count() - before <= ALLOWED_GROWTH


@pytest.mark.parametrize("site", ["vision", "window", "input"])
def test_every_call_site_is_leak_free(site):
    if site == "vision":
        from macro_studio.core.vision_scanner import VisionScanner
        target = VisionScanner.__new__(VisionScanner)._ensure_desktop_access
    elif site == "window":
        from macro_studio.core.window_manager import WindowManager
        target = WindowManager.__new__(WindowManager)._ensure_desktop_access
    else:
        from macro_studio.core.input_controller import InputController
        target = InputController.__new__(InputController)._ensure_desktop_access
    assert in_thread(lambda: growth_over(target)) <= ALLOWED_GROWTH


@pytest.mark.parametrize("module", ["goliath", "mutation"])
def test_calibrators_use_the_shared_helper(module):
    import importlib
    import os
    mod = importlib.import_module(f"macro_studio.calibrators.{module}")
    with open(mod.__file__, encoding="utf-8") as handle:
        source = handle.read()
    assert "OpenInputDesktop" not in source, os.path.basename(mod.__file__)
