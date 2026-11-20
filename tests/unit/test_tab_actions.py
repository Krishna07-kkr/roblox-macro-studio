"""Right-click actions in the Tabs picker (selection rules and the safe window/process actions)."""

import threading
from types import SimpleNamespace

from macro_studio.core.tab_selection import TabSelection
from macro_studio.tools.macro.tab_actions import TabActions

OPEN = [1, 2, 3, 4]


# ------------------------------------------------------------------ selection rules
def test_select_only_takes_exactly_those_tabs():
    sel = TabSelection()
    sel.select_only([2, 3], OPEN)
    assert sel.pids == frozenset({2, 3})


def test_selecting_every_open_tab_goes_back_to_all():
    sel = TabSelection()
    sel.select_only([1, 2], OPEN)
    sel.select_only(OPEN, OPEN)
    assert sel.all_tabs


def test_select_only_nothing_means_no_tabs():
    sel = TabSelection()
    sel.select_only([], OPEN)
    assert sel.pids == frozenset() and sel.describe(4) == "No tabs"


def test_invert_swaps_ticked_and_unticked():
    sel = TabSelection()
    sel.select_only([1, 2], OPEN)
    sel.invert(OPEN)
    assert sel.pids == frozenset({3, 4})


def test_invert_from_all_gives_no_tabs():
    sel = TabSelection()
    sel.invert(OPEN)
    assert sel.pids == frozenset()


def test_invert_with_no_open_tabs_changes_nothing():
    sel = TabSelection()
    sel.invert([])
    assert sel.all_tabs


def test_range_follows_the_displayed_order():
    sel = TabSelection()
    assert sel.range_pids(OPEN, 4, 2) == [2, 3, 4]
    assert sel.range_pids(OPEN, 1, 2) == [1, 2]
    assert sel.range_pids(OPEN, 3, 3) == [3]


def test_range_with_an_unknown_start_is_just_the_clicked_tab():
    sel = TabSelection()
    assert sel.range_pids(OPEN, 2, 99) == [2]
    assert sel.range_pids(OPEN, 99, 2) == []


# ------------------------------------------------------------------ actions
class FakeWM:
    def __init__(self, owners):
        self.owners, self.focused = dict(owners), []

    def get_pid(self, hwnd):
        return self.owners.get(hwnd, 0)

    def force_activate_window(self, hwnd):
        self.focused.append(hwnd)


class Fakes:
    def __init__(self, names=None):
        self.names = names or {}
        self.trimmed, self.closed, self.log = [], [], []


def tab(hwnd, pid):
    return SimpleNamespace(hwnd=hwnd, pid=pid, title="Roblox")


def make(owners, names=None):
    fakes = Fakes({pid: "robloxplayerbeta.exe" for pid in owners.values()} if names is None
                  else names)
    wm = FakeWM(owners)
    actions = TabActions(wm, fakes.log.append,
                         process_name=lambda pid: fakes.names.get(pid, ""),
                         trim=fakes.trimmed.append, terminate=fakes.closed.append)
    return actions, wm, fakes


def test_focus_brings_the_window_to_the_front():
    actions, wm, _ = make({10: 100})
    actions.do("focus", [tab(10, 100)])
    assert wm.focused == [10]


def test_trim_empties_memory_of_each_verified_client():
    actions, _, fakes = make({10: 100, 11: 101})
    actions.do("trim", [tab(10, 100), tab(11, 101)])
    assert fakes.trimmed == [100, 101]
    assert any("2" in line and "trim" in line.lower() for line in fakes.log), fakes.log


def test_close_ends_each_verified_client():
    actions, _, fakes = make({10: 100})
    actions.do("close", [tab(10, 100)])
    assert fakes.closed == [100]


def test_a_window_whose_owner_changed_is_left_alone():
    """The picker's list is up to a second old; the window may now belong to another program."""
    actions, _, fakes = make({10: 555})            # hwnd 10 is now process 555, not 100
    actions.do("close", [tab(10, 100)])
    actions.do("trim", [tab(10, 100)])
    assert fakes.closed == [] and fakes.trimmed == []
    assert any("skipped" in line.lower() for line in fakes.log), fakes.log


def test_a_process_that_is_not_a_roblox_client_is_never_closed():
    actions, _, fakes = make({10: 100}, names={100: "notepad.exe"})
    actions.do("close", [tab(10, 100)])
    assert fakes.closed == [] and any("skipped" in line.lower() for line in fakes.log)


def test_one_failure_does_not_stop_the_rest():
    actions, _, fakes = make({10: 100, 11: 101})

    def flaky(pid):
        if pid == 100:
            raise PermissionError("denied")
        fakes.closed.append(pid)
    actions._terminate = flaky
    actions.do("close", [tab(10, 100), tab(11, 101)])
    assert fakes.closed == [101]
    assert any("denied" in line for line in fakes.log), fakes.log


def test_an_unknown_action_is_refused():
    actions, _, fakes = make({10: 100})
    actions.do("explode", [tab(10, 100)])
    assert fakes.closed == [] and fakes.trimmed == []


def test_run_goes_to_one_background_worker_and_stop_ends_it():
    actions, _, fakes = make({10: 100})
    actions = TabActions(FakeWM({10: 100}), fakes.log.append,
                         process_name=lambda pid: "robloxplayerbeta.exe",
                         trim=fakes.trimmed.append, terminate=fakes.closed.append)
    before = threading.active_count()
    for _ in range(20):
        actions.run("trim", [tab(10, 100)])
    actions.stop(wait=True)
    assert fakes.trimmed == [100] * 20
    assert threading.active_count() <= before + 1, "20 actions must not start 20 threads"
    assert not actions.alive


def test_run_after_stop_does_nothing():
    actions, _, fakes = make({10: 100})
    actions.stop(wait=True)
    actions.run("trim", [tab(10, 100)])
    assert fakes.trimmed == []
