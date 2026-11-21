"""Shared pytest setup.

Legacy tests open files such as "profiles/Mutation.json" relative to the project root, so
every test runs with the project root as the working directory.
"""

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# In a virtual environment Tcl sometimes fails to find init.tcl ("Can't find a usable
# init.tcl") when a test creates a Tk root. Point it at the base install explicitly.
for _var, _sub in (("TCL_LIBRARY", "tcl8.6"), ("TK_LIBRARY", "tk8.6")):
    _path = os.path.join(sys.base_prefix, "tcl", _sub)
    if not os.environ.get(_var) and os.path.isdir(_path):
        os.environ[_var] = _path


@pytest.fixture(autouse=True)
def _run_from_project_root(monkeypatch):
    monkeypatch.chdir(PROJECT_ROOT)


class FakeClock:
    """Monotonic clock tests can move by hand."""

    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def fake_clock():
    return FakeClock()


# ---------------------------------------------------------------------------------------
# Fakes for the service boundary (Constitution III). They mirror the public methods of the
# real WindowManager, InputController and VisionScanner, so a test can swap one in without
# touching Win32, the screen or the network. None of them enforces the stop event on its
# own: a test that checks "no input after stop" reads the recorded timestamps instead.
# ---------------------------------------------------------------------------------------
class FakeWM:
    """WindowManager stand-in. `windows` maps hwnd -> dict(pid, title, minimized, offset)."""

    def __init__(self, windows=None, enum_delay=0.0):
        self.windows = dict(windows or {})
        self.enum_delay = enum_delay        # seconds each enumeration takes (slow-machine test)
        self.calls = []                     # (method, args) in call order
        self.foreground = None

    def _note(self, name, *args):
        self.calls.append((name, args))

    def is_minimized(self, hwnd):
        return bool(self.windows.get(hwnd, {}).get("minimized"))

    def find_windows(self, include_minimized=False):
        self._note("find_windows")
        if self.enum_delay:
            import time
            time.sleep(self.enum_delay)
        return sorted(h for h, w in self.windows.items() if not w.get("hidden"))

    def count_minimized_clients(self):
        self._note("count_minimized_clients")
        if self.enum_delay:
            import time
            time.sleep(self.enum_delay)
        return sum(1 for w in self.windows.values() if w.get("minimized"))

    def get_pid(self, hwnd):
        return self.windows.get(hwnd, {}).get("pid", 0)

    def get_window_title(self, hwnd):
        return self.windows.get(hwnd, {}).get("title", "Roblox Game Client")

    def get_window_offset(self, hwnd):
        return tuple(self.windows.get(hwnd, {}).get("offset", (0, 0)))

    get_client_offset = get_window_offset

    def force_activate_window(self, hwnd, max_wait_sec=1.5, auto_shrink=False):
        self._note("force_activate_window", hwnd)
        if hwnd not in self.windows:
            return False
        self.foreground = hwnd
        return True

    def is_foreground(self, hwnd):
        return self.foreground == hwnd


class FakeInput:
    """InputController stand-in that records every action with its time.

    `actions` holds (time, kind, x, y). `clock` is any zero-argument time source; it
    defaults to time.monotonic so latency tests see real time.
    """

    def __init__(self, clock=None):
        import time
        self._clock = clock or time.monotonic
        self.actions = []

    def _record(self, kind, x=None, y=None):
        self.actions.append((self._clock(), kind, x, y))

    def place_cursor(self, screen_x, screen_y, attempts=3):
        self._record("move", screen_x, screen_y)
        return True

    def click(self, x, y, *args, **kwargs):
        self._record("click", x, y)

    click_native = click
    click_ahk = click

    def double_click(self, x, y, *args, **kwargs):
        self._record("double_click", x, y)

    def last_action_time(self):
        return self.actions[-1][0] if self.actions else None


class FakeVision:
    """VisionScanner stand-in. `frames` maps hwnd -> image (any object) or None."""

    def __init__(self, frames=None, slot_state=None):
        self.frames = dict(frames or {})
        self.slot_state = slot_state
        self.captures = []
        self.fail_next = None               # an exception instance raised by the next capture

    def capture_client_full(self, hwnd):
        self.captures.append(hwnd)
        if self.fail_next is not None:
            err, self.fail_next = self.fail_next, None
            raise err
        return self.frames.get(hwnd)

    def analyze_slot_advanced(self, full_image, slot_cfg):
        return self.slot_state

    def verify_action_success(self, before_crop, after_crop, expected_action, slot_name):
        return True

    def save_full_capture(self, full_image, tag):
        return None


