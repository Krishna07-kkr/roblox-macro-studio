"""Roblox clients: each client's own log, who is playing, which game (spec 001, US2).

No Tk, no Win32 and no network in here: logs are read from a folder, names come through an
`http` object with `get(url, timeout)`, and time comes from a clock argument, so every rule is
unit-tested with fixtures and fakes. The rules come from RobloxMultiTool_fixed.ahk, with its
known defects fixed (names are parsed as real JSON, so \\uXXXX escapes work).
"""

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, UTC

LOG_TIME = re.compile(r"(\d{8})T(\d{6})Z")
# "game_join_loadtime ... userid:123" on one line, else any "userid" field.
USER_ON_JOIN_LINE = re.compile(r"game_join_loadtime.*?userid\W{0,3}(\d+)", re.IGNORECASE)
USER_ANYWHERE = re.compile(r"\buserid\W{0,3}(\d+)", re.IGNORECASE)
PLACE = re.compile(r"\bplace(?:id)?\W{0,3}(\d{4,})", re.IGNORECASE)

#: A client's own log begins this many seconds before / after its process started.
LOG_WINDOW_BEFORE_S = 15
LOG_WINDOW_AFTER_S = 60
#: The fallback only looks at logs written to this recently, and not too far from the start.
FALLBACK_MAX_AGE_S = 1200
FALLBACK_MAX_GAP_S = 900
MISSES_BEFORE_FALLBACK = 3


# ------------------------------------------------------------------ log files
@dataclass(frozen=True)
class LogFile:
    path: str
    started: datetime            # from the file name, UTC
    modified: float = 0.0        # file modification time (epoch seconds)


def log_start_time(filename):
    """The UTC time a log file name says it started, or None."""
    found = LOG_TIME.search(os.path.basename(filename))
    if not found:
        return None
    return datetime.strptime("".join(found.groups()), "%Y%m%d%H%M%S").replace(tzinfo=UTC)


def default_log_folder():
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "Roblox", "logs")


def list_logs(folder):
    """The player logs in `folder` (not Studio or crash logs), as LogFile entries."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    logs = []
    for name in names:
        lowered = name.lower()
        if not lowered.endswith("_last.log") or "studio" in lowered or "crashhandler" in lowered:
            continue
        started = log_start_time(name)
        if started is None:
            continue
        path = os.path.join(folder, name)
        try:
            modified = os.path.getmtime(path)
        except OSError:
            continue
        logs.append(LogFile(path=path, started=started, modified=modified))
    return logs


# ------------------------------------------------------------------ pairing
def pair_log(process_started, logs, claimed):
    """The unclaimed log that started closest to the process, within the allowed window."""
    best, best_gap = None, None
    for entry in logs:
        if entry.path in claimed:
            continue
        delta = (entry.started - process_started).total_seconds()
        if -LOG_WINDOW_BEFORE_S <= delta <= LOG_WINDOW_AFTER_S:
            gap = abs(delta)
            if best is None or gap < best_gap:
                best, best_gap = entry, gap
    return best


def pair_log_fallback(process_started, logs, claimed, now):
    """The closest recently written unclaimed log, for a client that found no exact match."""
    best, best_gap = None, None
    for entry in logs:
        if entry.path in claimed or now - entry.modified > FALLBACK_MAX_AGE_S:
            continue
        gap = abs((entry.started - process_started).total_seconds())
        if gap <= FALLBACK_MAX_GAP_S and (best is None or gap < best_gap):
            best, best_gap = entry, gap
    return best


class LogPairer:
    """Gives every client its own log and never lets two clients share one."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self.claimed = {}              # log path -> pid
        self._by_pid = {}
        self._misses = {}

    def pair(self, pid, process_started, logs):
        if pid in self._by_pid:
            return self._by_pid[pid]
        taken = set(self.claimed)
        entry = pair_log(process_started, logs, taken)
        if entry is None:
            if self._misses.get(pid, 0) >= MISSES_BEFORE_FALLBACK:
                entry = pair_log_fallback(process_started, logs, taken, self._clock())
            else:
                self._misses[pid] = self._misses.get(pid, 0) + 1
        if entry is not None:
            self.claimed[entry.path] = pid
            self._by_pid[pid] = entry
        return entry

    def release(self, pid):
        entry = self._by_pid.pop(pid, None)
        if entry is not None:
            self.claimed.pop(entry.path, None)
        self._misses.pop(pid, None)


