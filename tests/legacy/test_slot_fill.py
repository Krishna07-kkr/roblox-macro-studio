"""Goliath: every slot must end up filled, and every click must be precise.

Two things are pinned here:

1. The settle sweep. A missed add or a missed claim used to be silent - the
   slot just stayed empty, or the pet stayed sitting there ready, until some
   later cycle noticed. Now, before moving to the next tab, the runner re-reads
   all slots, claims what is ready, fills what is empty, and refuses to start
   the claim timer while anything is still pending.

2. The click guards. A click is only sent when the window is in front and the
   cursor is verified on the exact pixel, and calibrated points are scaled into
   the window's real client area.
"""

import os
import tempfile

import main
import scaling
from claim_scheduler import ClaimScheduler
from vision_scanner import SlotState

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


SLOTS = [
    {"id": 1, "name": "Slot 1", "slot_click": {"x": 100, "y": 100},
     "claim_click": {"x": 110, "y": 110},
     "scan_region": {"x": 0, "y": 0, "width": 10, "height": 10}},
    {"id": 2, "name": "Slot 2", "slot_click": {"x": 200, "y": 100},
     "claim_click": {"x": 210, "y": 110},
     "scan_region": {"x": 10, "y": 0, "width": 10, "height": 10}},
    {"id": 3, "name": "Slot 3", "slot_click": {"x": 300, "y": 100},
     "claim_click": {"x": 310, "y": 110},
     "scan_region": {"x": 20, "y": 0, "width": 10, "height": 10}},
]
ACTIONS = {"pet_select": {"x": 400, "y": 400}, "confirm_yes": {"x": 500, "y": 500}}


class ScriptedVision:
    """Slot states come from a script that can change after each add/claim."""

    def __init__(self, states, fill_on_add=True, claim_works=True):
        self.states = dict(states)          # slot id -> state
        self.fill_on_add = fill_on_add
        self.claim_works = claim_works
        self.claimed = []
        self.reads = 0
        self.last_claim_ok = True

    def capture_client_full(self, hwnd):
        return object()

    def analyze_slot_advanced(self, img, slot):
        self.reads += 1
        state = self.states.get(slot["id"], SlotState.BUSY_TIMER)
        return state, f"scripted {state}", None

    def verify_action_success(self, before, after, kind, slot_name):
        # Claims are verified honestly: a verified claim is trusted to have emptied
        # the slot (the real game still shows "ready" until the new pet is in), so a
        # missed claim must read as unverified for the sweep to retry it.
        if kind == "CLAIM" and not self.last_claim_ok:
            return False, "scripted: claim did not take"
        return True, "scripted ok"

    def save_full_capture(self, img, tag):
        return None

    def mark_filled(self, slot_id):
        # A pet can only go into an EMPTY slot. Modelling this matters: if a
        # claim silently failed, the slot is still holding a ready pet, so the
        # add that follows cannot turn it into a filled slot either.
        if self.fill_on_add and self.states.get(slot_id) == SlotState.EMPTY_SLOT:
            self.states[slot_id] = SlotState.BUSY_TIMER

    def mark_claimed(self, slot_id):
        """A claim empties the slot, which the add path then refills."""
        self.last_claim_ok = self.claim_works
        if self.claim_works:
            self.claimed.append(slot_id)
            self.states[slot_id] = SlotState.EMPTY_SLOT


class FakeWM:
    def __init__(self, foreground=True):
        self.foreground = foreground
        self.activated = []

    def find_windows(self, include_minimized=False):
        return [101]

    def get_pid(self, hwnd):
        return 11

    def force_activate_window(self, hwnd, max_wait_sec=1.5, auto_shrink=False):
        self.activated.append(hwnd)
        return self.foreground

    def is_foreground(self, hwnd):
        return self.foreground

    def shrink_all_windows_once(self, windows):
        return 0

    def auto_arrange(self, *a, **k):
        pass

    def get_client_offset(self, hwnd):
        return (7, 9)

    def get_window_title(self, hwnd):
        return "Roblox"


class TrackingInput:
    """Records clicks and tells the vision script when a pet was confirmed."""

    def __init__(self, vision=None, land=True):
        self.clicks = []
        self.vision = vision
        self.land = land
        self._pending_slot = None

    def click(self, x, y, client_offset=None, relative=True, hwnd=None):
        self.clicks.append({"x": x, "y": y, "offset": client_offset, "hwnd": hwnd})
        if not self.land:
            return False
        for s in SLOTS:
            if (x, y) == (s["claim_click"]["x"], s["claim_click"]["y"]):
                if self.vision:
                    self.vision.mark_claimed(s["id"])
        for s in SLOTS:
            if (x, y) == (s["slot_click"]["x"], s["slot_click"]["y"]):
                self._pending_slot = s["id"]
        if (x, y) == (ACTIONS["confirm_yes"]["x"], ACTIONS["confirm_yes"]["y"]):
            if self.vision and self._pending_slot:
                self.vision.mark_filled(self._pending_slot)
                self._pending_slot = None
        return True


