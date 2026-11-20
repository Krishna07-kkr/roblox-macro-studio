"""Closes a Robux purchase popup safely, before any macro click (spec 002, story A7).

The macros click fixed points. If a purchase prompt is on screen, one of those clicks can land
on its Buy button, so before every click the macro asks this guard whether the screen is clear.

How it works:
- The player captures the popup once (Settings > Capture popup): a picture of it, and where its
  close button is. The close button is stored relative to the picture, so the click follows the
  popup if it opens a little to one side.
- Before each macro click the guard looks for that picture in the client. Nothing found: the
  macro goes ahead. Found: it clicks the close button, waits, and looks again.
- It never guesses. Without a calibrated close button, or if the popup is nowhere near where it
  was captured, it clicks nothing and pauses the macro. If the popup is still there after three
  tries it stops the macro.
- It only ever clicks. There is no code path that sends a key: Enter or Space on a purchase
  prompt could confirm it.
- With no popup picture it is off and says so once; it cannot see what it was never shown.

Tk-free and Win32-free: the capture, click, wait, pause and stop actions are handed in.
"""

import os

import cv2
import numpy as np

TEMPLATE_NAME = "purchase_popup.png"
DEFAULT_THRESHOLD = 0.85
CLOSE_ATTEMPTS = 3
SETTLE_SECONDS = 0.6
#: A popup this far (in reference pixels) from where it was captured is not trusted.
MAX_SHIFT = 80
MIN_TEMPLATE_SIDE = 24
_REFERENCE = (800, 600)


def _reference_size(cfg):
    ref = cfg.get("reference_client") or {}
    width = int(ref.get("width") or _REFERENCE[0])
    height = int(ref.get("height") or _REFERENCE[1])
    return width, height


def _calibrated(point):
    """(x, y) from a {"x","y"} dict or tuple, or None. 0,0 means "not calibrated"."""
    if not point:
        return None
    if isinstance(point, dict):
        point = (point.get("x"), point.get("y"))
    try:
        raw_x, raw_y = point[0], point[1]
        if raw_x is None or raw_y is None:
            return None
        x, y = int(raw_x), int(raw_y)
    except (TypeError, ValueError, IndexError):
        return None
    return None if (x == 0 and y == 0) else (x, y)


