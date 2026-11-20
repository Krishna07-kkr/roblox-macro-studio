"""Tab picker: the macros only work on the Roblox client tabs the user ticked."""

import numpy as np

from macro_studio.core.tab_selection import TabSelection, snapshot_tabs
from macro_studio.runners.mutation import MutationRunner

PIDS = {101: 9001, 102: 9002, 103: 9003}


def get_pid(hwnd):
    return PIDS.get(hwnd, 0)


def test_default_is_every_tab():
    sel = TabSelection()
    assert sel.all_tabs
    assert sel.filter([101, 102, 103], get_pid) == [101, 102, 103]
    assert sel.describe(3) == "All tabs (3)"


def test_only_ticked_tabs_pass_in_window_order():
    sel = TabSelection()
    sel.set_pids([9003, 9001])
    assert not sel.all_tabs
    assert sel.filter([101, 102, 103], get_pid) == [101, 103]
    assert sel.includes(9001) and not sel.includes(9002)
    assert sel.describe(3) == "2 of 3 tabs"


def test_nothing_ticked_means_no_tabs_and_all_resets():
    sel = TabSelection()
    sel.set_pids([])
    assert sel.filter([101, 102], get_pid) == []
    assert sel.describe(2) == "No tabs"
    sel.set_all()
    assert sel.filter([101, 102], get_pid) == [101, 102]


def test_toggle_from_all_keeps_the_other_open_tabs():
    sel = TabSelection()
    sel.toggle(9002, open_pids=[9001, 9002, 9003])      # untick one while on "All"
    assert sel.filter([101, 102, 103], get_pid) == [101, 103]
    sel.toggle(9002, open_pids=[9001, 9002, 9003])      # tick it back: all again
    assert sel.all_tabs


def test_snapshot_lists_each_tab_with_its_actual_screen():
    frames = {101: np.full((600, 800, 3), 40, np.uint8), 102: None}
    tabs = snapshot_tabs(find_windows=lambda: [101, 102], get_pid=get_pid,
                         get_title=lambda h: f"Roblox {h}", capture=frames.get,
                         size=(160, 120))
    assert [(t.hwnd, t.pid, t.title) for t in tabs] == [
        (101, 9001, "Roblox 101"), (102, 9002, "Roblox 102")]
    assert tabs[0].image.size == (160, 120)
    assert tabs[1].image is None, "a tab that cannot be captured still shows up"


class _WM:
    def __init__(self, hwnds):
        self.hwnds = hwnds

    def find_windows(self, include_minimized=False):
        return list(self.hwnds)

    def get_pid(self, hwnd):
        return get_pid(hwnd)

    def get_window_title(self, hwnd):
        return "Roblox"


def test_mutation_runner_works_only_on_selected_tabs():
    sel = TabSelection()
    sel.set_pids([9002])
    r = object.__new__(MutationRunner)
    r.wm, r.tab_selection = _WM([101, 102, 103]), sel
    r.auto_shrink = r.auto_arrange = False
    r.logs = []
    r.log = r.logs.append
    states = r._discover_windows()
    assert [s.hwnd for s in states] == [102]
    assert any("1 of 3" in line for line in r.logs), r.logs
