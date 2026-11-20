"""Layout maths, the layout service and the multi-instance lock (spec 001, US4, FR-030..033)."""

import random

import pytest

from macro_studio.tools.layout.layout import STYLES, Rect, compute_layout
from macro_studio.tools.layout.service import LayoutService, MultiInstance

AREA = Rect(0, 0, 1920, 1040)           # a work area (taskbar excluded)


def inside(rect, area):
    return (area.x <= rect.x and area.y <= rect.y and rect.x + rect.w <= area.x + area.w
            and rect.y + rect.h <= area.y + area.h)


# ------------------------------------------------------------------ the maths
def test_the_four_styles_exist():
    assert STYLES == ("cascade", "grid", "columns", "stack")


def test_no_windows_gives_no_rectangles():
    for style in STYLES:
        assert compute_layout(style, 0, AREA) == []


def test_cascade_steps_by_the_offset_from_the_corner():
    rects = compute_layout("cascade", 3, AREA, size=(800, 600), offset=(30, 30))
    assert [(r.x, r.y) for r in rects] == [(0, 0), (30, 30), (60, 60)]
    assert all((r.w, r.h) == (800, 600) for r in rects)


def test_cascade_default_offset_is_30():
    rects = compute_layout("cascade", 2, AREA)
    assert (rects[1].x - rects[0].x, rects[1].y - rects[0].y) == (30, 30)


def test_cascade_starts_again_instead_of_leaving_the_screen():
    rects = compute_layout("cascade", 60, AREA, size=(800, 600), offset=(30, 30))
    assert all(inside(r, AREA) for r in rects)
    assert len({(r.x, r.y) for r in rects}) == 60              # never two windows on one spot


def test_grid_uses_a_square_ish_grid_of_equal_cells():
    rects = compute_layout("grid", 4, AREA)
    assert [(r.x, r.y, r.w, r.h) for r in rects] == [
        (0, 0, 960, 520), (960, 0, 960, 520), (0, 520, 960, 520), (960, 520, 960, 520)]


def test_grid_with_three_windows_is_two_columns():
    rects = compute_layout("grid", 3, AREA)
    assert sorted({r.x for r in rects}) == [0, 960]


def test_columns_are_equal_width_and_full_height():
    rects = compute_layout("columns", 3, AREA)
    assert [(r.x, r.w, r.h) for r in rects] == [(0, 640, 1040), (640, 640, 1040), (1280, 640, 1040)]


def test_stack_centres_every_window_in_the_same_place():
    rects = compute_layout("stack", 3, AREA, size=(800, 600))
    assert {(r.x, r.y, r.w, r.h) for r in rects} == {(560, 220, 800, 600)}


def test_a_size_bigger_than_the_screen_is_shrunk_to_fit():
    small = Rect(0, 0, 640, 480)
    rects = compute_layout("stack", 1, small, size=(800, 600))
    assert inside(rects[0], small)


def test_a_second_monitor_with_an_offset_origin_is_respected():
    right = Rect(1920, -200, 1280, 1024)
    for style in STYLES:
        assert all(inside(r, right) for r in compute_layout(style, 5, right))


def test_every_style_stays_on_screen_for_many_shapes():
    rng = random.Random(3)
    for _ in range(200):
        area = Rect(rng.randint(-2000, 2000), rng.randint(-1000, 1000),
                    rng.randint(400, 3840), rng.randint(300, 2160))
        count = rng.randint(1, 16)
        size = (rng.randint(200, 1600), rng.randint(200, 1200))
        offset = (rng.randint(0, 80), rng.randint(0, 80))
        for style in STYLES:
            assert all(inside(r, area) and r.w > 0 and r.h > 0
                       for r in compute_layout(style, count, area, size, offset)), (style, area, count)


# ------------------------------------------------------------------ the service
class Automation:
    def __init__(self, free=True):
        self.free = free

    def try_borrow(self, tool):
        return self.free


