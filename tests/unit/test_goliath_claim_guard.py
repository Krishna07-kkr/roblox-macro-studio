"""Goliath: a pet put in less than 10 minutes ago is never claimed.

Real run, 2026-11-19 16:51 (dist/debug_log.txt): all three adds verified, then the
verification sweep read all three fresh pets as CLAIM_READY and claimed them at once.
Because the sweep still saw "pending work" the account timer was never set, so the next
cycle hit the same client again. A Goliath takes 10 minutes, so no vision reading can make
a 10-second-old pet claimable.
"""

import os
import tempfile

from macro_studio.core.vision_scanner import SlotState
from macro_studio.runners.claim_scheduler import ClaimScheduler
from macro_studio.runners.goliath import MacroRunner

SLOTS = [
    {"id": i, "name": f"Slot {i}", "slot_click": {"x": 100 * i, "y": 100},
     "claim_click": {"x": 100 * i + 10, "y": 110},
     "scan_region": {"x": 10 * i, "y": 0, "width": 10, "height": 10}}
    for i in (1, 2, 3)
]
ACTIONS = {"pet_select": {"x": 400, "y": 400}, "confirm_yes": {"x": 500, "y": 500}}
CLAIM_POINTS = {(s["claim_click"]["x"], s["claim_click"]["y"]): s["id"] for s in SLOTS}
SLOT_POINTS = {(s["slot_click"]["x"], s["slot_click"]["y"]): s["id"] for s in SLOTS}


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


class Game:
    """Per-window slot states. A fresh pet reads as CLAIM_READY, as in the real log."""

    def __init__(self, windows, fresh_reads_as=SlotState.CLAIM_READY,
                 claimed_reads_as=SlotState.EMPTY_SLOT):
        self.states = {hwnd: {s["id"]: SlotState.EMPTY_SLOT for s in SLOTS} for hwnd in windows}
        self.fresh_reads_as = fresh_reads_as
        self.claimed_reads_as = claimed_reads_as   # real game: still "ready" until refilled
        self.claimed = set()                       # (hwnd, slot) emptied by a claim
        self.claims, self.adds = [], []

    # vision
    def capture_client_full(self, hwnd):
        return hwnd

    def analyze_slot_advanced(self, hwnd, slot):
        return self.states[hwnd][slot["id"]], "scripted", None

    def verify_action_success(self, before, after, kind, slot_name):
        return True, "scripted ok"

    def save_full_capture(self, img, tag):
        return None


class Mouse:
    def __init__(self, game):
        self.game, self.pending, self.hwnd_clicks = game, None, []

    def click(self, x, y, client_offset=None, relative=True, hwnd=None):
        self.hwnd_clicks.append(hwnd)
        states = self.game.states[hwnd]
        if (x, y) in CLAIM_POINTS:
            sid = CLAIM_POINTS[(x, y)]
            self.game.claims.append((hwnd, sid))
            self.game.claimed.add((hwnd, sid))
            states[sid] = self.game.claimed_reads_as
        elif (x, y) in SLOT_POINTS:
            self.pending = SLOT_POINTS[(x, y)]
        elif (x, y) == (ACTIONS["confirm_yes"]["x"], ACTIONS["confirm_yes"]["y"]):
            empty = (states.get(self.pending) == SlotState.EMPTY_SLOT
                     or (hwnd, self.pending) in self.game.claimed)
            if self.pending and empty:
                self.game.claimed.discard((hwnd, self.pending))
                self.game.adds.append((hwnd, self.pending))
                states[self.pending] = self.game.fresh_reads_as
            self.pending = None
        return True


class WM:
    def __init__(self, windows):
        self.windows, self.activated = list(windows), []
        self.minimized = set()

    def find_windows(self, include_minimized=False):
        return [h for h in self.windows if include_minimized or h not in self.minimized]

    def get_pid(self, hwnd):
        return hwnd + 1000

    def force_activate_window(self, hwnd, max_wait_sec=1.5, auto_shrink=False):
        self.activated.append(hwnd)
        return True

    def is_foreground(self, hwnd):
        return True

    def get_client_offset(self, hwnd):
        return (0, 0)

    def get_window_title(self, hwnd):
        return "Roblox"

    def shrink_all_windows_once(self, windows):
        return 0

    def auto_arrange(self, *a, **k):
        pass


def build(game, wm, scheduler):
    r = object.__new__(MacroRunner)
    r.log_callback = r.stats_callback = r.frame_callback = None
    r.max_cycles = 1
    r.running, r.paused, r.hud = True, False, None
    r.stats = {"cycles": 0, "claims": 0, "adds": 0, "accounts": 0, "active_hwnd": 0}
    r.use_relative = True
    r.auto_arrange = r.auto_shrink = False
    r.layout_style, r.offset_x, r.offset_y, r.grid_cols = "diagonal", 10, 10, 2
    r.config = {"slots": SLOTS, "common_actions": ACTIONS,
                "delays": {k: 0.0 for k in ("window_focus_delay", "between_slots_delay",
                                            "cycle_delay")}}
    r.wm, r.vs, r.input = wm, game, Mouse(game)
    r.verify_fill, r.fill_retries = True, 2
    r.capture_failures = r.dismiss_popups = False
    r.d_settle = r.d_after_slot = r.d_after_pet = r.d_after_confirm = r.d_after_claim = 0.0
    r.scheduler = scheduler
    r.logs = []
    r.log = r.logs.append
    r._mapper = lambda hwnd: _Identity()
    return r


