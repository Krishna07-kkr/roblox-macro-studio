"""Replaying a recording: timing, scaling, picture check, popup guard, stop (spec 003, C003)."""

import numpy as np
import pytest

from macro_studio.tools.recorder.model import Recording, Step
from macro_studio.tools.recorder.player import Player, find_picture

HWND = 5150
RNG = np.random.default_rng(7)
FRAME = RNG.integers(0, 255, (600, 800, 3), dtype=np.uint8)     # a busy, unique-looking screen


class FakeIO:
    def __init__(self, left=100, top=50, w=800, h=600):
        self.rect = (left, top, w, h)
        self.calls, self.focused, self.alive_flag = [], True, True
        self.held_buttons, self.held_keys = set(), set()
        self.fail_on = None

    def client_info(self, hwnd):
        return self.rect if self.alive_flag else None

    def focus(self, hwnd):
        self.calls.append(("focus",))
        return self.focused

    def is_foreground(self, hwnd):
        return self.focused

    def move(self, x, y):
        self._maybe_fail("move")
        self.calls.append(("move", x, y))

    def button(self, name, down):
        self._maybe_fail("button")
        self.calls.append(("down" if down else "up", name))
        (self.held_buttons.add if down else self.held_buttons.discard)(name)

    def wheel(self, delta):
        self.calls.append(("wheel", delta))

    def key(self, vk, down):
        self.calls.append(("key_down" if down else "key_up", vk))
        (self.held_keys.add if down else self.held_keys.discard)(vk)

    def _maybe_fail(self, what):
        if self.fail_on == what:
            raise OSError("input refused")


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []
        self.stop_after = None       # stop once this many waits happened

    def wait(self, seconds):
        self.waits.append(round(seconds, 4))
        self.now += seconds
        return not self.stopped()

    def stopped(self):
        return self.stop_after is not None and len(self.waits) >= self.stop_after


class Guard:
    def __init__(self, clear=True):
        self.clear, self.asked = clear, 0

    def ensure_clear(self, hwnd):
        self.asked += 1
        return self.clear


def make(io=None, frame=FRAME, guard=None, clock=None):
    io, clock, logs = io or FakeIO(), clock or Clock(), []
    player = Player(io, capture=lambda hwnd: frame, guard=guard, wait=clock.wait,
                    stop=clock.stopped, log=logs.append, clock=lambda: clock.now)
    return player, io, clock, logs


def picture_step(x, y, shift=(0, 0), button="left"):
    box = (x - 32, y - 32, 64, 64)
    dx, dy = shift
    pic = FRAME[box[1] - dy:box[1] - dy + 64, box[0] - dx:box[0] - dx + 64].copy() if shift != (0, 0) \
        else FRAME[box[1]:box[1] + 64, box[0]:box[0] + 64].copy()
    return pic, box


def rec(*steps, w=800, h=600):
    return Recording("t", w, h, list(steps))


def moves(io):
    return [c for c in io.calls if c[0] == "move"]


# ------------------------------------------------------------------ find_picture
def test_find_picture_at_the_same_place():
    pic, box = picture_step(300, 200)
    assert find_picture(FRAME, pic, box, (1.0, 1.0))[:2] == (0, 0)


def test_find_picture_finds_a_small_shift():
    shifted = np.roll(FRAME, (7, 12), axis=(0, 1))                  # content moved down 7, right 12
    pic, box = picture_step(300, 200)
    assert find_picture(shifted, pic, box, (1.0, 1.0))[:2] == (12, 7)


def test_find_picture_ignores_a_shift_over_the_limit():
    shifted = np.roll(FRAME, (0, 60), axis=(0, 1))
    pic, box = picture_step(300, 200)
    assert find_picture(shifted, pic, box, (1.0, 1.0)) is None


def test_find_picture_rejects_a_different_screen():
    other = RNG.integers(0, 255, FRAME.shape, dtype=np.uint8)
    pic, box = picture_step(300, 200)
    assert find_picture(other, pic, box, (1.0, 1.0)) is None


def test_find_picture_flat_spot_matches_only_a_flat_spot():
    flat = np.full((600, 800, 3), 40, dtype=np.uint8)
    pic = flat[168:232, 268:332].copy()
    assert find_picture(flat, pic, (268, 168, 64, 64), (1.0, 1.0)) is not None
    assert find_picture(FRAME, pic, (268, 168, 64, 64), (1.0, 1.0)) is None


def test_find_picture_works_when_the_window_is_scaled():
    pic, box = picture_step(300, 200)
    import cv2
    big = cv2.resize(FRAME, (1600, 1200), interpolation=cv2.INTER_LINEAR)
    found = find_picture(big, pic, box, (2.0, 2.0))
    assert found is not None and abs(found[0]) <= 2 and abs(found[1]) <= 2


# ------------------------------------------------------------------ playing
def test_steps_are_sent_in_order_at_client_position_plus_window_origin():
    player, io, _, _ = make()
    r = player.play(rec(Step("move", 0, x=300, y=200), Step("key_down", 10, vk=0x41),
                        Step("key_up", 10, vk=0x41)), HWND, lambda f: None)
    assert r.ok and r.steps_done == 3
    assert moves(io) == [("move", 400, 250)]                          # 100 + 300, 50 + 200
    assert [c[0] for c in io.calls if c[0].startswith("key")] == ["key_down", "key_up"]


