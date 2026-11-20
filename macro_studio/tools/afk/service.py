"""The Anti-AFK service: rounds of key presses so clients are not kicked for idling (US5).

No Tk and no Win32: everything it touches arrives through `io` (windows, focus, key and mouse
sending, idle time), the automation lock, a stop-aware `wait`, a clock and a random source, so
every rule is unit-tested (tests/unit/test_afk_service.py).

Safety rules (Constitution IV): nothing is sent while a macro runner owns the input, nothing
after the emergency stop, and a stop in the middle of a round ends it at once. The stop keys
(F6, F7, Esc) can never be sent, because the studio's own hotkeys would react to them.
"""

from dataclasses import dataclass, field

from macro_studio.tools.afk.keys import BadKey

RESERVED_VKS = {0x75, 0x76, 0x1B}            # F6, F7, Esc
NONE_ALERT_EVERY_S = 30 * 60
MIN_DELAY_S = 3.0
SETTLE_S = 0.12                              # after bringing a window forward
GAP_S = (0.06, 0.14)                         # between presses
HOLD_VARIATION_MS = (-15, 25)
MIN_HOLD_MS = 20
DEFER_S = 15.0                               # when the player is active


@dataclass
class Report:
    sent: int = 0
    skipped: list = field(default_factory=list)       # (pid, reason)
    deferred: bool = False
    note: str = ""


class AfkService:
    def __init__(self, io, automation, wait, clock, rng, publish, log, interval_s=60,
                 jitter_s=5, hold_ms=80, idle_guard=False, idle_secs=30, restore_focus=True,
                 skip_minimised=True, enabled=False):
        self._io, self._automation, self._wait = io, automation, wait
        self._clock, self._rng, self._publish, self._log = clock, rng, publish, log
        self.interval_s, self.jitter_s, self.hold_ms = interval_s, jitter_s, hold_ms
        self.idle_guard, self.idle_secs = idle_guard, idle_secs
        self.restore_focus, self.skip_minimised, self.enabled = restore_focus, skip_minimised, enabled
        self._tokens = []
        self._last_none = None

    def set_tokens(self, tokens):
        for token in tokens:
            if token.vk in RESERVED_VKS:
                raise BadKey("F6, F7 and Esc are the studio's stop and pause keys and cannot be sent.")
        self._tokens = list(tokens)

    # ------------------------------------------------------------ timing
    def next_delay(self):
        spread = self._rng.uniform(-self.jitter_s, self.jitter_s)
        return max(MIN_DELAY_S, self.interval_s + spread)

    def run_scheduled(self):
        """The timed round: nothing unless the tool is switched on."""
        return self.run_round() if self.enabled else None

    # ------------------------------------------------------------ one round
    def _stopped(self):
        return self._automation.stop_event.is_set()

    def run_round(self):
        """Send the key sequence to every client once. Also used by "Send now"."""
        report = Report()
        if not self._tokens:
            report.note = "No keys are set."
            return report
        if not self._automation.try_borrow("afk"):
            report.note = "A macro is running, so anti-AFK waits."
            return report
        if self._stopped():
            report.note = "Stopped."
            return report
        if self.idle_guard and self._io.idle_seconds() < self.idle_secs:
            report.deferred = True
            report.note = "You are using the PC; trying again shortly."
            return report
        windows = sorted(self._io.windows(), key=lambda w: (w[1], w[0]))      # by pid
        if not windows:
            self._no_clients()
            report.note = "No Roblox client is open."
            return report

        before = self._io.foreground() if self.restore_focus else None
        for hwnd, pid, minimised in windows:
            if self._stopped():
                break
            if self._visit(hwnd, pid, minimised, report) is False:
                break
        if before and not self._stopped():
            self._io.restore_foreground(before)
        return report

    def _no_clients(self):
        now = self._clock()
        if self._last_none is None or now - self._last_none >= NONE_ALERT_EVERY_S:
            self._last_none = now
            self._log("[AFK] No Roblox client is open, so nothing was sent.")
            self._publish("afk_none")

    def _visit(self, hwnd, pid, minimised, report):
        """One client. False means the round was stopped and should end."""
        if minimised and self.skip_minimised:
            report.skipped.append((pid, "minimised"))
            return True
        if minimised:
            self._io.restore(hwnd)
        if not self._io.activate(hwnd):
            report.skipped.append((pid, "could not be brought to the front"))
            return True
        if not self._wait(SETTLE_S):
            return False
        for index, token in enumerate(self._tokens):
            if self._stopped():
                return False
            if token.kind == "mouse":
                self._io.mouse(hwnd, token.name)
            else:
                hold_ms = max(MIN_HOLD_MS, self.hold_ms + self._rng.randint(*HOLD_VARIATION_MS))
                self._io.key(token.vk, hold_ms / 1000.0)
            if index < len(self._tokens) - 1 and not self._wait(self._rng.uniform(*GAP_S)):
                return False
        report.sent += 1
        if minimised:
            self._io.minimise(hwnd)
        return True
