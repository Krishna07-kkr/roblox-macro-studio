"""Anti-AFK key sequences: names, parsing, presets and the recorder (spec 001, US5).

No Tk and no Win32: key state comes from an `is_down(vk)` function, so the recorder is
unit-tested with a scripted keyboard.

F6, F7 and Esc cannot be used in a sequence. The studio's stop and pause hotkeys read the
keyboard state, which includes keys injected by this very tool, so sending one of them would
stop the macro or the app by itself.
"""

import string
from dataclasses import dataclass

VK_ESCAPE = 0x1B
RESERVED = {"f6", "f7", "esc", "escape"}
MOUSE_VK = {"lbutton": 0x01, "rbutton": 0x02, "mbutton": 0x04, "xbutton1": 0x05, "xbutton2": 0x06}
WHEEL = {"wheelup", "wheeldown"}


def _build_keys():
    keys = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "left": 0x25, "up": 0x26, "right": 0x27,
            "down": 0x28, "lshift": 0xA0, "rshift": 0xA1, "lctrl": 0xA2, "rctrl": 0xA3,
            "lalt": 0xA4, "ralt": 0xA5, "shift": 0x10, "ctrl": 0x11, "alt": 0x12}
    for letter in string.ascii_lowercase:
        keys[letter] = ord(letter.upper())
    for digit in string.digits:
        keys[digit] = ord(digit)
    for number in (1, 2, 3, 4, 5, 8, 9, 10, 11, 12):           # F6 and F7 are the studio's
        keys[f"f{number}"] = 0x6F + number
    return keys


KEYS = _build_keys()
_NAME_FOR_VK = {vk: name for name, vk in KEYS.items()}
_NAME_FOR_VK.update({vk: name for name, vk in MOUSE_VK.items()})

PRESETS = {
    "Space (jump)": "space",
    "W then S": "w,s",
    "Left Shift": "lshift",
    "Left then Right": "left,right",
    "Space + W": "space,w",
    "Left Click": "lbutton",
    "Space + Left Click": "space,lbutton",
}


class BadKey(ValueError):
    """A sequence that cannot be used, with a message the player can act on."""


@dataclass(frozen=True)
class Token:
    name: str
    kind: str          # "key" or "mouse"
    vk: int            # 0 for the wheel


def vk_for(name):
    name = name.lower()
    return KEYS.get(name) or MOUSE_VK[name]


def parse_sequence(text):
    """Turn "space, w, LButton" into tokens, or raise BadKey naming what is wrong."""
    tokens = []
    for raw in (text or "").split(","):
        name = raw.strip().lower()
        if not name:
            continue
        if name in RESERVED:
            raise BadKey(f"'{raw.strip()}' is one of the studio's stop and pause keys, so it "
                         f"cannot be sent. Pick another key.")
        if name in WHEEL:
            tokens.append(Token(name, "mouse", 0))
        elif name in MOUSE_VK:
            tokens.append(Token(name, "mouse", MOUSE_VK[name]))
        elif name in KEYS:
            tokens.append(Token(name, "key", KEYS[name]))
        else:
            raise BadKey(f"'{raw.strip()}' is not a key name I know.")
    if not tokens:
        raise BadKey("Type at least one key, for example: space")
    return tokens


def format_sequence(tokens):
    return ",".join(token.name for token in tokens)


class KeyRecorder:
    """Records keys and mouse buttons pressed while it runs, polled with `tick()`.

    Esc or a timeout ends it (Esc itself is not recorded). A held key counts once. Clicks while
    the studio window is in front are ignored: that is the player pressing Record or Stop.
    """

    def __init__(self, is_down, now, foreground, studio_hwnd, timeout_s=20.0):
        self._is_down, self._now, self._foreground = is_down, now, foreground
        self._studio, self._timeout = studio_hwnd, timeout_s
        self._started = now()
        self._held = set()
        self._tokens = []
        self.finished = False
        self.timed_out = False

    def tick(self):
        if self.finished:
            return
        if self._now() - self._started >= self._timeout:
            self.finished = self.timed_out = True
            return
        down = {vk for vk in _NAME_FOR_VK if self._is_down(vk)}
        if self._is_down(VK_ESCAPE):
            self.finished = True
            return
        for vk in sorted(down - self._held):
            name = _NAME_FOR_VK[vk]
            if name in RESERVED:
                continue
            if vk in MOUSE_VK.values() and self._foreground() == self._studio:
                continue
            self._tokens.append(Token(name, "mouse" if name in MOUSE_VK else "key", vk))
        self._held = down

    def tokens(self):
        return list(self._tokens)
