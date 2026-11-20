"""Replays a recording step by step, checking the screen before every press (spec 003, C003).

No Windows calls in here: input, window geometry, capture, the popup guard, waiting and
stopping are handed in, so every rule is unit-tested. Safety rules (spec 003):
- The window must be in front before any input; if it cannot be, nothing is sent.
- Before each button press: the popup guard, then a look at the screen. The saved picture of the
  spot must be found within 40 px of where it was recorded; the click follows that small shift.
  If it never shows up the replay stops without clicking.
- Stop is polled in every wait; whatever is held down is released on every way out.
"""

import time
from dataclasses import dataclass

import cv2
import numpy as np

MAX_SHIFT = 40
THRESHOLD = 0.85
FLAT_STD = 6.0
FLAT_MAX_DIFF = 12.0
RETRY_S = 0.25
ASPECT_TOLERANCE = 0.02
SIZE_TOLERANCE_PX = 3
SPEED_RANGE = (0.25, 4.0)
DEFAULT_STEP_TIMEOUT_S = 10.0


def find_picture(frame, template, box, scale, max_shift=MAX_SHIFT, threshold=THRESHOLD):
    """(dx, dy, score) where the saved picture is now, or None.

    `box` = (x, y, w, h) where it was saved at the recorded size; `scale` = (sx, sy) of the
    current window against the recorded one. The search stays within `max_shift` pixels.
    """
    if frame is None or template is None or not getattr(template, "size", 0):
        return None
    bx, by, bw, bh = box
    sx, sy = scale
    tw, th = max(1, round(bw * sx)), max(1, round(bh * sy))
    if template.shape[1] != tw or template.shape[0] != th:
        template = cv2.resize(template, (tw, th),
                              interpolation=cv2.INTER_AREA if tw < template.shape[1]
                              else cv2.INTER_LINEAR)
    ex, ey = round(bx * sx), round(by * sy)
    height, width = frame.shape[:2]
    x0, y0 = max(0, ex - max_shift), max(0, ey - max_shift)
    x1, y1 = min(width, ex + tw + max_shift), min(height, ey + th + max_shift)
    if y1 - y0 < th or x1 - x0 < tw:
        return None
    if float(np.std(template)) < FLAT_STD:
        # A plain patch matches anywhere; only the spot itself can be checked.
        patch = frame[ey:ey + th, ex:ex + tw]
        if patch.shape != template.shape:
            return None
        diff = float(np.mean(np.abs(patch.astype(np.int16) - template.astype(np.int16))))
        return (0, 0, 1.0 - diff / 255.0) if diff <= FLAT_MAX_DIFF else None
    result = cv2.matchTemplate(frame[y0:y1, x0:x1], template, cv2.TM_CCOEFF_NORMED)
    _low, best, _lowloc, where = cv2.minMaxLoc(result)
    if best < threshold:
        return None
    dx, dy = x0 + where[0] - ex, y0 + where[1] - ey
    if abs(dx) > max_shift or abs(dy) > max_shift:
        return None
    return dx, dy, float(best)


def _screen_point(step, left, top, sx, sy, shift):
    """Where on screen a step goes: window origin + scaled position + the picture-check shift."""
    return left + round(step.x * sx + shift[0]), top + round(step.y * sy + shift[1])


@dataclass
class Result:
    ok: bool
    reason: str = ""
    steps_done: int = 0
    stopped: bool = False
    unverified: int = 0


class _Stop(Exception):
    """Internal: ends the run with a reason (not an error in the program)."""

    def __init__(self, reason, stopped=False):
        super().__init__(reason)
        self.reason, self.stopped = reason, stopped


