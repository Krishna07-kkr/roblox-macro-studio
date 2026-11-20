"""Recordings: the steps, the safe on-disk store, and edits (spec 003, C001).

A recording lives in its own folder under `recordings\\` beside the app: `steps.json` plus one
small PNG for every verified button press. Names are plain (letters, digits, space, dash,
underscore) so a name can never point outside the folder. Files written here are atomic, and a
file read back is validated step by step: a damaged or hand-edited file is refused with a message
and can never make the player send a stop key or press a button that is never released.
"""

import contextlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field

import cv2
import numpy as np

from macro_studio.core.fileio import write_bytes_atomic, write_json_atomic

VERSION = 1
STEPS_FILE = "steps.json"
KINDS = ("move", "down", "up", "wheel", "key_down", "key_up")
BUTTONS = ("left", "right", "middle")
#: F6, F7, F8 and Esc stop or pause things; a recording must never contain them.
STOP_VKS = frozenset({0x75, 0x76, 0x77, 0x1B})
MAX_STEPS = 200_000
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_DT_MS = 600_000
MAX_WHEEL = 12_000
COORD_MIN, COORD_MAX = -100, 20_000
CLIENT_MIN, CLIENT_MAX = 50, 20_000
MIN_LOOK_SIDE = 8
_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9 _-]{0,46}[A-Za-z0-9_-])?")
_DEVICE_NAMES = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                           *(f"lpt{i}" for i in range(1, 10))})


class BadName(ValueError):
    """The name is not a plain recording name."""


class BadRecording(ValueError):
    """The recording file is damaged, unsupported or unsafe."""


def check_name(name):
    if not isinstance(name, str) or not _NAME.fullmatch(name) or name.lower() in _DEVICE_NAMES:
        raise BadName("Use 1 to 48 letters, digits, spaces, dashes or underscores (no spaces at "
                      "the ends).")
    return name