class PopupGuard:
    def __init__(self, config, template_dir, capture, click, log, wait, pause, stop,
                 fallback_close=None):
        """
        capture(hwnd) -> BGR frame of the client or None
        click(hwnd, x, y) -> True if the click was sent (x, y in client pixels)
        wait(seconds) -> False if the macro was stopped while waiting
        pause() / stop(): pause or stop the macro
        fallback_close() -> (x, y) in the 800x600 reference space, or None (the older
            Goliath 'popup_close' point, used only when the guard has no close point)
        """
        self._cfg = (config or {}).get("popup_guard") or {}
        self._capture, self._click, self._log = capture, click, log
        self._wait, self._pause, self._stop = wait, pause, stop
        self._fallback_close = fallback_close
        self._scaled = {}
        self._warned = set()
        self.closed = 0
        self._template = None
        self.threshold = float(self._cfg.get("threshold", DEFAULT_THRESHOLD))
        self._reference = _reference_size(self._cfg)

        if self._cfg.get("enabled", True):
            name = str(self._cfg.get("template") or TEMPLATE_NAME)
            path = os.path.join(template_dir, name)
            # A picture name that points outside the templates folder is never read.
            usable = os.path.basename(name) == name and os.path.isfile(path)
            self._template = cv2.imread(path, cv2.IMREAD_COLOR) if usable else None
            if self._template is None:
                self._log("[POPUP] Purchase pop-up guard is OFF: no pop-up picture yet. Open the "
                          "pop-up once and use Settings > Capture popup so the macro can close "
                          "it instead of clicking over it.")

    @property
    def active(self):
        return self._template is not None

    # ------------------------------------------------------------ matching
    def _template_for(self, scale):
        template = self._template
        if template is None:
            raise RuntimeError("PopupGuard is not active")
        if abs(scale - 1.0) <= 0.02:
            return template, 1.0
        key = round(scale, 3)
        if key not in self._scaled:
            interp = cv2.INTER_LINEAR if scale > 1 else cv2.INTER_AREA
            self._scaled[key] = cv2.resize(template, (0, 0), fx=scale, fy=scale,
                                           interpolation=interp)
        return self._scaled[key], scale

    def _find(self, frame):
        """(x, y, score, scale) of the popup in this frame, or None."""
        if frame is None or not hasattr(frame, "shape") or frame.ndim != 3:
            return None
        height, width = frame.shape[:2]
        scale = width / float(self._reference[0])
        template, scale = self._template_for(scale)
        th, tw = template.shape[:2]
        if th < 1 or tw < 1 or th > height or tw > width:
            return None
        result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
        _, best, _, (x, y) = cv2.minMaxLoc(result)
        if not np.isfinite(best) or best < self.threshold:
            return None
        return x, y, float(best), scale

    def _close_point(self, match, frame):
        """Where to click, in client pixels, or None when no safe point is known."""
        x, y, _score, scale = match
        rel = _calibrated(self._cfg.get("close_rel"))
        if rel is not None:
            point = (x + round(rel[0] * scale), y + round(rel[1] * scale))
        else:
            fallback = _calibrated(self._fallback_close() if self._fallback_close else None)
            if fallback is None:
                return None
            point = (round(fallback[0] * scale), round(fallback[1] * scale))
        height, width = frame.shape[:2]
        if not (0 <= point[0] < width and 0 <= point[1] < height):
            return None
        return point

    def _far_from_where_captured(self, match):
        origin = _calibrated(self._cfg.get("origin"))
        if origin is None:
            return False
        x, y, _score, scale = match
        limit = MAX_SHIFT * scale
        return abs(x - origin[0] * scale) > limit or abs(y - origin[1] * scale) > limit

    # ------------------------------------------------------------ the check
    def _say_once(self, key, message):
        if key not in self._warned:
            self._warned.add(key)
            self._log(message)

    def _block(self, key, message):
        """Refuse the click: say why (once per popup) and pause the macro."""
        self._say_once(key, message)
        self._pause()
        return False

    def ensure_clear(self, hwnd):
        """True when it is safe to click: no purchase popup, or it was closed and is gone."""
        if not self.active:
            return True
        frame = self._capture(hwnd)
        match = self._find(frame)
        if match is None:
            self._warned.clear()       # a later popup should be reported again
            return True

        for attempt in range(1, CLOSE_ATTEMPTS + 1):
            if self._far_from_where_captured(match):
                return self._block(
                    "far", "[POPUP] A purchase pop-up is on screen but not where it was when it "
                           "was captured, so nothing was clicked. Pausing: close it yourself, then "
                           "press F7 to resume.")
            point = self._close_point(match, frame)
            if point is None:
                return self._block(
                    "noclose", "[POPUP] A purchase pop-up is on screen and no close button is "
                               "calibrated, so nothing was clicked. Pausing: close it yourself, "
                               "set the close button in Settings > Capture popup, then press F7.")
            self._log(f"[POPUP] Purchase pop-up on screen - closing it at {point} "
                      f"(try {attempt}/{CLOSE_ATTEMPTS}).")
            if not self._click(hwnd, point[0], point[1]):
                return self._block(
                    "noclick", "[POPUP] Could not click the pop-up's close button. Pausing: "
                               "close the pop-up yourself, then press F7.")
            if not self._wait(SETTLE_SECONDS):
                return False             # the player stopped the macro
            frame = self._capture(hwnd)
            if frame is None:
                return self._block(
                    "unverified", "[POPUP] Clicked the pop-up's close button but cannot see the "
                                  "game to check it closed. Pausing: check the screen, then press F7.")
            match = self._find(frame)
            if match is None:
                self.closed += 1
                self._log("[POPUP] Pop-up closed.")
                self._warned.clear()
                return True

        self._log("[POPUP] The purchase pop-up is still on screen after "
                  f"{CLOSE_ATTEMPTS} tries. Stopping the macro so nothing else is clicked.")
        self._stop()
        return False


# ---------------------------------------------------------------------- capturing it
def save_template(base_dir, frame, box, close, name=TEMPLATE_NAME):
    """Save the popup picture and return the `popup_guard` settings for the profile.

    box   = (x, y, width, height) of the popup in the client frame
    close = (x, y) of its close button in the same frame
    name  = file name inside `templates/`. Each profile uses its own, because the close
            button position is stored relative to this exact picture and client size.
    """
    if os.path.basename(name) != name or not name.lower().endswith(".png") or name.startswith("."):
        raise ValueError("The pop-up picture name must be a plain .png file name.")
    x, y, w, h = (int(v) for v in box)
    height, width = frame.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + w), min(height, y + h)
    if (x1 - x0) < MIN_TEMPLATE_SIDE or (y1 - y0) < MIN_TEMPLATE_SIDE:
        raise ValueError("The selection is too small to recognise the pop-up. Draw a box around "
                         "the whole pop-up.")
    cx, cy = int(close[0]), int(close[1])
    if not (x0 <= cx < x1 and y0 <= cy < y1):
        raise ValueError("The close button must be inside the pop-up box.")

    folder = os.path.join(base_dir, "templates")
    os.makedirs(folder, exist_ok=True)
    final = os.path.join(folder, name)
    partial = os.path.join(folder, "~" + name)
    if not cv2.imwrite(partial, frame[y0:y1, x0:x1]):
        raise OSError("Could not write the pop-up picture.")
    os.replace(partial, final)          # never leave a half-written picture behind

    return {
        "enabled": True,
        "template": name,
        "threshold": DEFAULT_THRESHOLD,
        "origin": {"x": x0, "y": y0},
        "close_rel": {"x": cx - x0, "y": cy - y0},
        "reference_client": {"width": width, "height": height},
    }
