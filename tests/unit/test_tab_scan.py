"""The Tabs picker lists every Roblox client, minimized ones too (they cannot be captured)."""

import numpy as np

from macro_studio.core.tab_selection import snapshot_tabs
from macro_studio.tools.macro.tab_actions import TabActions

PIDS = {1: 101, 2: 102, 3: 103}


def frame(value):
    return np.full((60, 80, 3), value, dtype=np.uint8)


class Source:
    def __init__(self):
        self.hwnds = [1, 2, 3]
        self.minimized = set()
        self.captured = []
        self.color = {1: 10, 2: 100, 3: 200}

    def find(self):
        return list(self.hwnds)

    def capture(self, hwnd):
        self.captured.append(hwnd)
        return frame(self.color[hwnd])

    def snap(self, cache):
        return snapshot_tabs(self.find, PIDS.get, lambda h: f"Roblox {h}", self.capture,
                             size=(40, 30), is_minimized=lambda h: h in self.minimized,
                             cache=cache)


def test_active_clients_are_captured_as_before():
    s, cache = Source(), {}
    tabs = s.snap(cache)
    assert [t.hwnd for t in tabs] == [1, 2, 3]
    assert all(t.image is not None and not t.minimized for t in tabs)


def test_a_minimized_client_is_listed_but_never_captured():
    s, cache = Source(), {}
    s.minimized = {2}
    tabs = s.snap(cache)
    assert [t.hwnd for t in tabs] == [1, 2, 3]
    assert 2 not in s.captured, "capturing a minimized window returns garbage and costs time"
    assert tabs[1].minimized and not tabs[0].minimized


def test_a_minimized_client_that_was_never_seen_has_no_picture():
    s, cache = Source(), {}
    s.minimized = {2}
    assert s.snap(cache)[1].image is None


def test_a_client_minimized_later_keeps_its_last_picture():
    s, cache = Source(), {}
    first = s.snap(cache)
    s.minimized = {2}
    s.captured.clear()
    later = s.snap(cache)
    assert later[1].minimized and later[1].image is not None
    assert later[1].image.tobytes() == first[1].image.tobytes()
    assert s.captured == [1, 3]


def test_the_picture_cache_forgets_clients_that_closed():
    s, cache = Source(), {}
    s.snap(cache)
    s.hwnds = [1]
    s.snap(cache)
    assert set(cache) == {1}, "the cache must not grow as clients come and go"


def test_a_tab_has_no_minimized_flag_set_by_default():
    from macro_studio.core.tab_selection import TabInfo
    assert TabInfo(1, 2, "x", None).minimized is False


# ------------------------------------------------------------------ restore / minimize
class FakeWM:
    def __init__(self, owners):
        self.owners = owners

    def get_pid(self, hwnd):
        return self.owners.get(hwnd, 0)

    def force_activate_window(self, hwnd):
        pass


def make(owners):
    shown, log = [], []
    actions = TabActions(FakeWM(owners), log.append,
                         process_name=lambda pid: "robloxplayerbeta.exe",
                         trim=lambda pid: None, terminate=lambda pid: None,
                         show_window=lambda hwnd, how: shown.append((hwnd, how)))
    return actions, shown, log


class T:
    def __init__(self, hwnd, pid):
        self.hwnd, self.pid, self.title = hwnd, pid, "Roblox"


def test_restore_brings_a_minimized_window_back():
    actions, shown, _ = make({10: 100})
    actions.do("restore", [T(10, 100)])
    assert shown == [(10, "restore")]


def test_minimize_sends_the_window_to_the_taskbar():
    actions, shown, _ = make({10: 100})
    actions.do("minimize", [T(10, 100)])
    assert shown == [(10, "minimize")]


def test_restore_skips_a_window_that_changed_owner():
    actions, shown, log = make({10: 999})
    actions.do("restore", [T(10, 100)])
    assert shown == [] and any("skipped" in line.lower() for line in log)