def build(vision, wm=None, inp=None, retries=2, verify=True, cycles=1):
    r = object.__new__(main.MacroRunner)
    r.log_callback = r.stats_callback = r.frame_callback = None
    r.max_cycles = cycles
    r.running, r.paused, r.hud = True, False, None
    r.stats = {"cycles": 0, "claims": 0, "adds": 0, "accounts": 0, "active_hwnd": 0}
    r.use_relative = True
    r.auto_arrange = r.auto_shrink = False
    r.layout_style, r.offset_x, r.offset_y, r.grid_cols = "diagonal", 10, 10, 2
    r.config = {"slots": SLOTS, "common_actions": ACTIONS,
                "delays": {k: 0.0 for k in ("window_focus_delay", "between_slots_delay",
                                            "cycle_delay")}}
    r.wm = wm or FakeWM()
    r.vs = vision
    r.input = inp if inp is not None else TrackingInput(vision)
    r.claim_on_timer = False   # this test is about the skip, not the claim
    r.verify_fill = verify
    r.fill_retries = retries
    r.capture_failures = False      # no screenshots from the fakes
    r.dismiss_popups = False        # no pop-up close point in these profiles
    r.d_settle = r.d_after_slot = r.d_after_pet = 0.0
    r.d_after_confirm = r.d_after_claim = 0.0
    r.scheduler = ClaimScheduler(wait_minutes=10,
                                 path=os.path.join(tempfile.mkdtemp(), "s.json"))
    r.log = lambda m: None
    return r


