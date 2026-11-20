"""The Memory service: trim clients over a limit, safely (spec 001, US3, FR-020).

No Tk and no Win32: trimming, process lookups, the clock and events arrive as arguments, so
every rule is unit-tested (tests/unit/test_memory_service.py). Rules that fix the AutoHotkey
version: the allowed range is enforced in exactly one place, a process is checked to still be
the same program before it is touched (pids get reused), and a refused trim is reported once,
not every few seconds.
"""

import threading
from dataclasses import dataclass, field

from macro_studio.core.processes import same_process

MIN_THRESHOLD_MB = 50
MAX_THRESHOLD_MB = 8000
OVER_REPEAT_S = 15 * 60          # the memory alert repeats for a client at most this often
BYTES_PER_MB = 2**20


@dataclass(frozen=True)
class Trimmed:
    pid: int
    before_mb: float
    after_mb: float


@dataclass
class TrimReport:
    trimmed: list = field(default_factory=list)
    skipped: list = field(default_factory=list)          # (pid, reason)

    @property
    def freed_mb(self):
        return sum(max(0.0, t.before_mb - t.after_mb) for t in self.trimmed)


class MemoryService:
    def __init__(self, trim, process_info, publish, clock, log, threshold_mb=350,
                 trim_all=False, enabled=False):
        self._threshold = MIN_THRESHOLD_MB
        self.threshold_mb = threshold_mb
        self.trim_all, self.enabled = trim_all, enabled
        self._trim, self._process_info = trim, process_info
        self._publish, self._clock, self._log = publish, clock, log
        self._rows = []
        self._lock = threading.Lock()
        self._last_over = {}               # pid -> when ram_over was last published
        self._refused = set()              # pids already reported as refused

    # ------------------------------------------------------------ settings
    @property
    def threshold_mb(self):
        return self._threshold

    @threshold_mb.setter
    def threshold_mb(self, value):
        if not MIN_THRESHOLD_MB <= value <= MAX_THRESHOLD_MB:
            raise ValueError(f"The limit must be between {MIN_THRESHOLD_MB} and "
                             f"{MAX_THRESHOLD_MB} MB.")
        self._threshold = value

    # ------------------------------------------------------------ input
    def set_rows(self, rows):
        """The latest client list (called from whichever thread scanned)."""
        with self._lock:
            self._rows = list(rows)
        ids = {r.pid for r in rows}
        self._refused &= ids
        for pid in set(self._last_over) - ids:
            del self._last_over[pid]

    def _snapshot(self):
        with self._lock:
            return list(self._rows)

    # ------------------------------------------------------------ trimming
    def run_auto(self):
        """The scheduled pass: nothing unless automatic trimming is on."""
        return self.trim_now() if self.enabled else None

    def trim_now(self):
        """Trim every client over the limit (every client if 'trim all'), on demand."""
        report = TrimReport()
        for row in self._snapshot():
            if self.trim_all or row.ram_mb >= self._threshold:
                self._trim_one(row, report)
        if report.trimmed:
            self._log(f"[MEMORY] Trimmed {len(report.trimmed)} client(s), freed "
                      f"{report.freed_mb:,.0f} MB.")
        return report

    def _trim_one(self, row, report):
        current = self._process_info(row.pid)
        if current is None:
            report.skipped.append((row.pid, "already closed"))
            return
        if not same_process(row, current):
            report.skipped.append((row.pid, "no longer the same program"))
            return
        try:
            self._trim(row.pid)
        except OSError:
            report.skipped.append((row.pid, "Windows refused"))
            if row.pid not in self._refused:
                self._refused.add(row.pid)
                self._log(f"[MEMORY] Windows refused to trim PID {row.pid}; skipping it.")
            return
        after = self._process_info(row.pid)
        after_mb = after.rss_bytes / BYTES_PER_MB if after is not None else 0.0
        report.trimmed.append(Trimmed(row.pid, row.ram_mb, after_mb))

    # ------------------------------------------------------------ the alert
    def check_limit(self):
        """Publish `ram_over` for clients over the limit, each at most every 15 minutes."""
        now = self._clock()
        fresh = [row for row in self._snapshot()
                 if row.ram_mb >= self._threshold
                 and now - self._last_over.get(row.pid, -OVER_REPEAT_S - 1) > OVER_REPEAT_S]
        if not fresh:
            return
        for row in fresh:
            self._last_over[row.pid] = now
        self._publish("ram_over", rows=fresh, threshold_mb=self._threshold)
