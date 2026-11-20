"""
Per-account claim scheduler - GOLIATH MODE ONLY.

The Goliath daycare loop used to re-focus and re-scan every Roblox client on
every cycle, even though a pet that was just added cannot possibly be ready
for minutes. This module remembers, per account, when its pets were last put
in, and tells the runner to leave that account alone until the timer is up.

Accounts are keyed by PID rather than HWND: a window handle changes if the
client is relaunched, but while a client lives its PID is stable, and that is
exactly the lifetime a claim timer is meaningful for.

The mutation loop deliberately does not use any of this - it has no claim step
and its own pacing - and nothing here imports MutationRunner or its config, so
the two macros cannot fight over state. The schedule lives in its own file
(account_schedule.json) for the same reason.
"""

import json
import os
import time

from macro_studio.core import scaling
from macro_studio.core.fileio import write_json_atomic

SCHEDULE_FILENAME = "account_schedule.json"
DEFAULT_WAIT_MINUTES = 10.0


class ClaimScheduler:
    """Tracks 'this account is not claimable until T' per Roblox process."""

    def __init__(self, wait_minutes=DEFAULT_WAIT_MINUTES, enabled=True,
                 path=None, log=None, clock=time.time):
        self.enabled = bool(enabled)
        self.wait_seconds = max(0.0, float(wait_minutes) * 60.0)
        self.path = path or os.path.join(scaling.get_base_dir(), SCHEDULE_FILENAME)
        self._log = log
        self._clock = clock
        self.records = self._load()

    # ---------------- persistence ----------------
    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save(self):
        try:
            write_json_atomic(self.path, self.records)
        except (OSError, TypeError, ValueError) as err:
            # A failed save must not stop the macro: the timers stay in memory and the old
            # file stays whole. Say so, because after a restart the timers would be stale.
            if self._log:
                self._log(f"[SCHEDULE] Could not save the claim timers: {err}")

    # ---------------- queries ----------------
    def seconds_remaining(self, pid):
        """Seconds until this account may be claimed again (0 = claim now).

        An account with no record has never been served this run, so it is due
        immediately - that is what makes the very first cycle scan everything.
        """
        if not self.enabled:
            return 0.0
        rec = self.records.get(str(pid))
        if not rec:
            return 0.0
        due_at = rec.get("next_claim_timestamp")
        if not due_at:
            return 0.0
        return max(0.0, float(due_at) - self._clock())

    def slot_claimable(self, pid, slot_id):
        """False while a pet this macro put into the slot is younger than the wait.

        A Goliath takes the full wait to grow, so a pet added seconds ago cannot be
        ready whatever the screen seems to show. Without this, the post-add sweep read
        three fresh pets as CLAIM_READY and claimed them straight back out (real run,
        2026-11-19). Slots this macro never filled have no record and may be claimed.
        """
        rec = self.records.get(str(pid)) or {}
        added_at = (rec.get("slots_added") or {}).get(str(slot_id))
        if not added_at:
            return True
        return self._clock() - float(added_at) >= self.wait_seconds

    def slot_wait(self, pid, slot_id):
        """'7m 03s' left before this slot's pet can be claimed."""
        rec = self.records.get(str(pid)) or {}
        added_at = float((rec.get("slots_added") or {}).get(str(slot_id)) or 0)
        remaining = int(round(max(0.0, added_at + self.wait_seconds - self._clock())))
        return f"{remaining // 60}m {remaining % 60:02d}s"

    def is_due(self, pid):
        return self.seconds_remaining(pid) <= 0.0

    def describe_wait(self, pid):
        """'4m 12s' style string for the log line."""
        remaining = int(round(self.seconds_remaining(pid)))
        return f"{remaining // 60}m {remaining % 60:02d}s"

    # ---------------- updates ----------------
    def _record(self, pid, hwnd=None, label=None, title=None):
        rec = self.records.setdefault(str(pid), {})
        rec["pid"] = int(pid)
        if hwnd is not None:
            rec["hwnd"] = int(hwnd)
        if label:
            rec["label"] = label
        if title:
            rec["title"] = title
        return rec

    def mark_slot_added(self, pid, slot_id):
        """Remember when this macro put a pet into one slot (see slot_claimable)."""
        rec = self._record(pid)
        rec.setdefault("slots_added", {})[str(slot_id)] = self._clock()
        self.save()

    def mark_pet_added(self, pid, hwnd=None, label=None, title=None):
        """A pet went into a slot: this account is off the clock for wait_minutes."""
        now = self._clock()
        rec = self._record(pid, hwnd, label, title)
        rec["last_action"] = time.strftime("%H:%M:%S", time.localtime(now))
        rec["last_action_timestamp"] = now
        rec["next_claim_timestamp"] = now + self.wait_seconds
        rec["next_claim"] = time.strftime(
            "%H:%M:%S", time.localtime(rec["next_claim_timestamp"]))
        self.save()
        return rec

    def mark_claimed(self, pid, hwnd=None, label=None, title=None):
        """A claim landed. The timer restarts when the replacement pet goes in."""
        now = self._clock()
        rec = self._record(pid, hwnd, label, title)
        rec["last_claim"] = time.strftime("%H:%M:%S", time.localtime(now))
        rec["last_claim_timestamp"] = now
        rec["next_claim_timestamp"] = 0
        rec["next_claim"] = ""
        self.save()
        return rec

    def forget(self, pid):
        """Drop an account (client closed), so a relaunch starts clean."""
        if self.records.pop(str(pid), None) is not None:
            self.save()

    def prune(self, live_pids):
        """Remove records for processes that are no longer running."""
        live = {str(p) for p in live_pids}
        stale = [k for k in self.records if k not in live]
        for k in stale:
            del self.records[k]
        if stale:
            self.save()
        return len(stale)

    @classmethod
    def from_config(cls, config, log=None):
        """Build from a Goliath profile's `claim_schedule` block (or defaults)."""
        cfg = (config or {}).get("claim_schedule", {})
        return cls(
            wait_minutes=cfg.get("wait_minutes", DEFAULT_WAIT_MINUTES),
            enabled=cfg.get("enabled", True),
            log=log,
        )