class Player:
    def __init__(self, io, capture, guard, wait, stop, log, clock=time.monotonic):
        """io: client_info, focus, is_foreground, move, button, wheel, key.
        capture(hwnd) -> BGR frame; guard.ensure_clear(hwnd) -> bool (or None for no guard);
        wait(seconds) -> False when stopped; stop() -> True when stop was requested."""
        self._io, self._capture, self._guard = io, capture, guard
        self._wait, self._stop, self._log, self._clock = wait, stop, log, clock

    # ------------------------------------------------------------ geometry
    def _geometry(self, hwnd, rec):
        info = self._io.client_info(hwnd)
        if not info:
            raise _Stop("The Roblox window closed.")
        left, top, width, height = info
        if width <= 0 or height <= 0:
            raise _Stop("The Roblox window closed.")
        if abs((width / height) / (rec.client_w / rec.client_h) - 1) > ASPECT_TOLERANCE:
            raise _Stop(f"The window size is {width}x{height} but this was recorded at "
                        f"{rec.client_w}x{rec.client_h}. Resize the window to match.")
        sx = 1.0 if abs(width - rec.client_w) <= SIZE_TOLERANCE_PX else width / rec.client_w
        sy = 1.0 if abs(height - rec.client_h) <= SIZE_TOLERANCE_PX else height / rec.client_h
        return left, top, sx, sy

    def _front(self, hwnd):
        if self._io.is_foreground(hwnd) or self._io.focus(hwnd):
            return
        raise _Stop("Could not bring the Roblox window to the front, so nothing was sent.")

    # ------------------------------------------------------------ the run
    def play(self, rec, hwnd, load_picture, speed=1.0, step_timeout=DEFAULT_STEP_TIMEOUT_S,
             progress=None, verify=True):
        """Replay `rec`. `verify` False sends every click to its exact recorded position without
        looking at the screen; True checks the saved picture of each click first."""
        if not rec.steps:
            return Result(False, "This recording has no steps.")
        if not SPEED_RANGE[0] <= speed <= SPEED_RANGE[1]:
            return Result(False, f"Speed must be between {SPEED_RANGE[0]}x and {SPEED_RANGE[1]}x.")
        buttons, keys = set(), set()
        done = unverified = 0
        try:
            self._geometry(hwnd, rec)
            if not self._io.focus(hwnd) and not self._io.is_foreground(hwnd):
                raise _Stop("Could not bring the Roblox window to the front, so nothing was sent.")
            due, shift = self._clock(), (0, 0)
            for index, step in enumerate(rec.steps, start=1):
                due += step.dt / 1000.0 / speed
                delay = due - self._clock()
                if delay > 0 and not self._wait(delay):
                    raise _Stop("Stopped.", stopped=True)
                if self._stop():
                    raise _Stop("Stopped.", stopped=True)
                left, top, sx, sy = self._geometry(hwnd, rec)
                self._front(hwnd)

                if step.kind == "down":
                    shift, found = self._check_press(hwnd, step, index, (sx, sy), load_picture,
                                                     step_timeout, shift, verify)
                    if not found:
                        unverified += 1
                        if unverified == 1:
                            self._log(f"[RECORDER] Step {index} has no picture; clicked on "
                                      f"timing only.")
                    due = max(due, self._clock())
                    self._io.move(*_screen_point(step, left, top, sx, sy, shift))
                    self._io.button(step.button, True)
                    buttons.add(step.button)
                elif step.kind == "up":
                    self._io.move(*_screen_point(step, left, top, sx, sy, shift))
                    self._io.button(step.button, False)
                    buttons.discard(step.button)
                    shift = (0, 0)
                elif step.kind == "move":
                    self._io.move(*_screen_point(step, left, top, sx, sy, shift))
                elif step.kind == "wheel":
                    self._io.move(*_screen_point(step, left, top, sx, sy, shift))
                    self._io.wheel(step.delta)
                elif step.kind == "key_down":
                    self._io.key(step.vk, True)
                    keys.add(step.vk)
                elif step.kind == "key_up":
                    self._io.key(step.vk, False)
                    keys.discard(step.vk)
                done = index
                if progress:
                    progress(index, len(rec.steps))
            return Result(True, "Done.", done, unverified=unverified)
        except _Stop as stop:
            return Result(False, stop.reason, done, stop.stopped, unverified)
        except OSError as err:
            return Result(False, f"Input failed at step {done + 1}: {err}", done,
                          unverified=unverified)
        finally:
            self._release(buttons, keys)

    def _check_press(self, hwnd, step, index, scale, load_picture, timeout, shift, verify=True):
        """Guard, then the picture check. Returns (shift to use, whether a picture was checked)."""
        if self._guard is not None and self._guard.ensure_clear(hwnd) is False:
            raise _Stop("A purchase pop-up is covering the game, so nothing was clicked.")
        if not verify:
            return shift, True              # exact position, no screen check, nothing to count
        if not step.look:
            return shift, False
        template = load_picture(step.look["file"])
        if template is None:
            raise _Stop(f"Step {index}: its saved picture is missing, so nothing was clicked.")
        deadline = self._clock() + timeout
        while True:
            found = find_picture(self._capture(hwnd), template, tuple(step.look["box"]), scale)
            if found is not None:
                return (found[0], found[1]), True
            if self._clock() >= deadline:
                raise _Stop(f"Step {index}: the spot does not look the way it did when "
                            f"recorded, so nothing was clicked.")
            if not self._wait(RETRY_S):
                raise _Stop("Stopped.", stopped=True)

    def _release(self, buttons, keys):
        for name in sorted(buttons):
            try:
                self._io.button(name, False)
            except OSError as err:
                self._log(f"[RECORDER] Could not release the {name} button: {err}")
        for vk in sorted(keys):
            try:
                self._io.key(vk, False)
            except OSError as err:
                self._log(f"[RECORDER] Could not release key {vk}: {err}")
