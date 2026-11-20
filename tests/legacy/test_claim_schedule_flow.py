"""End-to-end check that the Goliath runner really honours the claim timer.

The unit tests in test_claim_scheduler.py only prove the bookkeeping. This one
drives MacroRunner.run() itself with fake windows, fake vision and a fake mouse,
and asserts on the clicks that actually got sent - because the whole point of
the feature is that a scheduled account receives no clicks at all.
"""

import os
import tempfile
import time

import main
from claim_scheduler import ClaimScheduler
from vision_scanner import SlotState

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


class FakeWM:
    """Two Roblox clients: hwnd 101 -> pid 11, hwnd 102 -> pid 22."""

    def __init__(self):
        self.activated = []

    def find_windows(self, include_minimized=False):
        return [101, 102]

    def get_pid(self, hwnd):
        return {101: 11, 102: 22}[hwnd]

    def force_activate_window(self, hwnd, max_wait_sec=1.5, auto_shrink=False):
        self.activated.append(hwnd)
        return True

    def is_foreground(self, hwnd):
        return True

    def shrink_all_windows_once(self, windows):
        return 0

    def auto_arrange(self, *a, **k):
        pass

    def get_client_offset(self, hwnd):
        return (0, 0)

    def get_window_title(self, hwnd):
        return f"Roblox {hwnd}"


class FakeVision:
    """A slot starts CLAIM_READY and becomes BUSY once a pet is confirmed.

    Modelling the transition matters now that the runner refuses to move on
    while anything is still claimable: a slot frozen at CLAIM_READY would mean
    the claim never took, and no timer would ever be armed.
    """

    def __init__(self):
        self.states = {}        # (hwnd, slot id) -> state
        self._hwnd = None

    def capture_client_full(self, hwnd):
        self._hwnd = hwnd
        return object()

    def analyze_slot_advanced(self, img, slot):
        key = (self._hwnd, slot.get("id"))
        return self.states.get(key, SlotState.CLAIM_READY), "fake claim", None

    def settle(self, hwnd, slot_id):
        self.states[(hwnd, slot_id)] = SlotState.BUSY_TIMER

    def verify_action_success(self, before, after, kind, slot_name):
        return True, f"fake {kind} verified"


class FakeInput:
    """Confirming a pet settles that slot, the way the real game does."""

    def __init__(self, vision=None):
        self.clicks = []
        self.vision = vision

    def click(self, x, y, client_offset=None, relative=True, hwnd=None):
        self.clicks.append((hwnd, x, y))
        if self.vision and (x, y) == (40, 40):      # confirm_yes
            self.vision.settle(hwnd, 1)
        return True


def build_runner(wait_minutes, cycles=1):
    """A MacroRunner with every OS-touching part replaced by a fake."""
    r = object.__new__(main.MacroRunner)
    r.log_callback = None
    r.stats_callback = None
    r.frame_callback = None
    r.max_cycles = cycles
    r.running = True
    r.paused = False
    r.stats = {"cycles": 0, "claims": 0, "adds": 0, "accounts": 0, "active_hwnd": 0}
    r.hud = None
    r.use_relative = True
    r.auto_arrange = False
    r.auto_shrink = False
    r.layout_style = "diagonal"
    r.offset_x = r.offset_y = 10
    r.grid_cols = 2
    r.config = {
        "slots": [{"id": 1, "name": "Slot 1",
                   "slot_click": {"x": 10, "y": 10},
                   "claim_click": {"x": 20, "y": 20},
                   "scan_region": {"x": 0, "y": 0, "width": 5, "height": 5}}],
        "common_actions": {"pet_select": {"x": 30, "y": 30},
                           "confirm_yes": {"x": 40, "y": 40}},
        "delays": {k: 0.0 for k in ("window_focus_delay", "click_delay",
                                    "after_claim_delay", "after_pet_select_delay",
                                    "after_confirm_delay", "between_slots_delay",
                                    "cycle_delay")},
    }
    r.wm = FakeWM()
    r.vs = FakeVision()
    r.input = FakeInput(r.vs)
    r.claim_on_timer = False   # this test is about the skip, not the claim
    r.verify_fill = True
    r.fill_retries = 2
    r.d_settle = r.d_after_slot = r.d_after_pet = 0.0
    r.d_after_confirm = r.d_after_claim = 0.0
    r.scheduler = ClaimScheduler(
        wait_minutes=wait_minutes,
        path=os.path.join(tempfile.mkdtemp(), "account_schedule.json"))
    # Silence the runner's own logging noise but keep it callable.
    r.log = lambda msg: None
    return r


def clicked_windows(runner):
    return {hwnd for hwnd, _, _ in runner.input.clicks}


def run():
    # --- Cycle 1: nothing is scheduled yet, so both accounts get worked. ---
    r = build_runner(wait_minutes=10, cycles=1)
    r.run()
    check("first pass clicks both accounts", clicked_windows(r) == {101, 102},
          clicked_windows(r))
    check("first pass focuses both accounts", set(r.wm.activated) == {101, 102},
          r.wm.activated)
    check("timer armed for account 1", not r.scheduler.is_due(11))
    check("timer armed for account 2", not r.scheduler.is_due(22))
    remaining = r.scheduler.seconds_remaining(11)
    check("timer is ~10 minutes", 590 <= remaining <= 600, remaining)

    # --- Cycle 2: same schedule file, so both accounts must be skipped. ---
    r2 = build_runner(wait_minutes=10, cycles=1)
    r2.scheduler = r.scheduler
    r2.run()
    check("scheduled accounts receive no clicks", r2.input.clicks == [], r2.input.clicks)
    check("scheduled accounts are not focused", r2.wm.activated == [], r2.wm.activated)
    check("no claims counted while waiting", r2.stats["claims"] == 0, r2.stats)

    # --- One account's timer expires: only that account is worked. ---
    r3 = build_runner(wait_minutes=10, cycles=1)
    r3.scheduler = r.scheduler
    r3.scheduler.records["11"]["next_claim_timestamp"] = time.time() - 1
    # "Ten minutes later" also ages the per-slot fill times, as real time would.
    slots_added = r3.scheduler.records["11"].get("slots_added", {})
    for slot_id in slots_added:
        slots_added[slot_id] = time.time() - 601
    r3.run()
    check("expired account is worked again", clicked_windows(r3) == {101},
          clicked_windows(r3))
    check("still-waiting account stays untouched", 102 not in clicked_windows(r3))
    check("expired account timer re-armed", not r3.scheduler.is_due(11))

    # --- Disabled scheduler must never skip anything. ---
    r4 = build_runner(wait_minutes=10, cycles=1)
    r4.scheduler = ClaimScheduler(
        wait_minutes=10, enabled=False,
        path=os.path.join(tempfile.mkdtemp(), "s.json"))
    r4.scheduler.mark_pet_added(11)
    r4.scheduler.mark_pet_added(22)
    r4.run()
    check("disabled scheduler works every account",
          clicked_windows(r4) == {101, 102}, clicked_windows(r4))

    # --- A closed client's record is pruned, not left blocking a relaunch. ---
    r5 = build_runner(wait_minutes=10, cycles=1)
    r5.scheduler = ClaimScheduler(
        wait_minutes=10, path=os.path.join(tempfile.mkdtemp(), "s.json"))
    r5.scheduler.mark_pet_added(999)  # a client that no longer exists
    r5.run()
    check("dead account pruned from schedule", "999" not in r5.scheduler.records,
          list(r5.scheduler.records))

    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())


def test_legacy_checks():
    """Run every check in this script under pytest; a recorded failure fails the test."""
    assert run() == 0, failures
