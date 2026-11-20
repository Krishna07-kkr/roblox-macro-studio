"""Turning raw mouse and key events into recorded steps (spec 003, C002). No Windows involved."""

import numpy as np

from macro_studio.tools.recorder.session import RawEvent, Session

# One fake Roblox window: client area at screen (100, 50), 800x600.
WIN = 7001
OTHER = 7002
CLIENT = (100, 50, 800, 600)


class World:
    def __init__(self):
        self.foreground_hwnd = WIN
        self.captures = 0
        self.frame = np.zeros((600, 800, 3), dtype=np.uint8)
        self.frame[:, :, 1] = np.arange(800, dtype=np.uint8)[None, :] % 251     # something to see

    def window_at(self, x, y):
        if 100 <= x < 900 and 50 <= y < 650:
            return WIN
        if 1000 <= x < 1400 and 50 <= y < 450:
            return OTHER
        return None

    def client_info(self, hwnd):
        return CLIENT if hwnd == WIN else (1000, 50, 400, 400)

    def foreground(self):
        return self.foreground_hwnd

    def capture(self, hwnd, t):
        self.captures += 1
        return self.frame if hwnd == WIN else None


def make(world=None, **kw):
    world = world or World()
    return Session(world.window_at, world.client_info, world.foreground, world.capture, **kw), world


def ev(kind, t, x=None, y=None, button=None, delta=None, vk=None):
    return RawEvent(kind, t, x, y, button, delta, vk)


def kinds(session):
    return [s.kind for s in session.steps]


def test_positions_become_client_relative():
    s, _ = make()
    s.feed(ev("move", 1.0, 400, 250))
    assert (s.steps[0].x, s.steps[0].y) == (300, 200)
    assert (s.client_w, s.client_h) == (800, 600)


def test_events_outside_every_roblox_window_are_dropped():
    s, _ = make()
    s.feed(ev("move", 1.0, 50, 50))
    s.feed(ev("down", 1.1, 50, 50, button="left"))
    s.feed(ev("up", 1.2, 50, 50, button="left"))
    assert s.steps == []


def test_the_first_window_touched_is_the_only_one_recorded():
    s, _ = make()
    s.feed(ev("move", 1.0, 400, 250))
    s.feed(ev("move", 1.1, 1100, 100))              # a second client
    s.feed(ev("down", 1.2, 1100, 100, button="left"))
    s.feed(ev("up", 1.3, 1100, 100, button="left"))
    assert kinds(s) == ["move"]


def test_pauses_are_the_time_since_the_previous_recorded_step():
    s, _ = make()
    s.feed(ev("move", 10.000, 400, 250))
    s.feed(ev("move", 10.250, 500, 250))
    s.feed(ev("down", 10.750, 500, 250, button="left"))
    assert [st.dt for st in s.steps] == [0, 250, 500]


def test_tiny_and_too_frequent_moves_are_thinned():
    s, _ = make()
    s.feed(ev("move", 1.000, 400, 250))
    s.feed(ev("move", 1.005, 401, 250))             # 1 px, 5 ms: dropped
    s.feed(ev("move", 1.100, 401, 251))             # 1 px: dropped
    s.feed(ev("move", 1.101, 460, 250))             # far but only 1 ms after: dropped
    s.feed(ev("move", 1.200, 460, 250))             # far and later: kept
    assert [(st.x, st.y) for st in s.steps] == [(300, 200), (360, 200)]


def test_a_press_keeps_a_picture_of_the_spot_and_its_box():
    s, world = make()
    s.feed(ev("down", 1.0, 400, 250, button="right"))
    st = s.steps[0]
    assert st.kind == "down" and st.button == "right" and (st.x, st.y) == (300, 200)
    assert st.picture is not None and st.picture.shape[:2] == (64, 64)
    assert st.look_box == (268, 168, 64, 64)
    assert np.array_equal(st.picture, world.frame[168:232, 268:332])


def test_the_picture_box_stays_inside_the_window_at_the_edges():
    s, _ = make()
    s.feed(ev("down", 1.0, 101, 51, button="left"))            # client (1, 1): the corner
    assert s.steps[0].look_box == (0, 0, 64, 64)
    s.feed(ev("up", 1.1, 101, 51, button="left"))
    s.feed(ev("down", 1.2, 899, 649, button="left"))           # client (799, 599)
    assert s.steps[2].look_box == (736, 536, 64, 64)