class FakeHttp:
    """Scripted network. routes maps a URL to a value, an Exception, or a list of them.

    A list is consumed one item per call (the last item repeats), so a test can script
    "fail, fail, succeed". Values are returned as-is; exceptions are raised.
    """

    def __init__(self, routes=None, clock=None):
        import time
        self._clock = clock or time.monotonic
        self.routes = dict(routes or {})
        self.requests = []                  # (time, method, url, payload)

    def _answer(self, method, url, payload=None):
        self.requests.append((self._clock(), method, url, payload))
        if url not in self.routes:
            raise ConnectionError(f"FakeHttp: no route for {url}")
        value = self.routes[url]
        if isinstance(value, list):
            value = value.pop(0) if len(value) > 1 else value[0]
        if isinstance(value, Exception):
            raise value
        return value

    def get(self, url, timeout=None):
        return self._answer("GET", url)

    def post(self, url, payload=None, timeout=None):
        return self._answer("POST", url, payload)


@pytest.fixture
def fake_wm():
    return FakeWM()


@pytest.fixture
def fake_input():
    return FakeInput()


@pytest.fixture
def fake_vision():
    return FakeVision()


@pytest.fixture
def fake_http():
    return FakeHttp()


# ---------------------------------------------------------------------------------------
# Keeping the player's real data safe (Constitution III)
# ---------------------------------------------------------------------------------------
_DATA_FILES = ("config.json", "config_mutation.json", "account_schedule.json",
               "studio_settings.json")


def _real_data_digest():
    """A fingerprint of the player's real profiles and configs, for the end-of-run check."""
    import hashlib
    digest = {}
    paths = [os.path.join(PROJECT_ROOT, name) for name in _DATA_FILES]
    profiles = os.path.join(PROJECT_ROOT, "profiles")
    if os.path.isdir(profiles):
        paths += [os.path.join(profiles, n) for n in sorted(os.listdir(profiles))]
    for path in paths:
        if os.path.isfile(path):
            with open(path, "rb") as handle:
                digest[os.path.relpath(path, PROJECT_ROOT)] = hashlib.sha256(
                    handle.read()).hexdigest()
    return digest


@pytest.fixture(scope="session", autouse=True)
def _real_data_untouched():
    """Fail the run if any test changed the real profiles or config files."""
    before = _real_data_digest()
    yield
    after = _real_data_digest()
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    assert not changed, f"tests changed real user data: {changed}"


@pytest.fixture
def tmp_base_dir(tmp_path, monkeypatch):
    """A throwaway copy of the data root; the app and any child process use it.

    Sets MACRO_STUDIO_DATA_DIR, which get_base_dir() honours when running from source.
    Modules that already computed a path at import time keep the old one, so use this
    with tests that start a fresh Python process (run_in_fresh_python), which inherit
    the variable before anything is imported.
    """
    import shutil
    for name in _DATA_FILES:
        src = os.path.join(PROJECT_ROOT, name)
        if os.path.isfile(src):
            shutil.copy2(src, tmp_path / name)
    for folder in ("profiles", "templates"):
        src = os.path.join(PROJECT_ROOT, folder)
        if os.path.isdir(src):
            shutil.copytree(src, tmp_path / folder)
    monkeypatch.setenv("MACRO_STUDIO_DATA_DIR", str(tmp_path))
    return tmp_path


def run_in_fresh_python(script, *args, timeout=180):
    """Run a test script in its own Python process; return (exit code, output).

    Tk is unreliable when a second root is created in one process after the first
    was destroyed (intermittent "invalid command name tcl_findLibrary"). The app
    itself never does that - each window type runs in its own process - so GUI
    tests do the same.
    """
    import subprocess

    env = dict(os.environ, PYTHONPATH=PROJECT_ROOT, PYTHONIOENCODING="utf-8",
               PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run([sys.executable, script, *args], cwd=PROJECT_ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr
