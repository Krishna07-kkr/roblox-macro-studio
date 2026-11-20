"""The recorder service: recording state, saving, playing with loops and the lock (spec 003)."""

import threading

import numpy as np
import pytest

from macro_studio.core.inputhook import RawEvent
from macro_studio.services.automation import AutomationLock
from macro_studio.tools.recorder.model import BadName, BadRecording, RecordingStore
from macro_studio.tools.recorder.player import Result
from macro_studio.tools.recorder.service import RecorderService


class FakeHook:
    def __init__(self, ok=True):
        self.ok, self.started, self.stopped, self.events, self.error = ok, False, False, [], ""

    def start(self):
        self.started = self.ok
        if not self.ok:
            self.error = "refused"
        return self.ok

    def stop(self):
        self.stopped = True

    def drain(self, handle, limit=500):
        batch, self.events = self.events[:limit], self.events[limit:]
        for e in batch:
            handle(e)
        return len(batch)


class World:
    frame = np.zeros((600, 800, 3), dtype=np.uint8)

    def window_at(self, x, y):
        return 1 if x >= 0 else None

    def client_info(self, hwnd):
        return (0, 0, 800, 600)

    def foreground(self):
        return 1

    def capture(self, hwnd, t):
        return self.frame


class FakePlayer:
    def __init__(self, results):
        self.results, self.calls = list(results), []

    def play(self, rec, hwnd, load_picture, speed=1.0, step_timeout=10.0, progress=None,
             verify=True):
        self.calls.append((rec.name, hwnd, speed, step_timeout))
        self.verify_seen = verify
        return self.results.pop(0) if self.results else Result(True, "Done.", len(rec.steps))


def ev(kind, t, **kw):
    return RawEvent(kind, t, **kw)


@pytest.fixture
def parts(tmp_path):
    hook, store, lock, logs = FakeHook(), RecordingStore(str(tmp_path)), AutomationLock(), []
    player = FakePlayer([])
    svc = RecorderService(store=store, hook=hook, world=World(), automation=lock,
                          player_factory=lambda: player, log=logs.append, clock=lambda: 100.0)
    return svc, hook, store, lock, player, logs


def record_something(svc, hook):
    assert svc.begin_recording()
    hook.events = [ev("move", 1.0, x=10, y=10), ev("down", 1.1, x=10, y=10, button="left"),
                   ev("up", 1.2, x=10, y=10, button="left")]
    svc.tick()


def test_begin_installs_the_hook_and_end_removes_it(parts):
    svc, hook, *_ = parts
    assert svc.begin_recording() and hook.started and svc.recording
    svc.end_recording()
    assert hook.stopped and not svc.recording


def test_a_hook_that_cannot_start_is_reported_and_nothing_records(parts):
    svc, hook, *_ = parts
    hook.ok = False
    assert not svc.begin_recording() and "refused" in svc.error and not svc.recording


def test_tick_turns_queued_events_into_steps(parts):
    svc, hook, *_ = parts
    record_something(svc, hook)
    assert svc.step_count == 3


def test_f8_ends_the_recording_by_itself(parts):
    svc, hook, *_ = parts
    svc.begin_recording()
    hook.events = [ev("key_down", 1.0, vk=0x77)]
    svc.tick()
    assert svc.stop_requested


def test_a_second_begin_while_recording_is_refused(parts):
    svc, *_ = parts
    assert svc.begin_recording() and not svc.begin_recording()


def test_saving_needs_steps_and_a_good_name(parts):
    svc, hook, store, *_ = parts
    svc.begin_recording()
    svc.end_recording()
    with pytest.raises(BadRecording):
        svc.save("empty")                                  # nothing was recorded
    record_something(svc, hook)
    svc.end_recording()
    with pytest.raises(BadName):
        svc.save("../evil")
    svc.save("run one")
    assert [r.name for r in store.list()] == ["run one"]


def test_saving_never_overwrites_without_asking(parts):
    svc, hook, store, *_ = parts
    record_something(svc, hook)
    svc.end_recording()
    svc.save("a")
    with pytest.raises(BadRecording):
        svc.save("a")
    svc.save("a", overwrite=True)


def test_the_finished_recording_can_be_saved_once_then_is_cleared(parts):
    svc, hook, *_ = parts
    record_something(svc, hook)
    svc.end_recording()
    assert svc.has_unsaved
    svc.save("x")
    assert not svc.has_unsaved
    svc.discard()


