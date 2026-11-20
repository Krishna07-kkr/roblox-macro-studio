"""Shared live vision feed (Constitution VI, FR-005)."""

import numpy as np

from macro_studio.core.live_feed import LiveFeed, Overlay

RED = (255, 0, 0)


class FakeScheduler:
    def __init__(self):
        self.jobs = {}
        self.isolated = {}

    def add(self, name, fn, interval_s, jitter_s=0.0, run_now=False, isolated=False):
        self.jobs[name] = (fn, interval_s)
        self.isolated[name] = isolated

    def remove(self, name):
        self.jobs.pop(name, None)


def black(w, h):
    return np.zeros((h, w, 3), dtype=np.uint8)


def make(window=7, frame=None, overlay=None, max_size=(2000, 2000)):
    calls = {"capture": 0, "ui": []}

    def capture(hwnd):
        calls["capture"] += 1
        return frame

    feed = LiveFeed(capture=capture, pick_window=lambda: window,
                    overlay=lambda: overlay or Overlay(), scheduler=FakeScheduler(),
                    ui_call=lambda fn: calls["ui"].append(fn),
                    on_frame=lambda hwnd, image: calls.setdefault("shown", []).append(
                        (hwnd, image)),
                    max_size=max_size)
    return feed, calls


def test_no_window_or_failed_capture_gives_no_frame():
    assert make(window=0, frame=black(800, 600))[0].render() is None
    assert make(window=7, frame=None)[0].render() is None


def test_regions_are_scaled_to_the_real_client_size():
    overlay = Overlay(reference=(800, 600), regions=[(100, 100, 50, 50, RED, "Slot 1")])
    feed, _ = make(frame=black(1600, 1200), overlay=overlay)
    hwnd, image = feed.render()
    assert hwnd == 7 and image.size == (1600, 1200)
    assert image.getpixel((200, 200)) == RED          # top-left corner, scaled x2
    assert image.getpixel((299, 260)) == RED          # right edge at x = (100+50)*2 - 1
    assert image.getpixel((250, 250)) == (0, 0, 0)    # inside is not filled


def test_points_are_drawn_scaled():
    overlay = Overlay(reference=(800, 600), points=[(400, 300, RED, "Claim")])
    _, image = make(frame=black(800, 600), overlay=overlay)[0].render()
    assert image.getpixel((400, 300)) == RED


def test_large_frames_shrink_to_fit_keeping_aspect():
    feed, _ = make(frame=black(1600, 1200), max_size=(640, 640))
    _, image = feed.render()
    assert image.size == (640, 480)


def test_bgr_frame_is_shown_as_rgb():
    frame = black(100, 100)
    frame[:, :] = (255, 0, 0)          # pure blue in OpenCV's BGR order
    _, image = make(frame=frame)[0].render()
    assert image.getpixel((50, 50)) == (0, 0, 255)


def test_runs_on_the_shared_scheduler_at_least_twice_a_second():
    feed, _ = make(frame=black(800, 600))
    feed.start()
    (fn, interval), = feed._scheduler.jobs.values()
    assert interval <= 0.5
    feed.stop()
    assert feed._scheduler.jobs == {}


def test_the_feed_runs_isolated_so_a_frozen_client_cannot_hold_up_other_jobs():
    feed, _ = make(frame=black(800, 600))
    feed.start()
    assert feed._scheduler.isolated == {feed.name: True}


def test_tick_hands_the_frame_to_the_ui_thread_only_when_visible():
    feed, calls = make(frame=black(800, 600))
    feed.set_visible(False)
    feed.tick()
    assert calls["capture"] == 0 and calls["ui"] == []
    feed.set_visible(True)
    feed.tick()
    assert calls["capture"] == 1 and len(calls["ui"]) == 1
    calls["ui"][0]()                    # what the Tk thread would run
    hwnd, image = calls["shown"][0]
    assert hwnd == 7 and image.size == (800, 600)


def test_empty_feed_message_explains_minimized_clients():
    from macro_studio.core.live_feed import empty_feed_message
    assert "No Roblox client" in empty_feed_message(0)
    msg = empty_feed_message(2)
    assert "2" in msg and "minimized" in msg.lower() and "restore" in msg.lower()


def test_tick_reports_no_window_to_the_ui():
    feed, calls = make(window=0, frame=None)
    feed.tick()
    calls["ui"][0]()
    assert calls["shown"] == [(0, None)]