class World:
    def __init__(self):
        self.placed, self.minimised, self.restored, self.logs = [], [], [], []
        self.windows = [(30, 300), (10, 100), (20, 200)]            # (hwnd, pid), unordered
        self.monitors = [Rect(0, 0, 1920, 1040), Rect(1920, 0, 1280, 1024)]

    def service(self, free=True):
        return LayoutService(
            windows=lambda: list(self.windows), monitors=lambda: list(self.monitors),
            place=lambda hwnd, rect: self.placed.append((hwnd, rect)),
            minimise=self.minimised.append, restore=self.restored.append,
            automation=Automation(free), log=self.logs.append)


def test_windows_are_placed_in_a_stable_order_by_process_id():
    world = World()
    result = world.service().arrange("columns", offset=(30, 30), size=(800, 600), monitor=0)
    assert result.ok and [hwnd for hwnd, _ in world.placed] == [10, 20, 30]
    assert [r.x for _, r in world.placed] == [0, 640, 1280]


def test_the_order_does_not_change_when_the_window_list_order_does():
    world = World()
    service = world.service()
    service.arrange("grid", (30, 30), (800, 600), 0)
    first = list(world.placed)
    world.placed.clear()
    world.windows.reverse()
    service.arrange("grid", (30, 30), (800, 600), 0)
    assert world.placed == first


def test_the_chosen_monitor_is_used_and_a_missing_one_falls_back_to_the_first():
    world = World()
    service = world.service()
    service.arrange("stack", (30, 30), (800, 600), 1)
    assert all(r.x >= 1920 for _, r in world.placed)
    world.placed.clear()
    service.arrange("stack", (30, 30), (800, 600), 7)
    assert all(r.x < 1920 for _, r in world.placed)


def test_nothing_moves_while_a_macro_runs():
    world = World()
    result = world.service(free=False).arrange("grid", (30, 30), (800, 600), 0)
    assert not result.ok and "macro" in result.reason.lower() and world.placed == []
    assert not world.service(free=False).minimise_all().ok
    assert not world.service(free=False).restore_all().ok


def test_no_clients_is_a_clear_message_not_an_error():
    world = World()
    world.windows = []
    result = world.service().arrange("grid", (30, 30), (800, 600), 0)
    assert not result.ok and "no roblox" in result.reason.lower()


def test_minimise_and_restore_reach_every_window():
    world = World()
    service = world.service()
    assert service.minimise_all().ok and sorted(world.minimised) == [10, 20, 30]
    assert service.restore_all().ok and sorted(world.restored) == [10, 20, 30]


def test_an_unknown_style_is_refused():
    with pytest.raises(ValueError):
        World().service().arrange("spiral", (30, 30), (800, 600), 0)


# ------------------------------------------------------------------ multi-instance
class Mutex:
    def __init__(self):
        self.open, self.opened = set(), 0

    def create(self, name):
        self.opened += 1
        self.open.add(self.opened)
        return self.opened

    def close(self, handle):
        self.open.discard(handle)


def test_the_lock_is_held_until_it_is_turned_off():
    mutex = Mutex()
    multi = MultiInstance(create=mutex.create, close=mutex.close, clients_running=lambda: False)
    assert multi.enable().ok and multi.active and len(mutex.open) == 1
    assert multi.enable().ok and len(mutex.open) == 1          # turning it on twice holds one handle
    multi.disable()
    assert not multi.active and mutex.open == set()


def test_it_is_refused_while_clients_are_already_running():
    mutex = Mutex()
    multi = MultiInstance(create=mutex.create, close=mutex.close, clients_running=lambda: True)
    result = multi.enable()
    assert not result.ok and "close" in result.reason.lower() and not multi.active
    assert mutex.open == set()


def test_a_failure_to_create_the_lock_is_reported():
    multi = MultiInstance(create=lambda name: 0, close=lambda h: None, clients_running=lambda: False)
    result = multi.enable()
    assert not result.ok and not multi.active


def test_disabling_when_off_is_harmless():
    mutex = Mutex()
    multi = MultiInstance(create=mutex.create, close=mutex.close, clients_running=lambda: False)
    multi.disable()
    assert mutex.open == set()


def test_the_lock_uses_the_name_roblox_checks():
    seen = []
    multi = MultiInstance(create=lambda name: seen.append(name) or 1, close=lambda h: None,
                          clients_running=lambda: False)
    multi.enable()
    assert seen == ["ROBLOX_singletonEvent"]
