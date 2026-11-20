"""Checks the Goliath per-account claim scheduler and its isolation from mutation."""

import os
import tempfile
import time

from claim_scheduler import ClaimScheduler

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


def run():
    path = os.path.join(tempfile.mkdtemp(), "account_schedule.json")
    s = ClaimScheduler(wait_minutes=10, path=path)

    # An account never seen before must be scanned, not skipped.
    check("unknown pid is due", s.is_due(4321))

    rec = s.mark_pet_added(4321, hwnd=99, label="Account #1", title="Roblox")
    check("timer armed after add", not s.is_due(4321))
    remaining = s.seconds_remaining(4321)
    check("wait is ~10 minutes", 595 <= remaining <= 600, remaining)
    check("next_claim string written", bool(rec["next_claim"]), rec)
    check("keyed by pid", "4321" in s.records, list(s.records))

    # Other accounts are unaffected - each PID has its own clock.
    check("other pid still due", s.is_due(8888))

    # Persisted across restarts.
    s2 = ClaimScheduler(wait_minutes=10, path=path)
    check("timer survives reload", not s2.is_due(4321))
    check("reload keeps label", s2.records["4321"]["label"] == "Account #1")

    # A claim clears the timer; the next add re-arms it.
    s2.mark_claimed(4321)
    check("claim clears timer", s2.is_due(4321))

    # An expired timer reads as due.
    s3 = ClaimScheduler(wait_minutes=0, path=os.path.join(tempfile.mkdtemp(), "s.json"))
    s3.mark_pet_added(7, hwnd=1)
    check("zero wait is immediately due", s3.is_due(7))

    # Closed clients are pruned so a relaunch is not blocked by a dead PID.
    s2.mark_pet_added(4321)
    s2.mark_pet_added(5555)
    pruned = s2.prune([4321])
    check("stale pid pruned", pruned == 1 and "5555" not in s2.records, list(s2.records))

    # Disabled means the runner never skips anything.
    s4 = ClaimScheduler(wait_minutes=10, enabled=False,
                        path=os.path.join(tempfile.mkdtemp(), "s.json"))
    s4.mark_pet_added(3)
    check("disabled scheduler never blocks", s4.is_due(3))

    # from_config reads the Goliath block; a mutation profile has none and
    # falls back to defaults without ever being consulted by MutationRunner.
    s5 = ClaimScheduler.from_config({"claim_schedule": {"wait_minutes": 2, "enabled": True}})
    s5.path = os.path.join(tempfile.mkdtemp(), "s.json")
    s5.mark_pet_added(9)
    check("from_config honours wait_minutes", 115 <= s5.seconds_remaining(9) <= 120,
          s5.seconds_remaining(9))

    # The mutation runner must not carry any claim-scheduler wiring.
    import mutation_runner
    src = open(mutation_runner.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    check("mutation runner stays independent",
          "ClaimScheduler" not in src and "claim_schedule" not in src)

    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())


def test_legacy_checks():
    """Run every check in this script under pytest; a recorded failure fails the test."""
    assert run() == 0, failures
