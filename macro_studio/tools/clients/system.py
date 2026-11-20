"""The real Windows side of the Clients tool: processes, windows, log files, actions.

Thin wrappers over psutil and Win32 that the service gets as plain functions. Kept apart so
the service stays testable with fakes. Nothing here is called on the Tk thread: the tool
runs scans and actions on worker threads.
"""

import ctypes
from ctypes import wintypes

import win32con
import win32gui

# Process helpers are shared with the Memory tool, so they live in core/.
from macro_studio.core.processes import (  # noqa: F401 - re-exported for this tool
    ROBLOX_EXES,
    list_processes,
    process_info,
    terminate_process,
    trim_process,
)
from macro_studio.core.window_manager import WindowManager
from macro_studio.tools.clients.service import WindowInfo

HEAD_BYTES = 400_000
TAIL_BYTES = 200_000
HUNG_CHECK_MS = 100

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                        wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                                        ctypes.POINTER(ctypes.c_size_t)]
_user32.SendMessageTimeoutW.restype = ctypes.c_size_t
SMTO_ABORTIFHUNG = 0x0002

_window_manager = None


def _manager():
    global _window_manager
    if _window_manager is None:
        _window_manager = WindowManager()
    return _window_manager


def _responsive(hwnd):
    result = ctypes.c_size_t()
    return bool(_user32.SendMessageTimeoutW(hwnd, win32con.WM_NULL, 0, 0, SMTO_ABORTIFHUNG,
                                            HUNG_CHECK_MS, ctypes.byref(result)))


def list_windows():
    """Visible, titled top-level windows that belong to a Roblox client."""
    found = []

    def visit(hwnd, _extra):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        pid = _manager().get_pid(hwnd)
        if pid and _manager()._process_name(pid) in ROBLOX_EXES:
            found.append(WindowInfo(hwnd=hwnd, pid=pid, title=title,
                                    minimized=bool(win32gui.IsIconic(hwnd)),
                                    responsive=_responsive(hwnd)))
        return True

    win32gui.EnumWindows(visit, None)
    return found


def read_log_text(path, head):
    """The first 400 KB (head) or last 200 KB (tail) of a log, as text."""
    with open(path, "rb") as handle:
        if head:
            data = handle.read(HEAD_BYTES)
        else:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - TAIL_BYTES))
            data = handle.read()
    return data.decode("utf-8", errors="ignore")


def focus_window(hwnd):
    _manager().force_activate_window(hwnd)


def set_window_title(hwnd, title):
    win32gui.SetWindowText(hwnd, title)  # pyright: ignore[reportCallIssue] - bad stub; works


def list_error_dialogs():
    """(hwnd, owner pid) of every visible standard dialog (#32770) owned by a Roblox client."""
    found = []

    def visit(hwnd, _extra):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == "#32770":
            pid = _manager().get_pid(hwnd)
            if pid and _manager()._process_name(pid) in ROBLOX_EXES:
                found.append((hwnd, pid))
        return True

    win32gui.EnumWindows(visit, None)
    return found


def close_window(hwnd):
    """Ask a window to close (what its own X button does)."""
    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
