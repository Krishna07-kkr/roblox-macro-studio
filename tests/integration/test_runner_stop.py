"""Stop means stop: no mouse input more than 0.1 s after the stop signal (spec 002, A3, H6).

Runs the real InputController against a fake Win32 layer that records every event with its time.
A button that was already pressed is released at once (a held button would be worse than a
click), but nothing new is pressed or moved after the stop.
"""

import random
import subprocess
import threading
import time

import win32con

from macro_studio.core import input_controller as ic_mod
from macro_studio.core.input_controller import (
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    InputController,
)

LATENCY = 0.1


class FakeUser32:
    """Records (time, kind). The cursor goes where SetCursorPos puts it."""

    def __init__(self):
        self.events = []
        self.pos = (0, 0)

    def _note(self, kind):
        self.events.append((time.monotonic(), kind))

    def IsWindow(self, hwnd):
        return 1

    def SetCursorPos(self, x, y):
        self.pos = (x, y)
        self._note("move")
        return 1

    def GetCursorPos(self, ref):
        ref._obj.x, ref._obj.y = self.pos
        return 1

    def mouse_event(self, flags, dx, dy, data, extra):
        if flags == win32con.MOUSEEVENTF_MOVE:
            self._note("nudge")
        elif flags == win32con.MOUSEEVENTF_LEFTDOWN:
            self._note("down")
        elif flags == win32con.MOUSEEVENTF_LEFTUP:
            self._note("up")
        return 1

    def SendInput(self, count, ref, size):
        flags = ref._obj.union.mi.dwFlags
        self._note("down" if flags & MOUSEEVENTF_LEFTDOWN else "up" if flags & MOUSEEVENTF_LEFTUP
                   else "other")
        return 1

    def kinds(self):
        return [kind for _t, kind in self.events]


def make_controller(monkeypatch, stop_flag, **attrs):
    fake = FakeUser32()
    monkeypatch.setattr(ic_mod, "user32", fake)
    monkeypatch.setattr(ic_mod, "attach_input_desktop", lambda: True)
    ctl = InputController.__new__(InputController)
    ctl.click_delay, ctl.hold_time, ctl.engine = 0.0, 0.1, "native"
    ctl.ahk_path, ctl.ahk_version = None, 0
    ctl.stop_check = stop_flag.is_set
    for name, value in attrs.items():
        setattr(ctl, name, value)
    return ctl, fake


# ------------------------------------------------------------------ native clicks
def test_a_normal_click_still_works(monkeypatch):
    stop = threading.Event()
    ctl, fake = make_controller(monkeypatch, stop)
    assert ctl.click(100, 100, client_offset=(10, 10), relative=True) is True
    kinds = fake.kinds()
    assert kinds.index("down") < kinds.index("up")
    assert fake.pos == (110, 110)


def test_no_input_at_all_when_already_stopped(monkeypatch):
    stop = threading.Event()
    stop.set()
    ctl, fake = make_controller(monkeypatch, stop)
    assert ctl.click(100, 100) is False
    assert fake.events == []


def test_stop_during_a_click_sends_nothing_new_and_releases_at_once(monkeypatch):
    rng = random.Random(7)
    for run in range(30):
        stop = threading.Event()
        ctl, fake = make_controller(monkeypatch, stop)
        worker = threading.Thread(target=lambda: ctl.click(100, 100))
        worker.start()
        time.sleep(rng.uniform(0.0, 0.3))       # anywhere from the first move to the release
        stopped_at = time.monotonic()
        stop.set()
        worker.join(timeout=2)
        assert not worker.is_alive(), f"run {run}: the click never ended"

        after = [(t - stopped_at, kind) for t, kind in fake.events if t > stopped_at]
        late = [(round(t, 3), kind) for t, kind in after if t > LATENCY]
        assert not late, f"run {run}: input more than {LATENCY}s after stop: {late}"
        assert all(kind == "up" for _t, kind in after), f"run {run}: new input after stop: {after}"
        kinds = fake.kinds()
        assert kinds.count("down") == kinds.count("up"), f"run {run}: a button was left held"


def test_double_click_stops_between_the_clicks(monkeypatch):
    stop = threading.Event()
    ctl, fake = make_controller(monkeypatch, stop, hold_time=0.02)
    original = ctl.click

    def click_then_stop(*args, **kwargs):
        result = original(*args, **kwargs)
        stop.set()
        return result
    ctl.click = click_then_stop
    ctl.double_click(50, 50)
    assert fake.kinds().count("down") == 1


def test_waits_give_up_quickly_when_stopped():
    stop = threading.Event()
    ctl = InputController.__new__(InputController)
    ctl.stop_check = stop.is_set
    threading.Timer(0.05, stop.set).start()
    began = time.monotonic()
    assert ctl._wait(5.0) is False
    assert time.monotonic() - began < 0.2


def test_a_controller_with_no_stop_check_behaves_as_before(monkeypatch):
    stop = threading.Event()
    ctl, fake = make_controller(monkeypatch, stop, stop_check=None)
    assert ctl.click(10, 10) is True
    assert "down" in fake.kinds()


