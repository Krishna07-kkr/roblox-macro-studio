"""The screens the PC has and their usable areas (spec 001, US4).

`Rect` is shared by the tools that place windows. `list_monitors()` returns every monitor with
its full area and its work area (the part not covered by the taskbar), primary first.
"""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class Monitor:
    full: Rect
    work: Rect
    primary: bool


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                      ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                        _MONITORENUMPROC, wintypes.LPARAM]
_user32.EnumDisplayMonitors.restype = wintypes.BOOL
_user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MonitorInfo)]
_user32.GetMonitorInfoW.restype = wintypes.BOOL
MONITORINFOF_PRIMARY = 1


def _rect(r):
    return Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)


def list_monitors():
    """Every monitor, primary first."""
    found = []

    def visit(handle, _dc, _rect_ptr, _data):
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if _user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            found.append(Monitor(full=_rect(info.rcMonitor), work=_rect(info.rcWork),
                                 primary=bool(info.dwFlags & MONITORINFOF_PRIMARY)))
        return True

    callback = _MONITORENUMPROC(visit)           # kept alive until the call returns
    _user32.EnumDisplayMonitors(None, None, callback, 0)
    found.sort(key=lambda monitor: not monitor.primary)
    return found
