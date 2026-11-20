"""The real Windows side of the Recorder: windows, geometry, capture, input (spec 003, C006).

Never called on the Tk thread. `FrameRing` keeps the last few screenshots of the window under the
cursor while recording, so the picture saved for a button press is from just BEFORE the click
(a click often changes the screen). Everything else is thin glue over `core` modules.
"""

import time
from collections import deque

import win32con
import win32gui

from macro_studio.core import sendinput
from macro_studio.core.vision_scanner import VisionScanner
from macro_studio.core.window_manager import WindowManager

RING_KEEP = 4
RING_MARGIN_S = 0.03
ROBLOX_CACHE_S = 3.0


class FrameRing:
    """The newest few frames per window, with the time each was taken."""

    def __init__(self, capture, keep=RING_KEEP, clock=time.monotonic):
        self._capture, self._keep, self._clock = capture, keep, clock
        self._frames = {}

    def grab(self, hwnd):
        """Take one frame of `hwnd` now. Drops what belongs to other windows."""
        frame = self._capture(hwnd)
        for other in [h for h in self._frames if h != hwnd]:
            del self._frames[other]
        if frame is not None:
            self._frames.setdefault(hwnd, deque(maxlen=self._keep)).append((self._clock(), frame))
        return frame is not None

    def before(self, hwnd, t, margin=RING_MARGIN_S):
        """The newest frame taken at least `margin` seconds before `t`; else the oldest kept."""
        frames = self._frames.get(hwnd)
        if not frames:
            return None
        older = [f for ts, f in frames if ts <= t - margin]
        return older[-1] if older else frames[0][1]

    def clear(self):
        self._frames.clear()


class RealIO:
    """What the Player needs: client area, focus, and input."""

    def __init__(self, wm=None):
        self._wm = wm or WindowManager()

    def client_info(self, hwnd):
        return client_info(hwnd)

    def focus(self, hwnd):
        return bool(self._wm.force_activate_window(hwnd, max_wait_sec=1.0, auto_shrink=False))

    def is_foreground(self, hwnd):
        return self._wm.is_foreground(hwnd)

    def move(self, x, y):
        sendinput.move_cursor(x, y)

    def button(self, name, down):
        sendinput.mouse_button(name, down)

    def wheel(self, delta):
        sendinput.mouse_wheel(delta)

    def key(self, vk, down):
        sendinput.send_key(vk, up=not down)


def client_info(hwnd):
    """(left, top, width, height) of a window's client area on screen, or None."""
    try:
        if not win32gui.IsWindow(hwnd) or win32gui.IsIconic(hwnd):
            return None
        left, top = win32gui.ClientToScreen(hwnd, (0, 0))
        _l, _t, right, bottom = win32gui.GetClientRect(hwnd)
    except win32gui.error:
        return None
    return left, top, right, bottom


def window_rect(hwnd):
    """(left, top, width, height) of a window including its title bar, or None when it is gone,
    hidden or minimized. One cheap call, safe to make on the Tk thread."""
    try:
        if (not win32gui.IsWindow(hwnd) or win32gui.IsIconic(hwnd)
                or not win32gui.IsWindowVisible(hwnd)):
            return None
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    except win32gui.error:
        return None
    return left, top, right - left, bottom - top


class World:
    """Which Roblox window is where, for the recording Session."""

    def __init__(self, wm=None, capture=None, ring=None, clock=time.monotonic):
        self.wm = wm or WindowManager()
        self._scanner = None
        self._capture_fn = capture
        self.ring = ring or FrameRing(self._capture)
        self._clock = clock
        self._known = {}                # hwnd -> (is roblox, time checked)

    def _capture(self, hwnd):
        if self._capture_fn is not None:
            return self._capture_fn(hwnd)
        if self._scanner is None:
            self._scanner = VisionScanner({"detection": {"save_debug_screenshots": False}})
        return self._scanner.capture_client_full(hwnd)

    def is_roblox(self, hwnd):
        now = self._clock()
        known = self._known.get(hwnd)
        if known is not None and now - known[1] < ROBLOX_CACHE_S:
            return known[0]
        pid = self.wm.get_pid(hwnd)
        name = self.wm._process_name(pid) if pid else ""
        ok = (name in self.wm.target_procs and name not in self.wm.ignore_procs
              and bool(win32gui.GetWindowText(hwnd)))
        self._known[hwnd] = (ok, now)
        if len(self._known) > 200:
            self._known = {h: v for h, v in self._known.items() if now - v[1] < ROBLOX_CACHE_S}
        return ok

    def _root(self, hwnd):
        return win32gui.GetAncestor(hwnd, win32con.GA_ROOT) if hwnd else 0

    def window_at(self, x, y):
        try:
            root = self._root(win32gui.WindowFromPoint((int(x), int(y))))
        except win32gui.error:
            return None
        if root and not win32gui.IsIconic(root) and self.is_roblox(root):
            return root
        return None

    def foreground(self):
        hwnd = win32gui.GetForegroundWindow()
        return hwnd if hwnd and self.is_roblox(hwnd) and not win32gui.IsIconic(hwnd) else None

    def window_under_cursor(self):
        x, y = win32gui.GetCursorPos()
        return self.window_at(x, y) or self.foreground()

    def client_info(self, hwnd):
        return client_info(hwnd)

    def capture_now(self, hwnd):
        """A fresh frame of the window right now (replay and the pop-up guard use this)."""
        return self._capture(hwnd)

    def capture(self, hwnd, t):
        """The frame of `hwnd` as it was just before time `t` (see FrameRing)."""
        return self.ring.before(hwnd, t)

    def roblox_windows(self):
        """(hwnd, pid, title) of every open Roblox client that is not minimized."""
        return [(h, self.wm.get_pid(h), self.wm.get_window_title(h) or "Roblox")
                for h in self.wm.find_windows()]
