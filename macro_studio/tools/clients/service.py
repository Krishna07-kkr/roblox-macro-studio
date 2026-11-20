"""The Clients service: what is running, whose it is, and safe actions on it (spec 001, US2).

No Tk and no Win32. Everything it looks at or does arrives through the arguments (processes,
windows, logs, names, focus, trim, terminate), so the whole thing is unit-tested with fakes
(tests/unit/test_clients_service.py). `system.py` provides the real implementations.
"""

import threading
from dataclasses import dataclass, replace
from datetime import datetime, UTC

from macro_studio.core import roblox
from macro_studio.core.processes import ProcessInfo, same_process  # noqa: F401 - ProcessInfo is re-exported

SCAN_ERRORS = (OSError, RuntimeError, ValueError)
HEAD_REREAD_S = 4.0            # until the account id is found
TAIL_REREAD_S = 8.0            # the current game
NAME_GIVE_UP_S = 30.0          # then the window title falls back to "PID n (id X)"


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    minimized: bool
    responsive: bool


@dataclass(frozen=True)
class ClientRow:
    pid: int
    hwnd: int
    title: str
    account: str
    game: str
    ram_mb: float
    uptime_s: int
    status: str
    started: float
    process_name: str
    user_id: int | None = None
    place_id: int | None = None
    resolved: bool = False     # the account name is a real name, not a placeholder


class _Info:
    """What has been worked out about one client so far."""

    def __init__(self, first_seen):
        self.first_seen = first_seen
        self.user_id = None
        self.places = roblox.PlaceTracker()
        self.head_at = None
        self.tail_at = None


_SORT_KEYS = {
    "pid": lambda r: r.pid,
    "ram": lambda r: r.ram_mb,
    "uptime": lambda r: r.uptime_s,
    "account": lambda r: r.account.casefold(),
    "game": lambda r: r.game.casefold(),
    "status": lambda r: r.status.casefold(),
}


def sort_rows(rows, key, ascending=True):
    """Rows sorted by a column: numbers as numbers (not text), names ignoring case."""
    return sorted(rows, key=_SORT_KEYS[key], reverse=not ascending)


