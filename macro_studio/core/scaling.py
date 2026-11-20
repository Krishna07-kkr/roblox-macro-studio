"""
Resolution and DPI normalisation.

Every coordinate in this project was calibrated on a reference rig:
    1920x1080 monitor, 100% Windows scaling (96 DPI),
    Roblox client area snapped to 800x600.

Two independent things break that on another machine:

1. DPI virtualisation. A process that has not declared DPI awareness is lied
   to by Windows: GetClientRect and cursor coordinates come back in *logical*
   pixels, so on a 150%-scaled display an 800px-wide client reports as 533.
   No amount of arithmetic fixes this after the fact - the process must
   declare awareness before it creates any window or reads any geometry.
   That is what set_dpi_awareness() is for, and it must run first.

2. A different client size. If the game window cannot be snapped to the
   reference 800x600 (small laptop screen, or the user resized it), every
   calibrated point has to be rescaled into the actual client area.
   That is what CoordinateMapper does.
"""

import ctypes

# Data-root helpers live in paths.py; re-exported here because every module and the
# calibrators already call scaling.get_base_dir() / scaling.get_resource_path().
from macro_studio.core.paths import get_base_dir, get_resource_path  # noqa: F401

# The rig every coordinate in config.json / profiles was captured on.
REFERENCE_CLIENT = (800, 600)
REFERENCE_SCREEN = (1920, 1080)
REFERENCE_DPI = 96

_dpi_state = {"applied": False, "mode": None}


def set_dpi_awareness():
    """
    Declare per-monitor DPI awareness. Must be called BEFORE creating any Tk
    window or reading any window geometry, and is safe to call repeatedly.

    Returns the mode that was applied, one of:
        "per-monitor-v2", "per-monitor", "system", "already-set", "failed"
    """
    if _dpi_state["applied"]:
        return _dpi_state["mode"]

    mode = "failed"

    # Windows 10 1703+ : per-monitor v2 handles mixed-DPI multi-monitor setups
    # correctly, which matters here because the second monitor may differ.
    try:
        ctx = ctypes.c_void_p(-4)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctx):
            mode = "per-monitor-v2"
    except Exception:
        pass

    # Windows 8.1+
    if mode == "failed":
        try:
            # 2 == PROCESS_PER_MONITOR_DPI_AWARE
            hr = ctypes.windll.shcore.SetProcessDpiAwareness(2)
            if hr == 0:
                mode = "per-monitor"
            elif hr == -2147024891:  # E_ACCESSDENIED: already set by manifest
                mode = "already-set"
        except Exception:
            pass

    # Vista+ fallback: system-wide awareness only
    if mode == "failed":
        try:
            if ctypes.windll.user32.SetProcessDPIAware():
                mode = "system"
        except Exception:
            pass

    _dpi_state["applied"] = True
    _dpi_state["mode"] = mode
    return mode


def get_system_dpi():
    """System DPI (96 == 100% scaling)."""
    try:
        return ctypes.windll.user32.GetDpiForSystem()
    except Exception:
        try:
            hdc = ctypes.windll.user32.GetDC(0)
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
            ctypes.windll.user32.ReleaseDC(0, hdc)
            return dpi
        except Exception:
            return REFERENCE_DPI


def get_dpi_for_window(hwnd):
    """DPI of the monitor the given window is on."""
    try:
        return ctypes.windll.user32.GetDpiForWindow(hwnd)
    except Exception:
        return get_system_dpi()


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def get_client_size(hwnd):
    """(width, height) of a window's client area in real pixels."""
    rect = _RECT()
    ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect))
    return (rect.right - rect.left, rect.bottom - rect.top)


class CoordinateMapper:
    """
    Maps calibrated reference coordinates onto an actual client area.

    When the client matches the reference size (the normal case once the
    window manager has snapped it to 800x600), this is the identity mapping
    and costs nothing.

        mapper = CoordinateMapper.for_window(hwnd)
        x, y = mapper.point(287, 300)
        box  = mapper.region({"x": 196, "y": 178, "width": 142, "height": 246})
    """

    def __init__(self, client_size, reference=REFERENCE_CLIENT, dpi=REFERENCE_DPI):
        self.client_size = client_size
        self.reference = reference
        self.dpi = dpi
        ref_w, ref_h = reference
        act_w, act_h = client_size
        self.sx = (act_w / ref_w) if ref_w else 1.0
        self.sy = (act_h / ref_h) if ref_h else 1.0

    @classmethod
    def for_window(cls, hwnd, reference=REFERENCE_CLIENT):
        return cls(get_client_size(hwnd), reference, get_dpi_for_window(hwnd))

    # Window borders and rounded corners mean a client snapped to "800x600"
    # often measures 800x599. Treat a couple of pixels as the same size rather
    # than rescaling every coordinate by 0.998 for nothing.
    IDENTITY_TOLERANCE_PX = 3

    @property
    def is_identity(self):
        return (abs(self.client_size[0] - self.reference[0]) <= self.IDENTITY_TOLERANCE_PX
                and abs(self.client_size[1] - self.reference[1]) <= self.IDENTITY_TOLERANCE_PX)

    @property
    def scale_percent(self):
        return round(self.dpi / REFERENCE_DPI * 100)

    def point(self, x, y):
        """Scale a calibrated (x, y) into the actual client area."""
        if self.is_identity:
            return int(x), int(y)
        return int(round(x * self.sx)), int(round(y * self.sy))

    def point_dict(self, d, default=(0, 0)):
        """Scale a {'x':..,'y':..} dict."""
        return self.point(d.get("x", default[0]), d.get("y", default[1]))

    def region(self, r):
        """Scale a {'x','y','width','height'} dict."""
        if self.is_identity:
            return {k: int(r.get(k, 0)) for k in ("x", "y", "width", "height")}
        return {
            "x": int(round(r.get("x", 0) * self.sx)),
            "y": int(round(r.get("y", 0) * self.sy)),
            "width": int(round(r.get("width", 0) * self.sx)),
            "height": int(round(r.get("height", 0) * self.sy)),
        }

    def describe(self):
        if self.is_identity:
            return (f"client {self.client_size[0]}x{self.client_size[1]} "
                    f"matches reference - no scaling needed")
        return (f"client {self.client_size[0]}x{self.client_size[1]} vs reference "
                f"{self.reference[0]}x{self.reference[1]} - scaling x{self.sx:.3f}, y{self.sy:.3f}")


def describe_environment():
    """One-line summary of the current display environment, for logs."""
    mode = _dpi_state["mode"] or "not-set"
    dpi = get_system_dpi()
    w = ctypes.windll.user32.GetSystemMetrics(0)
    h = ctypes.windll.user32.GetSystemMetrics(1)
    monitors = ctypes.windll.user32.GetSystemMetrics(80)
    return (f"display {w}x{h} @ {round(dpi / REFERENCE_DPI * 100)}% "
            f"({dpi} dpi), {monitors} monitor(s), dpi-awareness={mode}")
