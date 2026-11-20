"""Where the Recorder island sits next to a Roblox window, and how it moves (pure maths)."""

import pytest

from macro_studio.tools.recorder.island import Spring, island_position

SCREEN = (1920, 1080)
SIZE = (190, 40)


def test_above_the_window_and_centred_on_it():
    x, y, where = island_position((400, 300, 800, 600), SIZE, SCREEN, gap=8)
    assert where == "above"
    assert (x, y) == (400 + (800 - 190) // 2, 300 - 40 - 8)


def test_a_window_at_the_top_of_the_screen_gets_the_island_on_its_right():
    x, y, where = island_position((100, 10, 800, 600), SIZE, SCREEN, gap=8)
    assert where == "right" and x == 100 + 800 + 8 and y == 10


def test_no_room_above_or_right_uses_the_left():
    x, y, where = island_position((1300, 0, 600, 500), SIZE, SCREEN, gap=8)
    assert where == "left" and x == 1300 - 190 - 8 and y == 0


def test_no_room_anywhere_sits_inside_the_top_of_the_window():
    x, y, where = island_position((0, 0, 1920, 1080), SIZE, SCREEN, gap=8)
    assert where == "inside" and y == 8 and x == (1920 - 190) // 2


def test_it_never_leaves_the_screen_sideways():
    x, y, _ = island_position((1800, 300, 800, 600), SIZE, SCREEN)
    assert 0 <= x <= SCREEN[0] - SIZE[0]
    x, y, _ = island_position((-300, 300, 400, 600), SIZE, SCREEN)
    assert x >= 0


def test_a_second_monitor_to_the_left_is_respected():
    screen = (1920, 1080)
    x, y, where = island_position((-1500, 400, 800, 600), SIZE, screen, origin=(-1920, 0))
    assert where == "above" and -1920 <= x <= -1920 + 1920 - 190


# ------------------------------------------------------------------ the spring
def run(spring, target, seconds=2.0, dt=1 / 60):
    trail = []
    for _ in range(int(seconds / dt)):
        spring.step(target, dt)
        trail.append(spring.value)
    return trail


def test_a_critically_damped_spring_settles_without_overshoot():
    s = Spring(100.0, response=0.35, damping=1.0)
    trail = run(s, 300.0)
    assert max(trail) <= 300.0 + 0.01 and abs(trail[-1] - 300.0) < 0.05


def test_a_bouncy_spring_overshoots_then_settles():
    s = Spring(100.0, response=0.35, damping=0.7)
    trail = run(s, 300.0)
    assert max(trail) > 300.5 and abs(trail[-1] - 300.0) < 0.1


def test_the_response_time_is_about_what_was_asked_for():
    s = Spring(0.0, response=0.3, damping=1.0)
    trail = run(s, 100.0, seconds=0.6)
    reached = next(i for i, v in enumerate(trail) if v >= 95.0) / 60.0       # 95% of the way
    assert 0.5 * 0.3 < reached < 1.5 * 0.3          # about the response time, as in Apple's springs


def test_retargeting_mid_flight_keeps_the_motion_continuous():
    s = Spring(0.0, response=0.3, damping=1.0)
    for _ in range(8):
        s.step(100.0, 1 / 60)
    before = s.value
    s.step(0.0, 1 / 60)                               # the user changed their mind
    assert abs(s.value - before) < 6, "no jump when the target changes"
    assert s.velocity > -400


def test_a_huge_time_step_does_not_blow_up():
    s = Spring(0.0, response=0.3, damping=1.0)
    s.step(100.0, 5.0)
    assert 0 <= s.value <= 101


def test_settled_reports_when_it_has_stopped():
    s = Spring(0.0, response=0.2, damping=1.0)
    assert s.settled(0.0)
    s.step(50.0, 1 / 60)
    assert not s.settled(50.0)
    run(s, 50.0, seconds=3.0)
    assert s.settled(50.0)


@pytest.mark.parametrize("bad", [0, -1])
def test_a_non_positive_response_is_refused(bad):
    with pytest.raises(ValueError):
        Spring(0.0, response=bad)
