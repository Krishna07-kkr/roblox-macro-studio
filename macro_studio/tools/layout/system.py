"""The real Windows side of the Layout tool: find, move, minimise and restore windows."""

import win32con
import win32gui

from macro_studio.core.processes import list_processes
from macro_studio.core.window_manager import WindowManager

_manager = None


def _wm():
    global _manager
    if _manager is None:
        _manager = WindowManager()
    return _manager


def roblox_windows():
    """(hwnd, pid) of every Roblox game window."""
    wm = _wm()
    return [(hwnd, wm.get_pid(hwnd)) for hwnd in wm.find_windows()]


def clients_running():
    return bool(list_processes())


def place_window(hwnd, rect):
    """Restore the window if it is minimised, then put it exactly on `rect`."""
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetWindowPos(hwnd, 0, rect.x, rect.y, rect.w, rect.h,
                          win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)


def minimise_window(hwnd):
    win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)


def restore_window(hwnd):
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
