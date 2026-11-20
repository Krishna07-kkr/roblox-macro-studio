"""Liquid Glass for the window: a real blurred, translucent backdrop (Windows 11 acrylic).

How it works: the window frame is extended over the whole client area and given the acrylic
backdrop. Windows then draws pure black (#000000) as clear glass and every other colour as a
tint on top of the blurred background, so the app's black page becomes glass and its panels
become frosted glass, while text stays crisp. Needs Windows 11 22H2 (build 22621) or newer;
anywhere else, or with the switch off, the window keeps the normal solid dark look.
"""

import ctypes
import sys

MIN_BUILD = 22621                  # DWMWA_SYSTEMBACKDROP_TYPE exists from here
_DARK, _BORDER, _CAPTION, _TEXT, _BACKDROP = 20, 34, 35, 36, 38
_BACKDROP_NONE = 1
#: How see-through the glass is: the Windows backdrop material behind the translucent window.
STYLES = {"frosted": 3, "soft": 2, "deep": 4}      # acrylic (blurs what is behind), mica, mica alt
DEFAULT_STYLE = "frosted"       # the visibly see-through acrylic; "soft" (mica) is the subtle one
_COLOR_DEFAULT = 0xFFFFFFFF


class _Margins(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int), ("top", ctypes.c_int),
                ("bottom", ctypes.c_int)]


def glass_supported(build=None):
    if build is None:
        build = getattr(sys, "getwindowsversion", lambda: None)()
        build = getattr(build, "build", 0)
    return build >= MIN_BUILD


def _hwnd(root):
    """The real top-level window (not Tk's inner child window)."""
    user32 = ctypes.windll.user32
    user32.GetAncestor.restype = ctypes.c_void_p
    return user32.GetAncestor(root.winfo_id(), 2) or root.winfo_id()      # GA_ROOT


def current_backdrop(root):
    """The backdrop type Windows reports for the window (3 frosted, 2 soft, 1 none), or None."""
    try:
        number = ctypes.c_uint(0)
        result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            ctypes.c_void_p(_hwnd(root)), _BACKDROP, ctypes.byref(number), ctypes.sizeof(number))
        return number.value if result == 0 else None
    except (OSError, AttributeError, ValueError):
        return None


def _attribute(hwnd, code, value):
    number = ctypes.c_uint(value & 0xFFFFFFFF)
    return ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, code, ctypes.byref(number),
                                                      ctypes.sizeof(number))


def apply_window_look(root, glass, style=DEFAULT_STYLE):
    """Give the window its look: Liquid Glass when asked for and supported, else solid dark.

    Returns True when the glass backdrop is on. Best effort: a failure leaves a normal window.
    """
    try:
        hwnd = ctypes.c_void_p(_hwnd(root))
        _attribute(hwnd, _DARK, 1)
        if glass and glass_supported():
            margins = _Margins(-1, -1, -1, -1)
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
            for code in (_CAPTION, _BORDER, _TEXT):
                _attribute(hwnd, code, _COLOR_DEFAULT)
            return _attribute(hwnd, _BACKDROP, STYLES.get(style, STYLES[DEFAULT_STYLE])) == 0
        if glass_supported():
            _attribute(hwnd, _BACKDROP, _BACKDROP_NONE)
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_Margins(0, 0, 0, 0)))
        _attribute(hwnd, _CAPTION, 0x00000000)          # a black title bar
        _attribute(hwnd, _BORDER, 0x003A3A38)           # the separator colour (BGR)
        _attribute(hwnd, _TEXT, 0x00FFFFFF)
    except (OSError, AttributeError, ValueError):
        return False
    return False