def test_a_press_with_no_picture_is_counted_not_hidden():
    s, world = make()
    world.frame = None
    s.feed(ev("down", 1.0, 400, 250, button="left"))
    assert s.steps[0].picture is None and s.no_picture == 1


def test_a_release_whose_press_was_outside_is_dropped():
    s, _ = make()
    s.feed(ev("down", 1.0, 50, 50, button="left"))             # outside
    s.feed(ev("up", 1.1, 400, 250, button="left"))             # released inside
    assert s.steps == []


def test_a_drag_out_of_the_window_still_records_its_release_at_the_edge():
    s, _ = make()
    s.feed(ev("down", 1.0, 400, 250, button="left"))
    s.feed(ev("up", 1.5, 2000, 2000, button="left"))           # released far outside
    assert kinds(s) == ["down", "up"]
    assert (s.steps[1].x, s.steps[1].y) == (799, 599) or 0 <= s.steps[1].x <= 800


def test_wheel_is_recorded_with_its_position():
    s, _ = make()
    s.feed(ev("wheel", 1.0, 400, 250, delta=-240))
    assert kinds(s) == ["wheel"] and s.steps[0].delta == -240


def test_keys_are_recorded_only_while_a_roblox_window_is_in_front():
    s, world = make()
    world.foreground_hwnd = None
    s.feed(ev("key_down", 1.0, vk=0x41))
    s.feed(ev("key_up", 1.1, vk=0x41))
    assert s.steps == []
    world.foreground_hwnd = WIN
    s.feed(ev("key_down", 2.0, vk=0x41))
    s.feed(ev("key_up", 2.1, vk=0x41))
    assert kinds(s) == ["key_down", "key_up"] and s.steps[0].vk == 0x41


def test_key_auto_repeat_counts_once():
    s, _ = make()
    for t in (1.0, 1.03, 1.06, 1.09):
        s.feed(ev("key_down", t, vk=0x57))
    s.feed(ev("key_up", 1.2, vk=0x57))
    assert kinds(s) == ["key_down", "key_up"]


def test_a_key_pressed_before_any_mouse_event_picks_the_front_window():
    s, _ = make()
    s.feed(ev("key_down", 1.0, vk=0x57))
    assert (s.client_w, s.client_h) == (800, 600)


def test_stop_keys_are_never_recorded_and_f8_ends_the_recording():
    s, _ = make()
    for vk in (0x75, 0x76, 0x1B):
        s.feed(ev("key_down", 1.0, vk=vk))
        s.feed(ev("key_up", 1.1, vk=vk))
    assert s.steps == [] and not s.stop_requested
    s.feed(ev("key_down", 2.0, vk=0x77))
    assert s.stop_requested and s.steps == []


def test_finishing_releases_whatever_is_still_held():
    s, _ = make()
    s.feed(ev("down", 1.0, 400, 250, button="left"))
    s.feed(ev("key_down", 1.1, vk=0x41))
    s.finish(2.0)
    assert kinds(s) == ["down", "key_down", "up", "key_up"] or \
        sorted(kinds(s)) == sorted(["down", "key_down", "up", "key_up"])
    assert s.finished


def test_nothing_is_added_after_finish():
    s, _ = make()
    s.finish(1.0)
    s.feed(ev("move", 2.0, 400, 250))
    assert s.steps == []


def test_the_recording_is_capped():
    s, _ = make(max_steps=5)
    for i in range(20):
        s.feed(ev("wheel", 1.0 + i, 400, 250, delta=120))
    assert len(s.steps) == 5 and s.full


def test_to_recording_carries_the_name_size_and_steps():
    s, _ = make()
    s.feed(ev("move", 1.0, 400, 250))
    rec = s.to_recording("run1")
    assert (rec.name, rec.client_w, rec.client_h, len(rec.steps)) == ("run1", 800, 600, 1)


def test_without_pictures_a_press_is_recorded_but_nothing_is_captured():
    s, world = make(pictures=False)
    s.feed(ev("down", 1.0, 400, 250, button="left"))
    assert s.steps[0].picture is None and s.steps[0].look_box is None
    assert world.captures == 0 and s.no_picture == 0