@dataclass
class Step:
    kind: str
    dt: int                          # milliseconds since the previous step
    x: int | None = None             # client pixels at the recorded client size
    y: int | None = None
    button: str | None = None
    delta: int | None = None         # wheel, in 120ths of a notch
    vk: int | None = None
    look: dict | None = None         # {"file": "...png", "box": [x, y, w, h]} saved picture
    picture: np.ndarray | None = field(default=None, repr=False, compare=False)
    look_box: tuple | None = None    # box of `picture`, until it is saved

    def to_dict(self):
        out = {"kind": self.kind, "dt": int(self.dt)}
        for key in ("x", "y", "button", "delta", "vk", "look"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out


@dataclass
class Recording:
    name: str
    client_w: int
    client_h: int
    steps: list = field(default_factory=list)

    @property
    def duration_ms(self):
        return sum(int(s.dt) for s in self.steps)


@dataclass(frozen=True)
class RecordingInfo:
    name: str
    steps: int
    seconds: float


# ------------------------------------------------------------------ validation
def _int(value, low, high, what):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise BadRecording(f"{what} is missing or out of range.")
    return value


def _look(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise BadRecording("A picture entry is damaged.")
    name, box = raw.get("file"), raw.get("box")
    if (not isinstance(name, str) or os.path.basename(name) != name or name.startswith(".")
            or not name.lower().endswith(".png")):
        raise BadRecording("A picture file name is not a plain .png name.")
    if not (isinstance(box, list) and len(box) == 4):
        raise BadRecording("A picture box is damaged.")
    x, y, w, h = (_int(v, -COORD_MAX, COORD_MAX, "A picture box value") for v in box)
    if w < MIN_LOOK_SIDE or h < MIN_LOOK_SIDE:
        raise BadRecording("A picture box is too small.")
    return {"file": name, "box": [x, y, w, h]}


def _step(raw):
    if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
        raise BadRecording("A step has an unknown kind.")
    kind = raw["kind"]
    step = Step(kind, _int(raw.get("dt"), 0, MAX_DT_MS, "A pause"))
    if kind in ("move", "down", "up", "wheel"):
        step.x = _int(raw.get("x"), COORD_MIN, COORD_MAX, "A position")
        step.y = _int(raw.get("y"), COORD_MIN, COORD_MAX, "A position")
    if kind in ("down", "up"):
        step.button = raw.get("button")
        if step.button not in BUTTONS:
            raise BadRecording("A mouse button is not left, right or middle.")
        step.look = _look(raw.get("look")) if kind == "down" else None
    if kind == "wheel":
        step.delta = _int(raw.get("delta"), -MAX_WHEEL, MAX_WHEEL, "A wheel amount")
    if kind in ("key_down", "key_up"):
        step.vk = _int(raw.get("vk"), 1, 254, "A key code")
        if step.vk in STOP_VKS:
            raise BadRecording("A recording cannot contain F6, F7, F8 or Esc.")
    return step


def parse(data, name):
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise BadRecording("This recording file is not a version this studio can read.")
    client = data.get("client")
    if not isinstance(client, dict):
        raise BadRecording("The recorded window size is missing.")
    width = _int(client.get("width"), CLIENT_MIN, CLIENT_MAX, "The recorded window width")
    height = _int(client.get("height"), CLIENT_MIN, CLIENT_MAX, "The recorded window height")
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or len(raw_steps) > MAX_STEPS:
        raise BadRecording("The step list is missing or far too long.")
    return Recording(name, width, height, [_step(s) for s in raw_steps])


# ------------------------------------------------------------------ editing
def _partner(steps, index):
    """Index of the release for a press (or the press for a release), or None."""
    step = steps[index]
    pairs = {"down": ("up", 1), "up": ("down", -1), "key_down": ("key_up", 1),
             "key_up": ("key_down", -1)}
    if step.kind not in pairs:
        return None
    other, direction = pairs[step.kind]
    same = (lambda s: s.button == step.button) if step.kind in ("down", "up") \
        else (lambda s: s.vk == step.vk)
    i = index + direction
    while 0 <= i < len(steps):
        if steps[i].kind == other and same(steps[i]):
            return i
        i += direction
    return None


def _drop(rec, indices):
    """Remove steps; the pause of each removed step moves to the next one that stays."""
    carry, kept = 0, []
    for i, step in enumerate(rec.steps):
        if i in indices:
            carry += step.dt
            continue
        step.dt += carry
        carry = 0
        kept.append(step)
    rec.steps = kept


def delete_step(rec, index):
    """Delete one step and its other half, so nothing is left held down."""
    indices = {index}
    partner = _partner(rec.steps, index)
    if partner is not None:
        indices.add(partner)
    _drop(rec, indices)


def trim(rec, start_ms, end_ms):
    """Keep only the steps between two times; no press or release is left without its pair."""
    clock, outside = 0, set()
    for i, step in enumerate(rec.steps):
        clock += step.dt
        if not start_ms <= clock <= end_ms:
            outside.add(i)
    _drop(rec, outside)
    if rec.steps:
        rec.steps[0].dt = 0
    held, orphans = {}, set()
    for i, step in enumerate(rec.steps):
        key = ("b", step.button) if step.kind in ("down", "up") else ("k", step.vk)
        if step.kind in ("down", "key_down"):
            held[key] = i
        elif step.kind in ("up", "key_up") and held.pop(key, None) is None:
            orphans.add(i)
    orphans.update(held.values())
    _drop(rec, orphans)
    if rec.steps:
        rec.steps[0].dt = 0


# ------------------------------------------------------------------ the store
class RecordingStore:
    def __init__(self, base_dir):
        self.root = os.path.join(base_dir, "recordings")

    def _folder(self, name):
        return os.path.join(self.root, check_name(name))

    def save(self, rec):
        folder = self._folder(rec.name)
        os.makedirs(folder, exist_ok=True)
        for step in rec.steps:
            if step.picture is not None and step.look_box is not None:
                ok, buffer = cv2.imencode(".png", step.picture)
                if not ok:
                    raise OSError("Could not encode a step picture.")
                file = f"p{uuid.uuid4().hex[:10]}.png"
                write_bytes_atomic(os.path.join(folder, file), buffer.tobytes())
                step.look = {"file": file, "box": [int(v) for v in step.look_box]}
                step.picture = step.look_box = None
        data = {"version": VERSION, "name": rec.name,
                "client": {"width": int(rec.client_w), "height": int(rec.client_h)},
                "steps": [s.to_dict() for s in rec.steps]}
        write_json_atomic(os.path.join(folder, STEPS_FILE), data, indent=1)
        used = {s.look["file"] for s in rec.steps if s.look}
        for entry in os.listdir(folder):
            if entry.lower().endswith(".png") and entry not in used:
                with contextlib.suppress(OSError):
                    os.remove(os.path.join(folder, entry))

    def load(self, name):
        path = os.path.join(self._folder(name), STEPS_FILE)
        try:
            if os.path.getsize(path) > MAX_FILE_BYTES:
                raise BadRecording("The recording file is far too large.")
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError as err:
            raise BadRecording(f"There is no recording named '{name}'.") from err
        except (OSError, ValueError) as err:
            if isinstance(err, BadRecording):
                raise
            raise BadRecording(f"The recording '{name}' cannot be read: {err}") from err
        return parse(data, name)

    def picture(self, name, file):
        """The saved picture of a step as a BGR image, or None."""
        if os.path.basename(file) != file or not file.lower().endswith(".png"):
            return None
        try:
            raw = np.fromfile(os.path.join(self._folder(name), file), dtype=np.uint8)
        except OSError:
            return None
        return cv2.imdecode(raw, cv2.IMREAD_COLOR) if raw.size else None

    def list(self):
        try:
            names = sorted(os.listdir(self.root), key=str.lower)
        except OSError:
            return []
        rows = []
        for name in names:
            try:
                rec = self.load(name)
            except (BadName, BadRecording):
                continue
            rows.append(RecordingInfo(name, len(rec.steps), rec.duration_ms / 1000.0))
        return rows

    def exists(self, name):
        return os.path.isfile(os.path.join(self._folder(name), STEPS_FILE))

    def delete(self, name):
        folder = self._folder(name)
        if not os.path.isdir(folder):
            return
        for entry in os.listdir(folder):
            if entry == STEPS_FILE or entry.lower().endswith((".png", ".json", ".tmp")) \
                    or entry.startswith(".tmp-"):
                with contextlib.suppress(OSError):
                    os.remove(os.path.join(folder, entry))
        with contextlib.suppress(OSError):
            os.rmdir(folder)

    def duplicate(self, name, new_name):
        source, target = self._folder(name), self._folder(new_name)
        if os.path.exists(target):
            raise BadRecording(f"'{new_name}' already exists.")
        shutil.copytree(source, target)

    def rename(self, name, new_name):
        source, target = self._folder(name), self._folder(new_name)
        if os.path.exists(target):
            raise BadRecording(f"'{new_name}' already exists.")
        os.rename(source, target)