class ClientsService:
    def __init__(self, processes, windows, list_logs, read_text, lookup, publish, clock,
                 wall_clock, process_info, set_title, focus, trim, terminate,
                 rename=False, rename_with_game=False, close_dialogs=False, dialogs=None,
                 close_dialog=None, log=None, on_rows=None):
        self._processes, self._windows, self._list_logs = processes, windows, list_logs
        self._read_text, self._lookup, self._publish = read_text, lookup, publish
        self._clock, self._wall_clock = clock, wall_clock
        self._process_info, self._set_title = process_info, set_title
        self._focus, self._trim, self._terminate = focus, trim, terminate
        self.rename, self.rename_with_game = rename, rename_with_game
        self.close_dialogs = close_dialogs
        self._dialogs, self._close_dialog = dialogs, close_dialog
        self._log = log or (lambda message: None)
        self._on_rows = on_rows or (lambda rows: None)    # other tools learn the list from this
        self._pairer = roblox.LogPairer(clock=wall_clock)
        self._info = {}
        self._rows = []
        self._by_pid = {}
        self._statuses = {}
        self._manual = set()
        self._scanned = False
        # The scheduled scan and the action worker's follow-up scan run on different threads.
        self._lock = threading.RLock()

    # ------------------------------------------------------------ scanning
    def scan(self):
        """Look at everything once and return the rows. A failed look keeps the last list."""
        with self._lock:
            return self._scan()

    def _scan(self):
        try:
            processes = {p.pid: p for p in self._processes()}
            windows = {}
            for window in self._windows():
                windows.setdefault(window.pid, window)
            logs = self._list_logs()
        except SCAN_ERRORS:
            return list(self._rows)

        rows = [self._row(proc, windows.get(proc.pid), logs) for proc in processes.values()]
        self._announce(rows)
        for pid in set(self._info) - set(processes):
            self._pairer.release(pid)
            del self._info[pid]
            self._manual.discard(pid)
        self._rows, self._by_pid = rows, {row.pid: row for row in rows}
        self._statuses = {row.pid: row.status for row in rows}
        self._scanned = True
        self._on_rows(list(rows))
        return list(rows)

    def _row(self, proc, window, logs):
        now = self._clock()
        info = self._info.setdefault(proc.pid, _Info(now))
        self._read_log(proc, info, logs, now)

        user_label = self._lookup.user_label(info.user_id) if info.user_id else None
        place_label = self._lookup.place_label(info.places.current) if info.places.current else None
        placeholder = f"id {info.user_id}"
        resolved = bool(user_label) and user_label != placeholder
        if window is None:
            status = "No window"
        elif window.minimized:
            status = "Minimised"
        elif not window.responsive:
            status = "Not responding"
        else:
            status = "Running"
        return ClientRow(
            pid=proc.pid, hwnd=window.hwnd if window else 0, title=window.title if window else "",
            account=user_label or "", game=place_label or "",
            ram_mb=proc.rss_bytes / 2**20,
            uptime_s=max(0, int(self._wall_clock() - proc.started)),
            status=status, started=proc.started, process_name=proc.name.lower(),
            user_id=info.user_id, place_id=info.places.current, resolved=resolved)

    def _read_log(self, proc, info, logs, now):
        started = datetime.fromtimestamp(proc.started, UTC)
        entry = self._pairer.pair(proc.pid, started, logs)
        if entry is None:
            return
        if info.user_id is None and (info.head_at is None or now - info.head_at >= HEAD_REREAD_S):
            info.head_at = now
            info.user_id = roblox.parse_user_id(self._text(entry.path, head=True))
        if info.tail_at is None or now - info.tail_at >= TAIL_REREAD_S:
            info.tail_at = now
            info.places.update(roblox.parse_place_id(self._text(entry.path, head=False)))
        if info.user_id:
            self._lookup.want_user(info.user_id)
        if info.places.current:
            self._lookup.want_place(info.places.current)

    def _text(self, path, head):
        try:
            return self._read_text(path, head)
        except OSError:
            return ""

    # ------------------------------------------------------------ events
    def _announce(self, rows):
        """Publish started / closed / hung / recovered. The first scan only learns the state."""
        if not self._scanned:
            return
        now_pids = {row.pid for row in rows}
        before = set(self._statuses)
        for row in rows:
            if row.pid not in before:
                self._publish("client_started", row=row)
            elif row.status == "Not responding" and self._statuses[row.pid] != "Not responding":
                self._publish("client_hung", row=row)
            elif row.status != "Not responding" and self._statuses[row.pid] == "Not responding":
                self._publish("client_recovered", row=row)
        for pid in sorted(before - now_pids):
            self._publish("client_closed", pid=pid, row=self._by_pid.get(pid))
        if before and not now_pids:
            self._publish("all_closed")

    # ------------------------------------------------------------ names
    def rows(self):
        return list(self._rows)

    def desired_title(self, row):
        """The window title this client should have, or None when it is not known yet."""
        if row.resolved:
            if self.rename_with_game and row.game and row.game != f"place {row.place_id}":
                return f"{row.account} - {row.game}"
            return row.account
        info = self._info.get(row.pid)
        if (info is not None and row.user_id
                and self._clock() - info.first_seen >= NAME_GIVE_UP_S):
            return f"PID {row.pid} (id {row.user_id})"
        return None

    def auto_rename(self):
        """Give each client window its account name. Titles set by hand are left alone."""
        with self._lock:
            self._auto_rename()

    def _auto_rename(self):
        if not self.rename:
            return
        for row in self._rows:
            if not row.hwnd or row.pid in self._manual:
                continue
            wanted = self.desired_title(row)
            if wanted and wanted != row.title:
                self._set_title(row.hwnd, wanted)

    def close_error_dialogs(self):
        """Close Roblox's own error dialogs (e.g. a crash notice) when the option is on.

        Only dialogs owned by a listed client are touched, and each one is logged. Off by
        default: an unexpected dialog is information the player may want to read.
        """
        if not self.close_dialogs or self._dialogs is None or self._close_dialog is None:
            return 0
        with self._lock:
            known = {row.pid: row for row in self._rows}
        closed = 0
        for hwnd, owner in self._dialogs():
            if owner in known:
                self._close_dialog(hwnd)
                self._log(f"Closed an error dialog from {known[owner].account or 'PID ' + str(owner)}.")
                closed += 1
        return closed

    def rename_by_hand(self, pid, title):
        with self._lock:
            row = self._by_pid.get(pid)
            if row is None or not row.hwnd or not title.strip():
                return False
            self._manual.add(pid)
            self._set_title(row.hwnd, title.strip())
            self._by_pid[pid] = replace(row, title=title.strip())
            return True

    # ------------------------------------------------------------ actions
    def _verified(self, pid):
        """True only if `pid` is still the very process that was listed (pids get reused)."""
        with self._lock:
            row = self._by_pid.get(pid)
        if row is None:
            return False
        try:
            current = self._process_info(pid)
        except SCAN_ERRORS:
            return False
        return same_process(row, current)

    def focus(self, pid):
        row = self._by_pid.get(pid)
        if row is None or not row.hwnd:
            return False
        self._focus(row.hwnd)
        return True

    def trim_one(self, pid):
        if not self._verified(pid):
            return False
        try:
            self._trim(pid)
        except OSError:
            return False
        return True

    def close_one(self, pid):
        if not self._verified(pid):
            return False
        try:
            self._terminate(pid)
        except OSError:
            return False
        return True
