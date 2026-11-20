"""RunnerBase: the plumbing both macro runners share (T020, T021)."""

import threading

import pytest

from macro_studio.runners import base as base_mod
from macro_studio.runners.base import RunnerBase
from macro_studio.services.automation import AutomationLock
from macro_studio.services.hotkeys import HotkeyService

VK_F6, VK_F7, VK_ESC = 0x75, 0x76, 0x1B


class FakeKeyboard:
    def __init__(self):
        self.down = set()

    def __call__(self, vk):
        return vk in self.down


class DemoRunner(RunnerBase):
    """Smallest possible runner: loops until stopped, recording what happened."""

    name = "demo"

    def __init__(self, hotkeys=None, automation=None, **callbacks):
        self.config = {"reference_client": {"width": 800, "height": 600}}
        self.events = []
        self.loop_started = threading.Event()
        self._init_runtime(hotkeys=hotkeys, automation=automation, **callbacks)

    def _on_stopped(self):
        self.events.append("stopped")

    def _on_pause_changed(self):
        self.events.append("paused" if self.paused else "resumed")

    def _reference_config(self):
        return self.config

    def _run_loop(self):
        automation = getattr(self, "_automation", None)
        self.events.append(("owner", automation.owner if automation else None))
        self.loop_started.set()
        while self.running:
            self.sleep_interruptible(0.01)


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    path = tmp_path / "debug_log.txt"
    monkeypatch.setattr(base_mod, "LOG_FILE_PATH", str(path))
    return path


def shared_services():
    kb = FakeKeyboard()
    return kb, HotkeyService(key_state=kb), AutomationLock()


def test_log_goes_to_file_and_callback(log_file):
    got = []
    r = DemoRunner(*shared_services()[1:], log_callback=got.append)
    r.log("hello")
    assert got == ["hello"]
    assert "hello" in log_file.read_text(encoding="utf-8")


def test_failing_callbacks_never_break_the_runner(log_file):
    def boom(*a):
        raise RuntimeError("ui gone")

    r = DemoRunner(*shared_services()[1:], log_callback=boom, stats_callback=boom,
                   frame_callback=boom)
    r.log("x")
    r._notify_stats()
    r._notify_frame(1, None, "EMPTY_SLOT", "reason")


def test_stop_is_idempotent_and_runs_hook_once(log_file):
    r = DemoRunner(*shared_services()[1:])
    r.stop()
    r.stop()
    assert r.events == ["stopped"] and r.running is False


def test_toggle_pause_runs_hook(log_file):
    r = DemoRunner(*shared_services()[1:])
    r.toggle_pause()
    r.toggle_pause()
    assert r.events == ["paused", "resumed"]


def test_sleep_returns_early_on_stop_or_emergency(log_file):
    _, hk, lock = shared_services()
    r = DemoRunner(hk, lock)
    assert r.sleep_interruptible(0) is True
    lock.stop_event.set()
    assert r.sleep_interruptible(5) is False
    lock.stop_event.clear()
    r.running = False
    assert r.sleep_interruptible(5) is False


def test_mapper_falls_back_to_identity_on_unreadable_client(log_file, monkeypatch):
    from macro_studio.core import scaling

    class ZeroMapper:
        client_size = (0, 0)

    monkeypatch.setattr(scaling.CoordinateMapper, "for_window",
                        classmethod(lambda cls, hwnd, reference: ZeroMapper()))
    mapper = DemoRunner(*shared_services()[1:])._mapper(123)
    assert mapper.is_identity


def test_run_holds_the_automation_lock_and_releases_it(log_file):
    _, hk, lock = shared_services()
    r = DemoRunner(hk, lock)
    t = threading.Thread(target=r.run)
    t.start()
    assert r.loop_started.wait(2)
    assert lock.owner == "demo" and not lock.try_borrow("layout")
    r.stop()
    t.join(2)
    assert lock.owner is None
    assert ("owner", "demo") in r.events


def test_second_runner_refused_while_first_runs(log_file):
    _, hk, lock = shared_services()
    first, second = DemoRunner(hk, lock), DemoRunner(hk, lock)
    t = threading.Thread(target=first.run)
    t.start()
    assert first.loop_started.wait(2)
    second.run()  # returns at once: lock held
    assert not second.loop_started.is_set()
    first.stop()
    t.join(2)


def test_f6_and_esc_stop_f7_pauses(log_file):
    for stop_key in (VK_F6, VK_ESC):
        kb, hk, lock = shared_services()
        r = DemoRunner(hk, lock)
        t = threading.Thread(target=r.run)
        t.start()
        assert r.loop_started.wait(2)
        kb.down = {VK_F7}
        hk.poll_once()
        assert r.paused
        kb.down = {stop_key}
        hk.poll_once()
        t.join(2)
        assert not t.is_alive() and not r.running
        assert lock.stop_event.is_set()


def test_standalone_runner_starts_and_stops_its_own_hotkeys(log_file):
    # Command line / no app: the runner must bring its own F6 handling.
    r = DemoRunner()
    assert r._hotkeys is not None and r._automation is not None
    t = threading.Thread(target=r.run)
    t.start()
    assert r.loop_started.wait(2)
    assert r._hotkeys.running
    r.stop()
    t.join(2)
    assert not r._hotkeys.running


def test_run_works_on_a_runner_built_without_init(log_file):
    # Legacy tests build runners with object.__new__ and set only what they use.
    r = object.__new__(DemoRunner)
    r.events, r.running = [], False
    r.loop_started = threading.Event()
    r.run()
    assert ("owner", None) in r.events