def test_pauses_follow_the_recording_and_the_speed():
    player, _, clock, _ = make()
    steps = [Step("move", 0, x=10, y=10), Step("move", 400, x=20, y=20), Step("move", 200, x=30, y=30)]
    player.play(rec(*steps), HWND, lambda f: None, speed=2.0)
    assert [w for w in clock.waits if w > 0] == [0.2, 0.1]


def test_positions_scale_with_the_window():
    io = FakeIO(left=0, top=0, w=1600, h=1200)
    player, io, _, _ = make(io=io)
    player.play(rec(Step("move", 0, x=300, y=200)), HWND, lambda f: None)
    assert moves(io) == [("move", 600, 400)]


def test_a_window_of_a_different_shape_is_refused_before_any_input():
    io = FakeIO(w=1000, h=500)
    player, io, _, _ = make(io=io)
    r = player.play(rec(Step("move", 0, x=1, y=1)), HWND, lambda f: None)
    assert not r.ok and "size" in r.reason.lower() and io.calls == []


def test_a_tiny_size_difference_is_not_a_problem():
    io = FakeIO(w=799, h=599)
    player, io, _, _ = make(io=io)
    assert player.play(rec(Step("move", 0, x=1, y=1)), HWND, lambda f: None).ok


def test_a_window_that_cannot_be_brought_to_the_front_stops_everything():
    io = FakeIO()
    io.focused = False
    player, io, _, _ = make(io=io)
    r = player.play(rec(Step("key_down", 0, vk=0x41), Step("key_up", 0, vk=0x41)), HWND,
                    lambda f: None)
    assert not r.ok and "front" in r.reason.lower()
    assert not [c for c in io.calls if c[0].startswith(("key", "down", "up", "move"))]


def test_a_press_with_a_matching_picture_clicks_and_releases():
    pic, box = picture_step(300, 200)
    player, io, _, _ = make()
    steps = [Step("down", 0, x=300, y=200, button="left", look={"file": "a.png", "box": list(box)}),
             Step("up", 50, x=300, y=200, button="left")]
    r = player.play(rec(*steps), HWND, lambda f: pic)
    assert r.ok
    assert [c[0] for c in io.calls if c[0] in ("down", "up")] == ["down", "up"]
    assert moves(io)[0] == ("move", 400, 250)


def test_a_small_shift_moves_the_click_with_it_until_the_release():
    shifted = np.roll(FRAME, (7, 12), axis=(0, 1))
    pic, box = picture_step(300, 200)
    player, io, _, _ = make(frame=shifted)
    steps = [Step("down", 0, x=300, y=200, button="left", look={"file": "a", "box": list(box)}),
             Step("move", 30, x=340, y=200),
             Step("up", 30, x=340, y=200, button="left"),
             Step("move", 30, x=500, y=300)]
    player.play(rec(*steps), HWND, lambda f: pic)
    assert [c[1:] for c in moves(io)] == [(412, 257), (452, 257), (452, 257), (600, 350)]


def test_a_press_whose_spot_never_looks_right_is_not_clicked():
    pic, box = picture_step(300, 200)
    other = RNG.integers(0, 255, FRAME.shape, dtype=np.uint8)
    player, io, clock, _ = make(frame=other)
    steps = [Step("down", 0, x=300, y=200, button="left", look={"file": "a", "box": list(box)}),
             Step("up", 50, x=300, y=200, button="left")]
    r = player.play(rec(*steps), HWND, lambda f: pic, step_timeout=2.0)
    assert not r.ok and "step 1" in r.reason.lower()
    assert not [c for c in io.calls if c[0] in ("down", "up")]
    assert clock.now >= 2.0 and len(clock.waits) >= 4, "it waits and retries before giving up"


def test_a_missing_picture_file_stops_instead_of_clicking_blind():
    player, io, _, _ = make()
    steps = [Step("down", 0, x=300, y=200, button="left", look={"file": "gone.png", "box": [268, 168, 64, 64]})]
    r = player.play(rec(*steps), HWND, lambda f: None)
    assert not r.ok and not [c for c in io.calls if c[0] == "down"]


def test_a_press_recorded_without_a_picture_is_clicked_and_counted():
    player, io, _, logs = make()
    r = player.play(rec(Step("down", 0, x=300, y=200, button="left"),
                        Step("up", 20, x=300, y=200, button="left")), HWND, lambda f: None)
    assert r.ok and r.unverified == 1
    assert any("no picture" in line.lower() for line in logs)


def test_the_popup_guard_runs_before_every_press_and_can_block():
    guard = Guard(clear=False)
    player, io, _, _ = make(guard=guard)
    r = player.play(rec(Step("down", 0, x=300, y=200, button="left")), HWND, lambda f: None)
    assert not r.ok and "pop-up" in r.reason.lower() and guard.asked == 1
    assert not [c for c in io.calls if c[0] == "down"]


