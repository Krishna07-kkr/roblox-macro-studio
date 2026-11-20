"""empty_working_set: trims a process's memory without leaking a handle (US3 reuses this)."""

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes

from macro_studio.core import win32

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]


def handle_count():
    count = wintypes.DWORD()
    _kernel32.GetProcessHandleCount(_kernel32.GetCurrentProcess(), ctypes.byref(count))
    return count.value


def test_trimming_a_running_process_works():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert win32.empty_working_set(child.pid) is True
    finally:
        child.kill()
        child.wait()


def test_trimming_our_own_process_works():
    assert win32.empty_working_set(os.getpid()) is True


def test_a_process_that_does_not_exist_is_a_clean_false():
    assert win32.empty_working_set(4_000_000) is False


def test_trimming_never_leaks_a_handle():
    win32.empty_working_set(os.getpid())
    before = handle_count()
    for _ in range(300):
        win32.empty_working_set(os.getpid())
        win32.empty_working_set(4_000_000)
    assert handle_count() - before <= 3
