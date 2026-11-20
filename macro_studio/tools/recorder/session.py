"""Turns raw mouse and key events into recorded steps (spec 003, C002).

No Windows calls in here: where a point is, which window is in front, and a screenshot of a
window are handed in, so every rule is unit-tested. Rules (spec 003):
- Only the first Roblox window the player touches is recorded; events elsewhere are dropped.
- Positions are client pixels. Moves are thinned (3 px and 15 ms apart at least).
- A button press keeps a 64x64 picture of the spot, to verify it on replay.
- Keys count only while that window is in front; auto-repeat counts once.
- F6, F7, Esc are never recorded; F8 asks the recording to end.
- Finishing releases anything still held, so a replay can never leave a key or button down.
"""

from macro_studio.core.inputhook import RawEvent  # noqa: F401 - re-exported
from macro_studio.tools.recorder.model import MAX_DT_MS, MAX_STEPS, STOP_VKS, Recording, Step

VK_STOP_RECORDING = 0x77            # F8
PICTURE_SIDE = 64
MIN_MOVE_PX = 3
MIN_MOVE_GAP_S = 0.015


class Session:
    def __init__(self, window_at, client_info, foreground, capture, max_steps=MAX_STEPS,
                 pictures=True):
        """window_at(x, y) -> Roblox hwnd under a screen point or None;
        client_info(hwnd) -> (left, top, width, height) of its client area on screen;
        foreground() -> the Roblox hwnd in front or None;
        capture(hwnd, t) -> the BGR frame of that window as it was just BEFORE time t, or None
        (a click often changes the screen, so the picture must not be taken after it)."""
        self._window_at, self._client_info = window_at, client_info
        self._foreground, self._capture = foreground, capture
        self._max = max_steps
        self._pictures = pictures          # False: no screenshots, clicks replay at exact positions
        self.steps = []
        self.target = None
        self.client_w = self.client_h = 0
        self.no_picture = 0
        self.stop_requested = False
        self.finished = False
        self.full = False
        self._last_t = None
        self._last_move = None          # (client x, y, t) of the last recorded move
        self._buttons = set()           # recorded presses not yet released
        self._ignored_buttons = set()   # presses that happened outside: their release too
        self._keys = set()
        self._last_xy = (0, 0)

    # ------------------------------------------------------------ helpers
    def _adopt(self, hwnd):
        """Make `hwnd` the recorded window (the first one touched). False if it is another."""
        if self.target is None:
            info = self._client_info(hwnd)
            if not info:
                return False
            self.target = hwnd
            self.client_w, self.client_h = int(info[2]), int(info[3])
        return hwnd == self.target

    def _to_client(self, x, y, clamp=False):
        left, top, width, height = self._client_info(self.target)
        cx, cy = int(x) - int(left), int(y) - int(top)
        if clamp:
            cx, cy = min(max(cx, 0), int(width) - 1), min(max(cy, 0), int(height) - 1)
        return cx, cy

    def _add(self, step, t):
        if len(self.steps) >= self._max:
            self.full = True
            return False
        gap = 0 if self._last_t is None else round((t - self._last_t) * 1000)
        step.dt = max(0, min(gap, MAX_DT_MS))
        self._last_t = t
        self.steps.append(step)
        return True

    def _picture(self, cx, cy, t):
        frame = self._capture(self.target, t)
        if frame is None or not getattr(frame, "size", 0):
            self.no_picture += 1
            return None, None
        height, width = frame.shape[:2]
        side = min(PICTURE_SIDE, width, height)
        x0 = min(max(cx - side // 2, 0), width - side)
        y0 = min(max(cy - side // 2, 0), height - side)
        return frame[y0:y0 + side, x0:x0 + side].copy(), (x0, y0, side, side)

    # ------------------------------------------------------------ events
    def feed(self, event):
        if self.finished or self.full:
            return
        kind = event.kind
        if kind in ("key_down", "key_up"):
            self._key(event)
        else:
            self._mouse(event)

    def _mouse(self, event):
        kind = event.kind
        hwnd = self._window_at(event.x, event.y)
        if kind == "up":
            # A release always follows its press, even if the cursor left the window.
            if event.button in self._ignored_buttons:
                self._ignored_buttons.discard(event.button)
                return
            if event.button in self._buttons and self.target is not None:
                cx, cy = self._to_client(event.x, event.y, clamp=True)
                self._buttons.discard(event.button)
                self._add(Step("up", 0, x=cx, y=cy, button=event.button), event.t)
            return
        if hwnd is None or not self._adopt(hwnd):
            if kind == "down":
                self._ignored_buttons.add(event.button)
            return
        cx, cy = self._to_client(event.x, event.y)
        self._last_xy = (cx, cy)
        if kind == "move":
            last = self._last_move
            if last is not None and ((abs(cx - last[0]) < MIN_MOVE_PX and abs(cy - last[1]) < MIN_MOVE_PX)
                                     or event.t - last[2] < MIN_MOVE_GAP_S):
                return
            if self._add(Step("move", 0, x=cx, y=cy), event.t):
                self._last_move = (cx, cy, event.t)
        elif kind == "down":
            picture, box = self._picture(cx, cy, event.t) if self._pictures else (None, None)
            step = Step("down", 0, x=cx, y=cy, button=event.button, picture=picture,
                        look_box=box)
            if self._add(step, event.t):
                self._buttons.add(event.button)
                self._last_move = (cx, cy, event.t)
        elif kind == "wheel":
            self._add(Step("wheel", 0, x=cx, y=cy, delta=int(event.delta or 0)), event.t)

    def _key(self, event):
        vk = event.vk
        if event.kind == "key_down" and vk == VK_STOP_RECORDING:
            self.stop_requested = True
            return
        if vk in STOP_VKS or vk is None:
            return
        if event.kind == "key_up":
            if vk in self._keys:
                self._keys.discard(vk)
                self._add(Step("key_up", 0, vk=vk), event.t)
            return
        if vk in self._keys:
            return                                  # auto-repeat
        front = self._foreground()
        if front is None or not self._adopt(front):
            return
        if self._add(Step("key_down", 0, vk=vk), event.t):
            self._keys.add(vk)

    # ------------------------------------------------------------ the end
    def finish(self, t):
        """End the recording: release anything still held so a replay cannot leave it down."""
        if self.finished:
            return
        for button in sorted(self._buttons):
            cx, cy = self._last_xy
            self._add(Step("up", 0, x=cx, y=cy, button=button), t)
        for vk in sorted(self._keys):
            self._add(Step("key_up", 0, vk=vk), t)
        self._buttons.clear()
        self._keys.clear()
        self.finished = True

    def to_recording(self, name):
        return Recording(name, self.client_w, self.client_h, list(self.steps))