# ------------------------------------------------------------------ reading a log
def parse_user_id(head_text):
    """The account id from the start of a client log, or None."""
    for pattern in (USER_ON_JOIN_LINE, USER_ANYWHERE):
        found = pattern.search(head_text or "")
        if found:
            return int(found.group(1))
    return None


def parse_place_id(tail_text):
    """The most recent place id in the end of a client log, or None."""
    last = None
    for found in PLACE.finditer(tail_text or ""):
        last = int(found.group(1))
    return last


class PlaceTracker:
    """A changed game counts only after it is seen twice in a row (log lines can be stale)."""

    def __init__(self):
        self.current = None
        self._pending = None

    def update(self, candidate):
        if candidate is None:
            return self.current
        if self.current is None:
            self.current = candidate
        elif candidate == self.current:
            self._pending = None
        elif candidate == self._pending:
            self.current, self._pending = candidate, None
        else:
            self._pending = candidate
        return self.current


# ------------------------------------------------------------------ names from Roblox
USER_URL = "https://users.roblox.com/v1/users/{}"
UNIVERSE_URL = "https://apis.roblox.com/universes/v1/places/{}/universe"
GAME_URL = "https://games.roblox.com/v1/games?universeIds={}"
LOOKUP_ERRORS = (OSError, ValueError, KeyError, TypeError, IndexError)


class _Job:
    __slots__ = ("attempts", "next_try", "order", "placeholder", "value")

    def __init__(self, order):
        self.order, self.attempts, self.next_try = order, 0, 0.0
        self.value, self.placeholder = None, False


class NameLookup:
    """Looks up account and game names one at a time, with backoff.

    After a failure it retries in 2.5 s, then 5 s; after the third failure it shows a
    placeholder ("id N" / "place N") and tries again every 60 s. Never blocks: `tick()` runs
    at most one request and is called from a worker thread.
    """

    RETRY_DELAYS = (2.5, 5.0)
    PLACEHOLDER_RETRY_S = 60.0
    TIMEOUT_S = 3.0

    def __init__(self, http, clock=time.monotonic):
        self._http, self._clock = http, clock
        self._jobs = {}
        self._counter = 0

    # -------------------------------------------------- what is wanted
    def _want(self, kind, key):
        if (kind, key) not in self._jobs:
            self._counter += 1
            self._jobs[(kind, key)] = _Job(self._counter)

    def want_user(self, user_id):
        self._want("user", user_id)

    def want_place(self, place_id):
        self._want("place", place_id)

    def forget_user(self, user_id):
        self._jobs.pop(("user", user_id), None)

    def forget_place(self, place_id):
        self._jobs.pop(("place", place_id), None)

    # -------------------------------------------------- what is known
    def _label(self, kind, key, placeholder):
        job = self._jobs.get((kind, key))
        if job is None:
            return None
        if job.value:
            return job.value
        return placeholder if job.placeholder else None

    def user_label(self, user_id):
        return self._label("user", user_id, f"id {user_id}")

    def place_label(self, place_id):
        return self._label("place", place_id, f"place {place_id}")

    # -------------------------------------------------- work
    def tick(self):
        """Run the one lookup that is due (oldest first), if any."""
        now = self._clock()
        due = [(job.next_try, job.order, key, job) for key, job in self._jobs.items()
               if job.value is None and job.next_try <= now]
        if not due:
            return
        _t, _order, (kind, key), job = min(due, key=lambda item: (item[0], item[1]))
        try:
            job.value = self._fetch_user(key) if kind == "user" else self._fetch_place(key)
        except LOOKUP_ERRORS:
            job.attempts += 1
            if job.attempts <= len(self.RETRY_DELAYS):
                job.next_try = now + self.RETRY_DELAYS[job.attempts - 1]
            else:
                job.placeholder, job.attempts = True, 0
                job.next_try = now + self.PLACEHOLDER_RETRY_S

    def _get_json(self, url):
        return json.loads(self._http.get(url, timeout=self.TIMEOUT_S))

    @staticmethod
    def _clean_name(value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("no usable name in the reply")
        return value.strip()

    def _fetch_user(self, user_id):
        return self._clean_name(self._get_json(USER_URL.format(user_id))["name"])

    def _fetch_place(self, place_id):
        universe = self._get_json(UNIVERSE_URL.format(place_id))["universeId"]
        return self._clean_name(self._get_json(GAME_URL.format(universe))["data"][0]["name"])
