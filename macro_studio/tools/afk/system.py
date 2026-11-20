"""The real Windows side of Anti-AFK: focus, key and mouse sending, idle time (spec 001, US5).

Keys are sent as hardware scan codes with SendInput, as games expect. A key that went down is
always released, even if something fails in between. Never called on the Tk thread.
"""

import ctypes
import time
from ctypes import wintypes

import win32api
import win32con
import win32gui

from macro_studio.core.sendinput import (  # noqa: F401 - re-exported for callers and tests
    KEYEVENTF_EXTENDEDKEY,
    KEYEVENTF_KEYUP,
    KEYEVENTF_SCANCODE,
    scan_and_flags,
)
from macro_studio.core.sendinput import send_key as _send_key
from macro_studio.core.window_manager import WindowManager

MIN_WINDOW_PX = 40
WHEEL_NOTCH = 120


class _LastInput(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LastInput)]
_user32.GetLastInputInfo.restype = wintypes.BOOL
_kernel32.GetTickCount.restype = wintypes.DWORD
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short


def key_down(vk):
    """True while the key or mouse button is physically held (used by the key recorder)."""
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


def idle_seconds():
    """Seconds since the player last touched the keyboard or mouse."""
    info = _LastInput(cbSize=ctypes.sizeof(_LastInput))
    if not _user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return max(0.0, ((_kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0)


_MOUSE_FLAGS = {"lbutton": (0x0002, 0x0004, 0), "rbutton": (0x0008, 0x0010, 0),
                "mbutton": (0x0020, 0x0040, 0), "xbutton1": (0x0080, 0x0100, 1),
                "xbutton2": (0x0080, 0x0100, 2)}


class RealIO:
    def __init__(self):
        self._wm = WindowManager()

    def windows(self):
        wm = self._wm
        return [(hwnd, wm.get_pid(hwnd), bool(win32gui.IsIconic(hwnd))) for hwnd in wm.find_windows()]

    def foreground(self):
        return win32gui.GetForegroundWindow()

    def activate(self, hwnd):
        return bool(self._wm.force_activate_window(hwnd))

    def restore_foreground(self, hwnd):
        if win32gui.IsWindow(hwnd):
            self._wm.force_activate_window(hwnd)

    def minimise(self, hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)

    def restore(self, hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    def key(self, vk, hold_s):
        _send_key(vk, up=False)
        try:
            time.sleep(hold_s)
        finally:
            _send_key(vk, up=True)               # always let go

    def mouse(self, hwnd, name):
        """Click or scroll at the centre of the client, then put the cursor back."""
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        if right - left < MIN_WINDOW_PX or bottom - top < MIN_WINDOW_PX:
            return
        x, y = win32gui.ClientToScreen(hwnd, ((left + right) // 2, (top + bottom) // 2))
        old = win32gui.GetCursorPos()
        win32api.SetCursorPos((x, y))
        try:
            if name in ("wheelup", "wheeldown"):
                win32api.mouse_event(0x0800, 0, 0, WHEEL_NOTCH if name == "wheelup" else -WHEEL_NOTCH, 0)
            else:
                down, up, data = _MOUSE_FLAGS[name]
                win32api.mouse_event(down, 0, 0, data, 0)
                time.sleep(0.05)
                win32api.mouse_event(up, 0, 0, data, 0)
        finally:
            win32api.SetCursorPos(old)
