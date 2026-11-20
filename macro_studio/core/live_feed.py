"""Live vision feed: what the macro sees, refreshed continuously (Constitution VI).

Every macro shows the actual game client it reads and clicks, with its calibrated scan
regions and click points drawn on top, so a wrong calibration is visible before a click
misses. The capture runs as a job on the shared scheduler (never on the Tk thread); the
finished image is handed to the Tk thread through `ui_call`.

This module holds no Tk widgets: `render()` is plain image work and unit-tested. Pages
display frames with `LiveFeedView` (macro_studio/core/live_feed_view.py).
"""

from dataclasses import dataclass, field
from typing import Callable, List, Tuple

import cv2
from PIL import Image, ImageDraw

Color = Tuple[int, int, int]


@dataclass
class Overlay:
    """What to draw, in calibrated (reference-client) coordinates."""

    reference: Tuple[int, int] = (800, 600)
    regions: List[Tuple[int, int, int, int, Color, str]] = field(default_factory=list)
    points: List[Tuple[int, int, Color, str]] = field(default_factory=list)


def empty_feed_message(minimized_clients):
    """Caption when there is nothing to show.

    Windows does not render minimized windows, so they cannot be captured (their client
    area is 0x0); say so instead of claiming no client exists.
    """
    if minimized_clients:
        noun = "client is" if minimized_clients == 1 else "clients are"
        return (f"{minimized_clients} Roblox {noun} minimized - restore one to see the "
                f"live feed (Windows does not draw minimized windows).")
    return "No Roblox client found - open the game to see the live feed."


class LiveFeed:
    def __init__(self, capture: Callable, pick_window: Callable[[], int],
                 overlay: Callable[[], Overlay], scheduler, ui_call: Callable,
                 on_frame: Callable, interval_s: float = 0.4,
                 max_size: Tuple[int, int] = (640, 480), name: str = "live_feed"):
        self._capture = capture
        self._pick_window = pick_window
        self._overlay = overlay
        self._scheduler = scheduler
        self._ui_call = ui_call
        self._on_frame = on_frame
        self.interval_s = interval_s
        self.max_size = max_size
        self.name = name
        self.visible = True

    # ------------------------------------------------------------ image work
    def render(self):
        """(hwnd, PIL image with overlays) for the window to show, or None."""
        hwnd = self._pick_window()
        if not hwnd:
            return None
        frame = self._capture(hwnd)
        if frame is None or getattr(frame, "size", 0) == 0:
            return None

        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        self._draw(image, self._overlay() or Overlay())
        image.thumbnail(self.max_size, Image.Resampling.LANCZOS)
        return hwnd, image

    @staticmethod
    def _draw(image, overlay):
        w, h = image.size
        ref_w, ref_h = overlay.reference
        sx = w / ref_w if ref_w else 1.0
        sy = h / ref_h if ref_h else 1.0
        line = max(2, int(round(min(sx, sy) * 2)))
        draw = ImageDraw.Draw(image)

        for x, y, rw, rh, color, label in overlay.regions:
            x0, y0 = int(round(x * sx)), int(round(y * sy))
            x1, y1 = int(round((x + rw) * sx)) - 1, int(round((y + rh) * sy)) - 1
            draw.rectangle([x0, y0, x1, y1], outline=color, width=line)
            if label:
                draw.text((x0 + line + 2, y0 + line + 1), label, fill=color)

        r = max(3, line * 2)
        for x, y, color, label in overlay.points:
            cx, cy = int(round(x * sx)), int(round(y * sy))
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
            if label:
                draw.text((cx + r + 2, cy - r), label, fill=color)

    # ------------------------------------------------------------ runtime
    def set_visible(self, visible):
        """Hidden pages do not capture (no wasted PrintWindow calls)."""
        self.visible = bool(visible)

    def tick(self):
        """One refresh: runs on the scheduler thread, hands the result to Tk."""
        if not self.visible:
            return
        result = self.render() or (0, None)
        self._ui_call(lambda: self._on_frame(*result))

    def start(self):
        # Isolated: capturing a frozen client can block for seconds; it must not hold up
        # the other jobs (claim timers, the Tabs picker).
        self._scheduler.add(self.name, self.tick, interval_s=self.interval_s, run_now=True,
                            isolated=True)

    def stop(self):
        self._scheduler.remove(self.name)
