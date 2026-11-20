"""Recorder glue that can be checked without a Roblox client (spec 003, C006)."""

import numpy as np

from macro_studio.tools.recorder.system import FrameRing, client_info


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make(frames_for=None):
    clock, shots = Clock(), []

    def capture(hwnd):
        shots.append(hwnd)
        return np.full((4, 4, 3), len(shots), dtype=np.uint8) if hwnd != 0 else None
    return FrameRing(capture, keep=3, clock=clock), clock, shots


def test_the_frame_before_a_click_is_not_the_one_after_it():
    ring, clock, _ = make()
    for t in (1.0, 1.1, 1.2):
        clock.now = t
        ring.grab(5)
    frame = ring.before(5, 1.21)                     # a click at 1.21: the 1.2 frame is too close
    assert int(frame[0, 0, 0]) == 2                  # the 1.1 frame (second grab)


def test_when_every_frame_is_too_new_the_oldest_is_used():
    ring, clock, _ = make()
    clock.now = 1.0
    ring.grab(5)
    assert ring.before(5, 1.001) is not None


def test_only_the_newest_few_frames_are_kept():
    ring, clock, _ = make()
    for t in range(1, 8):
        clock.now = float(t)
        ring.grab(5)
    assert len(ring._frames[5]) == 3


def test_frames_of_other_windows_are_dropped_when_the_window_changes():
    ring, clock, _ = make()
    clock.now = 1.0
    ring.grab(5)
    ring.grab(6)
    assert list(ring._frames) == [6]


def test_a_failed_capture_adds_nothing():
    ring, _, _ = make()
    assert ring.grab(0) is False and ring.before(0, 1.0) is None


def test_client_info_of_a_window_that_does_not_exist_is_none():
    assert client_info(0x7FFFFFF) is None