class _Identity:
    is_identity = True


def scheduler(clock):
    return ClaimScheduler(wait_minutes=10, path=os.path.join(tempfile.mkdtemp(), "s.json"),
                          clock=clock)


def test_fresh_pets_misread_as_ready_are_not_claimed_and_timer_starts():
    clock, game, wm = Clock(), Game([7]), WM([7])
    sched = scheduler(clock)
    build(game, wm, sched)._run_loop()
    assert len(game.adds) == 3
    assert game.claims == [], f"claimed pets added seconds ago: {game.claims}"
    assert not sched.is_due(1007), "account timer must start after the pets go in"


def test_after_ten_minutes_the_account_is_claimed_and_refilled():
    clock, game, wm = Clock(), Game([7]), WM([7])
    sched = scheduler(clock)
    build(game, wm, sched)._run_loop()
    clock.now += 9 * 60
    build(game, wm, sched)._run_loop()
    assert game.claims == [], "claimed before 10 minutes"
    clock.now += 61
    build(game, wm, sched)._run_loop()
    assert sorted(sid for _, sid in game.claims) == [1, 2, 3]
    assert len(game.adds) == 6, "every claimed slot gets a new pet"
    assert not sched.is_due(1007), "timer restarts after the re-add"


def test_claimed_slot_that_still_reads_ready_gets_its_grow_timer():
    """Real run 2026-11-19 21:16: after a verified claim the slot kept reading CLAIM_READY
    until the new pet went in, so the add looked like "the claim never took", the slot
    timer was skipped, and the sweep claimed the 10-second-old pet in Slot 2."""
    clock = Clock()
    game = Game([7], claimed_reads_as=SlotState.CLAIM_READY)
    wm = WM([7])
    sched = scheduler(clock)
    build(game, wm, sched)._run_loop()          # fill at t=0
    clock.now += 601
    build(game, wm, sched)._run_loop()          # due: claim + refill all three
    assert sorted(sid for _, sid in game.claims) == [1, 2, 3], game.claims
    assert len(game.adds) == 6
    assert not sched.is_due(1007), "timer restarts after the re-add"
    for sid in (1, 2, 3):
        assert not sched.slot_claimable(1007, sid), f"slot {sid} fresh pet is claimable"


def test_accounts_are_handled_one_by_one_and_waiting_ones_are_skipped():
    clock, game, wm = Clock(), Game([7, 8]), WM([7, 8])
    sched = scheduler(clock)
    build(game, wm, sched)._run_loop()          # both filled at t=0
    clock.now += 5 * 60
    game.states[9] = {s["id"]: SlotState.EMPTY_SLOT for s in SLOTS}
    wm.windows.append(9)                         # a new client appears at t=5m
    wm.activated.clear()
    build(game, wm, sched)._run_loop()
    assert wm.activated == [9], f"only the new client is due: {wm.activated}"
    clock.now += 5 * 60 + 1                      # t=10m01s: 7 and 8 due, 9 not
    wm.activated.clear()
    build(game, wm, sched)._run_loop()
    assert wm.activated == [7, 8]


def test_goliath_works_only_on_selected_tabs():
    from macro_studio.core.tab_selection import TabSelection
    clock, game, wm = Clock(), Game([7, 8, 9]), WM([7, 8, 9])
    sel = TabSelection()
    sel.set_pids([1008])                         # WM.get_pid is hwnd + 1000
    r = build(game, wm, scheduler(clock))
    r.tab_selection = sel
    r._run_loop()
    assert wm.activated == [8], wm.activated
    assert {h for h, _ in game.adds} == {8}
    sel.set_all()                                # changed while running: next pass sees all
    r = build(game, wm, r.scheduler)
    r.tab_selection = sel
    r._run_loop()
    assert wm.activated == [8, 7, 9], "8 is waiting on its timer, the others are new"


def test_a_minimized_or_unticked_client_keeps_its_timers():
    from macro_studio.core.tab_selection import TabSelection
    clock, game, wm = Clock(), Game([7, 8, 9]), WM([7, 8, 9])
    sched = scheduler(clock)
    build(game, wm, sched)._run_loop()           # all filled at t=0
    wm.minimized.add(7)
    sel = TabSelection()
    sel.set_pids([1007, 1009])                    # 8 unticked
    r = build(game, wm, sched)
    r.tab_selection = sel
    r._run_loop()                                 # only 9 is visible and ticked (and waiting)
    wm.minimized.clear()
    clock.now += 60
    wm.activated.clear()
    build(game, wm, sched)._run_loop()
    assert wm.activated == [], f"fresh pets were worked again: {wm.activated}"
    assert game.claims == []


def test_scheduler_remembers_slot_fill_times_across_restarts(tmp_path):
    clock = Clock()
    path = tmp_path / "s.json"
    s = ClaimScheduler(wait_minutes=10, path=str(path), clock=clock)
    s.mark_slot_added(1007, 2)
    assert not s.slot_claimable(1007, 2)
    assert s.slot_claimable(1007, 1), "a slot never filled by us may be claimed"
    again = ClaimScheduler(wait_minutes=10, path=str(path), clock=clock)
    assert not again.slot_claimable(1007, 2)
    clock.now += 600
    assert again.slot_claimable(1007, 2)
    again.prune([])
    assert again.slot_claimable(1007, 2)