def test_the_guard_is_asked_for_each_press():
    guard = Guard()
    player, _, _, _ = make(guard=guard)
    steps = [Step("down", 0, x=1, y=1, button="left"), Step("up", 1, x=1, y=1, button="left")] * 3
    player.play(rec(*steps), HWND, lambda f: None)
    assert guard.asked == 3


def test_stop_ends_the_run_and_releases_everything_held():
    clock = Clock()
    clock.stop_after = 3
    player, io, _, _ = make(clock=clock)
    steps = [Step("key_down", 0, vk=0x41), Step("down", 10, x=1, y=1, button="left"),
             Step("move", 10, x=5, y=5), Step("move", 10, x=6, y=6), Step("up", 10, x=6, y=6, button="left")]
    r = player.play(rec(*steps), HWND, lambda f: None)
    assert not r.ok and r.stopped
    assert io.held_buttons == set() and io.held_keys == set()


def test_an_input_error_releases_everything_and_says_so():
    io = FakeIO()
    player, io, _, _ = make(io=io)
    steps = [Step("key_down", 0, vk=0x41), Step("down", 5, x=1, y=1, button="left")]
    io.fail_on = "button"
    r = player.play(rec(*steps), HWND, lambda f: None)
    assert not r.ok and "input" in r.reason.lower()
    assert io.held_keys == set() and io.held_buttons == set()


def test_a_client_that_closes_mid_run_ends_it():
    io = FakeIO()
    player, io, clock, _ = make(io=io)
    original = clock.wait

    def wait_and_close(seconds):
        io.alive_flag = False
        return original(seconds)
    player._wait = wait_and_close
    r = player.play(rec(Step("move", 5, x=1, y=1), Step("move", 5, x=2, y=2)), HWND, lambda f: None)
    assert not r.ok and "closed" in r.reason.lower()


def test_losing_the_front_window_mid_run_stops_before_the_next_key():
    io = FakeIO()
    player, io, _, _ = make(io=io)
    original = player._wait

    def wait_and_lose_focus(seconds):
        io.focused = False
        return original(seconds)
    steps = [Step("key_down", 0, vk=0x41)]
    r1 = player.play(rec(*steps), HWND, lambda f: None)         # focus fine at the start
    assert r1.ok
    io.calls.clear()
    player._wait = wait_and_lose_focus
    r = player.play(rec(Step("move", 5, x=1, y=1), Step("key_down", 5, vk=0x42)), HWND, lambda f: None)
    assert not r.ok and not [c for c in io.calls if c[0] == "key_down"]


def test_progress_is_reported():
    seen = []
    player, _, _, _ = make()
    player.play(rec(Step("move", 0, x=1, y=1), Step("move", 0, x=2, y=2)), HWND, lambda f: None,
                progress=lambda i, n: seen.append((i, n)))
    assert seen[-1] == (2, 2)


def test_an_empty_recording_is_refused():
    player, _, _, _ = make()
    assert not player.play(rec(), HWND, lambda f: None).ok


@pytest.mark.parametrize("speed", [0, -1, 0.1, 10])
def test_speed_outside_the_allowed_range_is_refused(speed):
    player, io, _, _ = make()
    assert not player.play(rec(Step("move", 0, x=1, y=1)), HWND, lambda f: None, speed=speed).ok
    assert io.calls == []


# ------------------------------------------------------------------ exact-position mode
def test_exact_mode_clicks_the_recorded_spot_without_looking_at_the_screen():
    pic, box = picture_step(300, 200)
    other = RNG.integers(0, 255, FRAME.shape, dtype=np.uint8)     # a screen that does not match
    looks, io, clock, logs = [], FakeIO(), Clock(), []
    player = Player(io, capture=lambda hwnd: looks.append(hwnd) or other, guard=None,
                    wait=clock.wait, stop=clock.stopped, log=logs.append, clock=lambda: clock.now)
    steps = [Step("down", 0, x=300, y=200, button="left", look={"file": "a", "box": list(box)}),
             Step("up", 50, x=300, y=200, button="left")]
    r = player.play(rec(*steps), HWND, lambda f: pic, verify=False)
    assert r.ok and r.unverified == 0
    assert looks == [], "no screen capture at all in exact mode"
    assert moves(io)[0] == ("move", 400, 250)                     # window origin + recorded spot
    assert [c[0] for c in io.calls if c[0] in ("down", "up")] == ["down", "up"]
    assert not logs, logs


def test_exact_mode_needs_no_pictures_at_all():
    player, io, _, logs = make()
    r = player.play(rec(Step("down", 0, x=1, y=1, button="left"), Step("up", 5, x=1, y=1, button="left")),
                    HWND, lambda f: None, verify=False)
    assert r.ok and r.unverified == 0 and not logs


def test_exact_mode_still_asks_the_popup_guard():
    guard = Guard(clear=False)
    player, io, _, _ = make(guard=guard)
    r = player.play(rec(Step("down", 0, x=1, y=1, button="left")), HWND, lambda f: None, verify=False)
    assert not r.ok and guard.asked == 1 and not [c for c in io.calls if c[0] == "down"]