def run():
    # --- 1. All three empty: all three get filled, timer armed. ---
    vis = ScriptedVision({1: SlotState.EMPTY_SLOT, 2: SlotState.EMPTY_SLOT,
                          3: SlotState.EMPTY_SLOT})
    r = build(vis)
    r.run()
    check("all three slots end up filled",
          all(v == SlotState.BUSY_TIMER for v in vis.states.values()), vis.states)
    check("claim timer armed once all slots are filled", not r.scheduler.is_due(11))

    # --- 2. One add silently misses: the sweep catches it and refills. ---
    class MissOnce(ScriptedVision):
        def __init__(self, states):
            super().__init__(states)
            self.misses_left = 1

        def mark_filled(self, slot_id):
            if slot_id == 2 and self.misses_left:
                self.misses_left -= 1   # this add "failed" - slot stays empty
                return
            super().mark_filled(slot_id)

    vis2 = MissOnce({1: SlotState.EMPTY_SLOT, 2: SlotState.EMPTY_SLOT,
                     3: SlotState.EMPTY_SLOT})
    r2 = build(vis2)
    r2.run()
    check("a missed add is retried and filled",
          vis2.states[2] == SlotState.BUSY_TIMER, vis2.states)
    check("timer armed after the retry filled it", not r2.scheduler.is_due(11))

    # --- 3. A slot that can never be filled: no timer, so the next cycle retries. ---
    vis3 = ScriptedVision({1: SlotState.EMPTY_SLOT, 2: SlotState.EMPTY_SLOT,
                           3: SlotState.EMPTY_SLOT}, fill_on_add=False)
    r3 = build(vis3, retries=2)
    r3.run()
    check("no claim timer on a half-filled account", r3.scheduler.is_due(11))

    # --- 4. Verification off: behaves as before, no sweep. ---
    vis4 = ScriptedVision({1: SlotState.EMPTY_SLOT, 2: SlotState.EMPTY_SLOT,
                           3: SlotState.EMPTY_SLOT}, fill_on_add=False)
    r4 = build(vis4, retries=2, verify=False)
    r4.run()
    check("timer armed when verification is switched off", not r4.scheduler.is_due(11))

    # --- 5. Window not in front: not a single click is sent. ---
    vis5 = ScriptedVision({1: SlotState.EMPTY_SLOT, 2: SlotState.EMPTY_SLOT,
                           3: SlotState.EMPTY_SLOT})
    inp5 = TrackingInput(vis5)
    r5 = build(vis5, wm=FakeWM(foreground=False), inp=inp5)
    r5.run()
    check("no clicks while the window is not in front", inp5.clicks == [],
          inp5.clicks)

    # --- 6. Cursor could not be placed: the add path stops instead of guessing. ---
    vis6 = ScriptedVision({1: SlotState.EMPTY_SLOT, 2: SlotState.BUSY_TIMER,
                           3: SlotState.BUSY_TIMER})
    inp6 = TrackingInput(vis6, land=False)
    r6 = build(vis6, inp=inp6, retries=1)
    r6.run()
    slot_clicks = [c for c in inp6.clicks
                   if (c["x"], c["y"]) == (100, 100)]
    pet_clicks = [c for c in inp6.clicks
                  if (c["x"], c["y"]) == (400, 400)]
    check("a failed cursor placement stops the add path",
          len(slot_clicks) >= 1 and pet_clicks == [], inp6.clicks)

    # --- 7. Clicks carry the freshly read client offset. ---
    vis7 = ScriptedVision({1: SlotState.EMPTY_SLOT, 2: SlotState.BUSY_TIMER,
                           3: SlotState.BUSY_TIMER})
    inp7 = TrackingInput(vis7)
    r7 = build(vis7, inp=inp7)
    r7.run()
    check("every click uses the window's client offset",
          all(c["offset"] == (7, 9) for c in inp7.clicks), inp7.clicks[:3])
    check("every click targets the right window",
          all(c["hwnd"] == 101 for c in inp7.clicks))

    # --- 8. Coordinate scaling: identity at reference size, scaled below it. ---
    r8 = build(ScriptedVision({}))
    ident = scaling.CoordinateMapper((800, 600))
    half = scaling.CoordinateMapper((400, 300))
    check("reference client needs no scaling", ident.is_identity)
    same = r8._map_slot(SLOTS[0], ident)
    check("identity mapping leaves points untouched",
          same["slot_click"] == {"x": 100, "y": 100}, same["slot_click"])
    scaled = r8._map_slot(SLOTS[0], half)
    check("smaller client scales the click point",
          scaled["slot_click"] == {"x": 50, "y": 50}, scaled["slot_click"])
    check("smaller client scales the scan region",
          scaled["scan_region"] == {"x": 0, "y": 0, "width": 5, "height": 5},
          scaled["scan_region"])
    acts = r8._map_actions(ACTIONS, half)
    check("pop-up buttons scale too", acts["confirm_yes"] == {"x": 250, "y": 250},
          acts["confirm_yes"])
    check("original slot dict is not mutated", SLOTS[0]["slot_click"] == {"x": 100, "y": 100})

    # --- 8b. A missed CLAIM is caught before moving to the next tab. ---
    class ClaimMissesOnce(ScriptedVision):
        """The first claim click does nothing - the pet stays ready."""

        def __init__(self, states):
            super().__init__(states)
            self.misses_left = 1

        def mark_claimed(self, slot_id):
            self.last_claim_ok = not self.misses_left
            if self.misses_left:
                self.misses_left -= 1
                return              # click "landed" but the claim never took
            self.claimed.append(slot_id)
            self.states[slot_id] = SlotState.EMPTY_SLOT

    visC = ClaimMissesOnce({1: SlotState.CLAIM_READY, 2: SlotState.BUSY_TIMER,
                            3: SlotState.BUSY_TIMER})
    rC = build(visC)
    rC.run()
    check("a missed claim is retried by the sweep", visC.claimed == [1], visC.claimed)
    check("the reclaimed slot is refilled",
          visC.states[1] == SlotState.BUSY_TIMER, visC.states)
    check("timer armed once nothing is pending", not rC.scheduler.is_due(11))

    # --- 8c. A claim that never works: no timer, so the next cycle retries. ---
    visD = ScriptedVision({1: SlotState.CLAIM_READY, 2: SlotState.BUSY_TIMER,
                           3: SlotState.BUSY_TIMER}, claim_works=False)
    rD = build(visD, retries=2)
    rD.run()
    check("slot still claimable is not left behind quietly",
          visD.states[1] == SlotState.CLAIM_READY, visD.states)
    check("no timer while a claim is still pending", rD.scheduler.is_due(11))

    # --- 8d. The sweep alone can arm the timer (main pass found nothing). ---
    class ReadyAfterFirstRead(ScriptedVision):
        """Everything looks busy on the first pass; one slot is ready by the sweep."""

        def __init__(self, states):
            super().__init__(states)
            self.first_pass_reads = 3

        def analyze_slot_advanced(self, img, slot):
            if self.first_pass_reads > 0:
                self.first_pass_reads -= 1
                return SlotState.BUSY_TIMER, "scripted busy", None
            return super().analyze_slot_advanced(img, slot)

    visE = ReadyAfterFirstRead({1: SlotState.CLAIM_READY, 2: SlotState.BUSY_TIMER,
                                3: SlotState.BUSY_TIMER})
    rE = build(visE)
    rE.run()
    check("sweep claims work the main pass missed", visE.claimed == [1], visE.claimed)
    check("sweep-only work still arms the timer", not rE.scheduler.is_due(11))

    # --- 9. An unreadable client size must not collapse coordinates to zero. ---
    r9 = build(ScriptedVision({}))
    r9.config["reference_client"] = {"width": 800, "height": 600}
    mapper = r9._mapper(0)          # not a real window
    check("bogus client size falls back to identity", mapper.is_identity,
          mapper.client_size)

    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())


def test_legacy_checks():
    """Run every check in this script under pytest; a recorded failure fails the test."""
    assert run() == 0, failures
