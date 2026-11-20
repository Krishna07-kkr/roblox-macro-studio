"""Scheduler: one worker for every periodic job (Constitution II)."""

import threading
import time

from macro_studio.services.scheduler import Scheduler


def test_job_runs_on_its_interval(fake_clock):
    calls = []
    s = Scheduler(clock=fake_clock)
    s.add("tick", lambda: calls.append(fake_clock()), interval_s=3)
    s.run_pending()
    assert calls == []
    fake_clock.advance(3)
    s.run_pending()
    fake_clock.advance(2.9)
    s.run_pending()
    fake_clock.advance(0.1)
    s.run_pending()
    assert len(calls) == 2


def test_run_now_runs_immediately(fake_clock):
    calls = []
    s = Scheduler(clock=fake_clock)
    s.add("tick", lambda: calls.append(1), interval_s=60, run_now=True)
    s.run_pending()
    assert calls == [1]


def test_jitter_stays_within_bounds(fake_clock):
    s = Scheduler(clock=fake_clock, rng=lambda a, b: b)  # always the maximum jitter
    s.add("afk", lambda: None, interval_s=60, jitter_s=5)
    assert s.next_run("afk") == fake_clock() + 65
    s = Scheduler(clock=fake_clock, rng=lambda a, b: a)  # always the minimum
    s.add("afk", lambda: None, interval_s=60, jitter_s=5)
    assert s.next_run("afk") == fake_clock() + 55


def test_adding_same_name_replaces_job(fake_clock):
    calls = []
    s = Scheduler(clock=fake_clock)
    s.add("job", lambda: calls.append("old"), interval_s=1)
    s.add("job", lambda: calls.append("new"), interval_s=1)
    fake_clock.advance(1)
    s.run_pending()
    assert calls == ["new"]


def test_remove_stops_job(fake_clock):
    calls = []
    s = Scheduler(clock=fake_clock)
    s.add("job", lambda: calls.append(1), interval_s=1)
    s.remove("job")
    fake_clock.advance(5)
    s.run_pending()
    assert calls == []


def test_failing_job_is_logged_and_kept(fake_clock):
    errors, calls = [], []

    def boom():
        calls.append(1)
        raise RuntimeError("nope")

    s = Scheduler(clock=fake_clock, on_error=lambda name, exc: errors.append((name, str(exc))))
    s.add("boom", boom, interval_s=1)
    for _ in range(2):
        fake_clock.advance(1)
        s.run_pending()
    assert len(calls) == 2
    assert errors == [("boom", "nope"), ("boom", "nope")]


def test_background_thread_runs_jobs_and_stops_quickly():
    ran = threading.Event()
    s = Scheduler()
    s.add("fast", ran.set, interval_s=0.01)
    s.start()
    try:
        assert ran.wait(2), "job never ran on the worker thread"
    finally:
        t0 = time.monotonic()
        s.stop(timeout=1.0)
        assert time.monotonic() - t0 < 1.0
    assert not s.running


# ------------------------------------------------------------------ job budget and isolation (A2, H5)
def test_a_slow_job_is_reported_once_per_minute(fake_clock):
    slow = []
    s = Scheduler(clock=fake_clock, on_slow=lambda name, seconds: slow.append((name, round(seconds, 1))),
                  slow_after_s=1.0)

    def sluggish():
        fake_clock.advance(2.5)
    s.add("feed", sluggish, interval_s=1, run_now=True)
    s.run_pending()
    assert slow == [("feed", 2.5)]
    fake_clock.advance(3)
    s.run_pending()
    assert len(slow) == 1                      # still inside the minute: no repeat
    fake_clock.advance(61)
    s.run_pending()
    assert len(slow) == 2


def test_a_fast_job_is_never_reported(fake_clock):
    slow = []
    s = Scheduler(clock=fake_clock, on_slow=lambda n, t: slow.append(n), slow_after_s=1.0)
    s.add("quick", lambda: fake_clock.advance(0.2), interval_s=1, run_now=True)
    s.run_pending()
    assert slow == []


def _wait_until(condition, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.005)
    return False


def test_a_hung_isolated_job_does_not_hold_up_other_jobs():
    release, started, fast_runs = threading.Event(), [], []
    s = Scheduler()
    s.add("hung", lambda: (started.append(1), release.wait(5)), interval_s=0.01, run_now=True,
          isolated=True)
    s.add("fast", lambda: fast_runs.append(1), interval_s=0.01, run_now=True)
    try:
        for _ in range(40):
            s.run_pending()
            time.sleep(0.01)
        assert len(fast_runs) >= 5             # kept running while "hung" was stuck
        assert started == [1]                  # and the stuck job was not started a second time
    finally:
        release.set()
    assert _wait_until(lambda: s.running_jobs() == 0)


def test_an_isolated_job_is_rescheduled_after_it_finishes(fake_clock):
    runs = []
    s = Scheduler(clock=fake_clock)
    s.add("feed", lambda: runs.append(1), interval_s=1, run_now=True, isolated=True)
    s.run_pending()
    assert _wait_until(lambda: runs == [1] and s.running_jobs() == 0)
    s.run_pending()
    assert runs == [1]                         # not due yet
    fake_clock.advance(1)
    s.run_pending()
    assert _wait_until(lambda: len(runs) == 2)


