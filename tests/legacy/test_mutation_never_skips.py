"""Proves the Goliath claim timer can never hold back the mutation loop.

The claim timer exists so Goliath stops re-checking an account whose pets were
just put in. Mutation has no claim step at all, so that skip must not reach it:
every account has to be visited on every pass, even when a schedule file says
those exact processes are "waiting".

This test therefore does two things at once - it fills account_schedule.json
with future timers for the very PIDs under test, and it makes get_pid() blow up
if the mutation runner ever asks for a process id at all.
"""

import os
import tempfile

import mutation_runner
from claim_scheduler import ClaimScheduler
from mutation_runner import STATE_PET_LIST, MutationRunner, WindowState

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


class FakeMapper:
    is_identity = True

    def point(self, x, y):
        return x, y

    def describe(self):
        return "identity"


class FakeWM:
    def __init__(self):
        self.activated = []
        self.pid_calls = 0

    def force_activate_window(self, hwnd, *a, **k):
        self.activated.append(hwnd)
        return True

    def get_client_offset(self, hwnd):
        return (0, 0)

    def get_window_title(self, hwnd):
        return f"Roblox {hwnd}"

    def get_pid(self, hwnd):
        # Mutation has no per-account gating, so it has no reason to ask.
        self.pid_calls += 1
        raise AssertionError("mutation runner asked for a PID - scheduling leak?")


class FakeInput:
    def __init__(self):
        self.clicks = []

    def click(self, x, y, client_offset=None, relative=True, hwnd=None):
        self.clicks.append((hwnd, x, y))


def build_runner(max_cycles=1):
    r = object.__new__(MutationRunner)
    r.log_callback = None
    r.stats_callback = None
    r.frame_callback = None
    r.max_cycles = max_cycles
    r.running = True
    r.paused = False
    r.hud = None
    r.stats = {"cycles": 0, "claims": 0, "adds": 0, "accounts": 0, "active_hwnd": 0}
    r.config = {"meta": {"macro_mode": "mutation"}}
    r.mutation_cfg = {
        "targets": {"pet_select": {"x": 5, "y": 5}, "mutate": {"x": 9, "y": 9}},
        "scan_region": {"x": 0, "y": 0, "width": 4, "height": 4},
    }
    r.focus_delay = 0.0
    r.after_pet_delay = 0.0
    r.after_mutate_delay = 0.0
    r.poll_delay = 0.0
    r.wm = FakeWM()
    r.input = FakeInput()
    r.log = lambda msg: None
    r._mapper = lambda hwnd: FakeMapper()
    # Wheel never appears: start_mutation gives up quickly and the steady-state
    # pass reads "pet list", i.e. every account is worked on every round.
    r.read_state = lambda hwnd, mapper: (STATE_PET_LIST, 50.0, 10.0, None)
    r.windows = [WindowState(101, "Account #1"), WindowState(102, "Account #2")]
    r._discover_windows = lambda: r.windows
    return r


def run():
    # Arm Goliath-style timers for the same processes the mutation run uses.
    sched_path = os.path.join(tempfile.mkdtemp(), "account_schedule.json")
    goliath_sched = ClaimScheduler(wait_minutes=60, path=sched_path)
    goliath_sched.mark_pet_added(101, hwnd=101, label="Account #1")
    goliath_sched.mark_pet_added(102, hwnd=102, label="Account #2")
    check("goliath timers are active", not goliath_sched.is_due(101))

    r = build_runner(max_cycles=1)
    r.run()

    clicked = {hwnd for hwnd, _, _ in r.input.clicks}
    check("mutation clicks every account despite timers", clicked == {101, 102}, clicked)
    check("mutation focuses every account", set(r.wm.activated) == {101, 102},
          r.wm.activated)
    check("mutation never asks for a PID", r.wm.pid_calls == 0, r.wm.pid_calls)
    check("both accounts clicked more than once (first pass + round)",
          len(r.input.clicks) >= 4, r.input.clicks)

    # The schedule file must be untouched by a mutation run.
    after = ClaimScheduler(wait_minutes=60, path=sched_path)
    check("mutation does not write the schedule file",
          sorted(after.records) == ["101", "102"], list(after.records))
    check("mutation does not clear goliath timers", not after.is_due(101))

    # And the module itself carries none of the scheduling vocabulary.
    src = open(mutation_runner.__file__, encoding="utf-8").read()
    for word in ("ClaimScheduler", "claim_schedule", "account_schedule",
                 "is_due", "seconds_remaining"):
        check(f"mutation_runner has no '{word}'", word not in src)

    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())


def test_legacy_checks():
    """Run every check in this script under pytest; a recorded failure fails the test."""
    assert run() == 0, failures
