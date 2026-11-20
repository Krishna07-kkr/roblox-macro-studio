"""One poller for every global hotkey (Constitution II).

Polls GetAsyncKeyState every 15 ms, like the original emergency-stop watchdog: it works
while Roblox has focus and needs no message loop. Callbacks fire once per press (on the
key-down edge) on the poller thread; UI work must be handed to the Tk thread.

F6, Esc and F7 are reserved for emergency stop and pause. Tools cannot bind them; only
`bind_system` (used by the runners and the automation lock) can.
"""

import threading

VK_NAMES = {
    **{f"F{i}": 0x6F + i for i in range(1, 25)},
    **{chr(c): c for c in range(ord("A"), ord("Z") + 1)},
    **{str(d): 0x30 + d for d in range(10)},
    "ESC": 0x1B, "ESCAPE": 0x1B, "SPACE": 0x20, "TAB": 0x09, "ENTER": 0x0D,
    "HOME": 0x24, "END": 0x23, "INSERT": 0x2D, "DELETE": 0x2E,
    "PAGEUP": 0x21, "PAGEDOWN": 0x22, "PAUSE": 0x13,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
}
MODIFIERS = {"CTRL": 0x11, "CONTROL": 0x11, "ALT": 0x12, "SHIFT": 0x10, "WIN": 0x5B}

RESERVED = {
    0x75: "emergency stop (F6)",
    0x1B: "emergency stop (Esc)",
    0x76: "pause / resume (F7)",
}


class HotkeyConflict(ValueError):
    def __init__(self, message, other_action=None):
        super().__init__(message)
        self.other_action = other_action


def parse_key(text):
    """'Ctrl+Alt+G' -> ((VK_CONTROL, VK_MENU), VK_G). Raises ValueError if unknown."""
    parts = [p.strip().upper() for p in str(text).split("+") if p.strip()]
    if not parts:
        raise ValueError(f"No key given: {text!r}")
    *mods, main = parts
    try:
        mod_codes = tuple(sorted(MODIFIERS[m] for m in mods))
    except KeyError as e:
        raise ValueError(f"Unknown modifier {e.args[0]!r} in {text!r}") from None
    if main not in VK_NAMES:
        raise ValueError(f"Unknown key {main!r} in {text!r}")
    return mod_codes, VK_NAMES[main]


def _async_key_state(vk):
    import ctypes
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


class _Binding:
    __slots__ = ("action", "combo", "callback", "was_down")

    def __init__(self, action, combo, callback):
        self.action, self.combo, self.callback = action, combo, callback
        self.was_down = False


class HotkeyService:
    def __init__(self, key_state=_async_key_state, poll_s=0.015, on_error=None):
        self._key_state = key_state
        self._poll_s = poll_s
        self._on_error = on_error or (lambda action, exc: None)
        self._bindings = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def _check_free(self, action, combo):
        for other in self._bindings.values():
            if other.action != action and other.combo == combo:
                raise HotkeyConflict(f"That key is already used by '{other.action}'.",
                                     other_action=other.action)

    def bind(self, action, key, callback):
        """Bind a tool action. Rejects reserved keys and keys used by another action."""
        combo = parse_key(key)
        mods, main = combo
        if not mods and main in RESERVED:
            raise HotkeyConflict(f"{key} is reserved for {RESERVED[main]}.",
                                 other_action=RESERVED[main])
        with self._lock:
            self._check_free(action, combo)
            self._bindings[action] = _Binding(action, combo, callback)

    def bind_system(self, action, key, callback):
        """Bind a core action; may use the reserved stop/pause keys."""
        combo = parse_key(key)
        with self._lock:
            self._check_free(action, combo)
            self._bindings[action] = _Binding(action, combo, callback)

    def unbind(self, action):
        with self._lock:
            self._bindings.pop(action, None)

    def poll_once(self):
        with self._lock:
            bindings = list(self._bindings.values())
        for b in bindings:
            mods, main = b.combo
            down = self._key_state(main) and all(self._key_state(m) for m in mods)
            if down and not b.was_down:
                try:
                    b.callback()
                except Exception as exc:
                    self._on_error(b.action, exc)
            b.was_down = down

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def _loop(self):
        while not self._stop.wait(self._poll_s):
            self.poll_once()

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="studio-hotkeys", daemon=True)
        self._thread.start()

    def stop(self, timeout=1.0):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None