def test_an_isolated_job_that_raises_is_reported_and_kept(fake_clock):
    errors = []
    s = Scheduler(clock=fake_clock, on_error=lambda name, exc: errors.append((name, str(exc))))

    def broken():
        raise RuntimeError("boom")
    s.add("feed", broken, interval_s=1, run_now=True, isolated=True)
    s.run_pending()
    assert _wait_until(lambda: errors == [("feed", "boom")])
    assert _wait_until(lambda: s.running_jobs() == 0)
    assert s.next_run("feed") is not None


def test_a_removed_isolated_job_is_not_rescheduled():
    release = threading.Event()
    s = Scheduler()
    s.add("feed", lambda: release.wait(5), interval_s=0.01, run_now=True, isolated=True)
    s.run_pending()
    s.remove("feed")
    release.set()
    assert _wait_until(lambda: s.running_jobs() == 0)
    assert s.next_run("feed") is None


def test_the_worker_loop_does_not_spin_while_an_isolated_job_runs():
    release = threading.Event()
    s = Scheduler(idle_wait_s=0.05)
    s.add("feed", lambda: release.wait(5), interval_s=0.01, run_now=True, isolated=True)
    try:
        s.run_pending()
        assert s._seconds_until_next() >= 0.04     # waits instead of returning 0 forever
    finally:
        release.set()


def test_stop_returns_even_if_an_isolated_job_is_stuck():
    release = threading.Event()
    s = Scheduler()
    s.add("feed", lambda: release.wait(5), interval_s=0.01, run_now=True, isolated=True)
    s.start()
    try:
        assert _wait_until(lambda: s.running_jobs() == 1)
        began = time.monotonic()
        s.stop(timeout=1.0)
        assert time.monotonic() - began < 1.5
    finally:
        release.set()


# ------------------------------------------------------------------ one worker per isolated job
# Every new thread that calls into Tkinter (ui_call) leaves one Windows handle behind for good
# (measured: 300 short threads = +301 handles; found by scripts/soak.py). So an isolated job
# gets one long-lived worker thread, reused for every run.
def test_every_run_of_an_isolated_job_uses_the_same_thread(fake_clock):
    seen = []
    s = Scheduler(clock=fake_clock)
    s.add("feed", lambda: seen.append(threading.get_ident()), interval_s=1, run_now=True,
          isolated=True)
    for _ in range(12):
        s.run_pending()
        assert _wait_until(lambda: s.running_jobs() == 0)
        fake_clock.advance(1)
    assert len(seen) >= 10 and len(set(seen)) == 1
    s.stop()


def test_threads_do_not_pile_up_while_isolated_jobs_run(fake_clock):
    s = Scheduler(clock=fake_clock)
    s.add("feed", lambda: None, interval_s=1, run_now=True, isolated=True)
    s.run_pending()
    assert _wait_until(lambda: s.running_jobs() == 0)
    baseline = threading.active_count()
    for _ in range(30):
        fake_clock.advance(1)
        s.run_pending()
        assert _wait_until(lambda: s.running_jobs() == 0)
    assert threading.active_count() == baseline
    s.stop()


def test_a_job_removed_and_added_again_keeps_its_thread(fake_clock):
    # The Tabs picker adds its job every time it opens and removes it when it closes.
    seen = []
    s = Scheduler(clock=fake_clock)
    for _ in range(5):
        s.add("tab_picker", lambda: seen.append(threading.get_ident()), interval_s=1,
              run_now=True, isolated=True)
        s.run_pending()
        assert _wait_until(lambda: s.running_jobs() == 0)
        s.remove("tab_picker")
    assert len(seen) == 5 and len(set(seen)) == 1
    s.stop()


def test_stop_ends_the_idle_workers():
    s = Scheduler()
    s.add("feed", lambda: None, interval_s=0.01, run_now=True, isolated=True)
    s.start()
    assert _wait_until(lambda: "feed" in s._workers and s._workers["feed"].thread.is_alive())
    worker_thread = s._workers["feed"].thread
    s.stop()
    worker_thread.join(timeout=2)
    assert not worker_thread.is_alive()


# ------------------------------------------------------------------ one-shot jobs
def test_a_once_job_runs_one_time_and_is_then_gone(fake_clock):
    runs = []
    s = Scheduler(clock=fake_clock)
    s.add("trim_now", lambda: runs.append(1), interval_s=3600, run_now=True, once=True)
    s.run_pending()
    assert runs == [1]
    assert s.next_run("trim_now") is None
    fake_clock.advance(7200)
    s.run_pending()
    assert runs == [1]


def test_a_once_job_that_fails_is_reported_and_still_removed(fake_clock):
    errors = []
    s = Scheduler(clock=fake_clock, on_error=lambda name, exc: errors.append(name))

    def broken():
        raise RuntimeError("x")
    s.add("one", broken, interval_s=1, run_now=True, once=True)
    s.run_pending()
    assert errors == ["one"] and s.next_run("one") is None


def test_an_isolated_once_job_runs_on_its_worker_and_is_removed(fake_clock):
    runs = []
    s = Scheduler(clock=fake_clock)
    s.add("one", lambda: runs.append(1), interval_s=1, run_now=True, isolated=True, once=True)
    s.run_pending()
    assert _wait_until(lambda: runs == [1] and s.running_jobs() == 0)
    assert s.next_run("one") is None
    s.stop()
