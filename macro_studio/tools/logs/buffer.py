"""The log kept for the Logs page: capped, thread-safe, with a game-activity filter (US7).

No Tk: lines can arrive from any thread, and the page only redraws when `version` changed.
"""

import collections
import threading
import time

MAX_LINES = 2000
#: Tags the studio uses for its own plumbing. "Game activity only" hides these.
CHATTER_TAGS = ("[JOB]", "[SETTINGS]", "[HOTKEY]", "[EVENTS]", "[STOP]", "[SYSTEM]")


def is_game_activity(line):
    """False for the studio's own housekeeping (job timings, settings, hotkey errors)."""
    return not line.lstrip().startswith(CHATTER_TAGS)


def _clock():
    return time.strftime("%H:%M:%S")


class LogBuffer:
    def __init__(self, max_lines=MAX_LINES, now=_clock):
        self._lines = collections.deque(maxlen=max_lines)
        self._now = now
        self._lock = threading.Lock()
        self.version = 0

    def add(self, message):
        line = f"{self._now()}  {message}"
        with self._lock:
            self._lines.append(line)
            self.version += 1

    def clear(self):
        with self._lock:
            self._lines.clear()
            self.version += 1

    def lines(self, game_only=False):
        with self._lock:
            snapshot = list(self._lines)
        if game_only:
            snapshot = [line for line in snapshot if is_game_activity(line.split("  ", 1)[-1])]
        return snapshot

    def text(self, game_only=False):
        return "\n".join(self.lines(game_only))