# ------------------------------------------------------------------ AutoHotkey clicks
class FakeAhkProcess:
    """A child process that runs until killed or until `duration` has passed."""

    def __init__(self, duration, returncode=0):
        self.duration, self.returncode = duration, returncode
        self.started = time.monotonic()
        self.killed = False

    def communicate(self, input=None, timeout=None):
        if self.killed or time.monotonic() - self.started >= self.duration:
            return "", ""
        time.sleep(timeout or 0)
        if self.killed or time.monotonic() - self.started >= self.duration:
            return "", ""
        raise subprocess.TimeoutExpired("ahk", timeout)

    def kill(self):
        self.killed = True


def make_ahk_controller(monkeypatch, stop, process):
    ctl, fake = make_controller(monkeypatch, stop, engine="ahk", ahk_path="C:\\ahk.exe", ahk_version=2)
    monkeypatch.setattr(ic_mod.subprocess, "Popen", lambda *a, **k: process)
    return ctl, fake


def test_an_ahk_click_that_finishes_is_reported_as_sent(monkeypatch):
    stop = threading.Event()
    ctl, fake = make_ahk_controller(monkeypatch, stop, FakeAhkProcess(duration=0.05))
    fake.pos = (60, 70)                        # where AutoHotkey left the cursor
    assert ctl.click_ahk(60, 70, hold_ms=10) is True


def test_an_ahk_click_on_a_moved_window_is_sent_once_at_the_screen_point(monkeypatch):
    """The window is not at the screen corner, as in real use. The click used to be checked
    against client pixels, fail, and be sent a second time by the native path."""
    stop = threading.Event()
    process = FakeAhkProcess(duration=0.05)
    scripts = []
    original = process.communicate

    def communicate(input=None, timeout=None):
        if input:
            scripts.append(input)
        return original(input, timeout)
    process.communicate = communicate
    ctl, fake = make_ahk_controller(monkeypatch, stop, process)
    fake.pos = (408, 331)                      # AutoHotkey left the cursor on the screen point
    assert ctl.click(400, 300, client_offset=(8, 31), relative=True) is True
    assert "down" not in fake.kinds(), "the native path clicked a second time"
    assert 'CoordMode("Mouse", "Screen")' in scripts[0] and "MouseMove(408, 331" in scripts[0]


def test_stop_kills_a_running_ahk_click_and_releases_the_button(monkeypatch):
    stop = threading.Event()
    process = FakeAhkProcess(duration=10)
    ctl, fake = make_ahk_controller(monkeypatch, stop, process)
    threading.Timer(0.1, stop.set).start()
    began = time.monotonic()
    assert ctl.click_ahk(60, 70, hold_ms=150) is False
    stopped_after = time.monotonic() - began - 0.1
    assert process.killed
    assert stopped_after < LATENCY, stopped_after
    assert fake.kinds() == ["up"]              # a possibly-held button is let go; nothing else


def test_ahk_that_hangs_is_killed_after_the_timeout(monkeypatch):
    stop = threading.Event()
    process = FakeAhkProcess(duration=60)
    ctl, fake = make_ahk_controller(monkeypatch, stop, process)
    ctl.AHK_TIMEOUT_S = 0.2
    assert ctl.click_ahk(60, 70) is False
    assert process.killed


def test_after_a_stopped_ahk_click_the_native_path_is_not_tried(monkeypatch):
    stop = threading.Event()
    process = FakeAhkProcess(duration=10)
    ctl, fake = make_ahk_controller(monkeypatch, stop, process)
    threading.Timer(0.05, stop.set).start()
    assert ctl.click(60, 70) is False
    assert "down" not in fake.kinds() and "move" not in fake.kinds()


# ------------------------------------------------------------------ runners hand it the signal
def test_runners_connect_their_input_to_the_emergency_stop_and_to_their_own_stop():
    from types import SimpleNamespace

    from macro_studio.runners.base import RunnerBase
    from macro_studio.services.automation import AutomationLock

    class Probe(RunnerBase):
        name = "probe"

        def __init__(self):
            self.input = SimpleNamespace(stop_check=None)
            self._init_runtime(automation=AutomationLock())

        def _run_loop(self):
            pass

    runner = Probe()
    runner._connect_input_stop()
    check = runner.input.stop_check
    assert check() is False
    runner._automation.stop_event.set()                  # F6 or Esc
    assert check() is True
    runner._automation.stop_event.clear()
    assert check() is False
    runner.running = False                               # the Stop button
    assert check() is True


def test_both_runners_call_the_connection_after_building_their_input():
    import inspect

    from macro_studio.runners.goliath import MacroRunner
    from macro_studio.runners.mutation import MutationRunner
    for cls in (MacroRunner, MutationRunner):
        source = inspect.getsource(cls.__init__)
        assert source.index("InputController(") < source.index("_connect_input_stop()"), cls