# ------------------------------------------------------------------ playing
def saved(parts, name="demo"):
    svc, hook, *_ = parts
    record_something(svc, hook)
    svc.end_recording()
    svc.save(name)


def test_play_takes_the_lock_runs_and_gives_it_back(parts):
    svc, _, _, lock, player, _ = parts
    saved(parts)
    seen = []
    player.play = lambda *a, **k: (seen.append(lock.owner), Result(True, "Done.", 3))[1]
    out = svc.play("demo", 99)
    assert out.ok and seen == ["recorder"] and lock.owner is None and not svc.playing


def test_play_is_refused_while_another_runner_owns_input(parts):
    svc, _, _, lock, player, _ = parts
    saved(parts)
    lock.acquire("goliath")
    out = svc.play("demo", 99)
    assert not out.ok and "running" in out.reason.lower() and player.calls == []


def test_play_is_refused_while_recording(parts):
    svc, hook, _, _, player, _ = parts
    saved(parts)
    svc.begin_recording()
    assert not svc.play("demo", 99).ok and player.calls == []


def test_play_of_a_missing_recording_says_so(parts):
    svc, *_ = parts
    out = svc.play("nothing", 99)
    assert not out.ok and "no recording" in out.reason.lower()


def test_loops_repeat_the_run(parts):
    svc, _, _, _, player, _ = parts
    saved(parts)
    out = svc.play("demo", 99, loops=3, speed=1.5)
    assert out.ok and len(player.calls) == 3 and out.loops_done == 3
    assert all(c[2] == 1.5 for c in player.calls)


def test_a_failed_pass_ends_the_loop_with_its_reason(parts):
    svc, _, _, _, player, _ = parts
    saved(parts)
    player.results = [Result(True, "Done.", 3), Result(False, "Step 2: nope.", 1)]
    out = svc.play("demo", 99, loops=5)
    assert not out.ok and out.reason == "Step 2: nope." and out.loops_done == 1


def test_loops_zero_runs_until_stopped(parts):
    svc, _, _, lock, player, _ = parts
    saved(parts)
    count = {"n": 0}

    def play(rec, hwnd, load, **k):
        count["n"] += 1
        if count["n"] == 4:
            lock.stop_event.set()
            return Result(False, "Stopped.", 1, stopped=True)
        return Result(True, "Done.", 3)
    player.play = play
    out = svc.play("demo", 99, loops=0)
    assert count["n"] == 4 and out.stopped


def test_the_lock_is_released_even_if_the_player_crashes(parts):
    svc, _, _, lock, player, _ = parts
    saved(parts)

    def boom(*a, **k):
        raise RuntimeError("crash")
    player.play = boom
    out = svc.play("demo", 99)
    assert not out.ok and lock.owner is None and not svc.playing


def test_a_bad_speed_or_loop_count_is_refused_without_taking_the_lock(parts):
    svc, _, _, lock, player, _ = parts
    saved(parts)
    for kwargs in ({"loops": -1}, {"loops": 1000}, {"speed": 0}, {"speed": 9}):
        assert not svc.play("demo", 99, **kwargs).ok
    assert player.calls == [] and lock.owner is None


def test_two_plays_at_once_are_refused(parts):
    svc, _, _, _, player, _ = parts
    saved(parts)
    entered, release = threading.Event(), threading.Event()

    def slow(*a, **k):
        entered.set()
        release.wait(3)
        return Result(True, "Done.", 3)
    player.play = slow
    first = threading.Thread(target=svc.play, args=("demo", 99))
    first.start()
    assert entered.wait(3)
    assert not svc.play("demo", 99).ok
    release.set()
    first.join(3)


def test_the_verify_choice_reaches_the_player(parts):
    svc, _, _, _, player, _ = parts
    saved(parts)
    svc.play("demo", 99, verify=False)
    assert player.verify_seen is False
    svc.play("demo", 99)
    assert player.verify_seen is True


def test_recording_without_pictures_is_passed_to_the_session(parts):
    svc, hook, *_ = parts
    assert svc.begin_recording(pictures=False)
    hook.events = [ev("down", 1.0, x=10, y=10, button="left"), ev("up", 1.1, x=10, y=10, button="left")]
    svc.tick()
    assert svc.session.steps[0].picture is None and svc.session.no_picture == 0
