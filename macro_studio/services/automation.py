"""Who owns the mouse and keyboard, and the emergency stop (Constitution IV).

A macro runner acquires the lock for its whole run. Tools that move windows or send
input (layouts, anti-AFK) borrow it only when no runner holds it, so automations never
fight each other. The emergency stop sets one shared event that input code checks right
before sending, then tells every registered stoppable to stop.
"""

import threading
import time

# Hotkey action names shared by the app shell and the runners, so both bind the same keys
# to the same actions instead of conflicting.
STOP_BINDINGS = (("emergency_stop", "F6"), ("emergency_stop_esc", "Esc"))
PAUSE_BINDING = ("pause_resume", "F7")


class AutomationLock:
    #: After an emergency stop the owner keeps the lock until it has really ended, so a new
    #: run cannot start while the old one may still be sending input. A runner that has not
    #: ended after this long is given up on (and reported) so it cannot block everything.
    STOP_GRACE_S = 3.0

    def __init__(self, on_error=None, clock=time.monotonic):
        self._on_error = on_error or (lambda obj, exc: None)
        self._clock = clock
        self._stop_requested_at = None
        self._owner = None
        self._stoppables = []
        self._lock = threading.Lock()
        self.stop_event = threading.Event()

    @property
    def owner(self):
        return self._owner

    @property
    def stopping(self):
        """True while an owner is still winding down after an emergency stop."""
        return self._owner is not None and self._stop_requested_at is not None

    def acquire(self, owner):
        """Take ownership for a run. Starting a run clears a previous emergency stop."""
        with self._lock:
            if self._owner is not None:
                waited = (self._clock() - self._stop_requested_at
                          if self._stop_requested_at is not None else None)
                if waited is None or waited <= self.STOP_GRACE_S:
                    return False
                stuck = self._owner
                self._on_error(stuck, TimeoutError(
                    f"'{stuck}' did not end {waited:.1f}s after the emergency stop; "
                    f"starting '{owner}' anyway."))
            self._owner = owner
            self._stop_requested_at = None
            self.stop_event.clear()
            return True

    def release(self, owner):
        with self._lock:
            if self._owner == owner:
                self._owner = None
                self._stop_requested_at = None

    def try_borrow(self, tool):
        """True when no runner owns input, so `tool` may move windows or send input.

        This answers ownership only. Whether input may be sent right now is the job
        of stop_event, which input code checks immediately before every send.
        """
        with self._lock:
            return self._owner is None

    def clear_stop(self):
        """Lift a previous emergency stop when the user explicitly restarts a tool."""
        self.stop_event.clear()

    def register_stoppable(self, obj):
        with self._lock:
            if obj not in self._stoppables:
                self._stoppables.append(obj)

    def unregister_stoppable(self, obj):
        with self._lock:
            if obj in self._stoppables:
                self._stoppables.remove(obj)

    def emergency_stop(self):
        self.stop_event.set()
        with self._lock:
            stoppables = list(self._stoppables)
            # The owner stays until it releases itself (see STOP_GRACE_S).
            if self._owner is not None and self._stop_requested_at is None:
                self._stop_requested_at = self._clock()
        for obj in stoppables:
            try:
                obj.on_emergency_stop()
            except Exception as exc:
                self._on_error(obj, exc)
