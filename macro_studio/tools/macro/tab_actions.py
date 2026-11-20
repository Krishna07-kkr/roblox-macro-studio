"""Actions on Roblox client tabs from the Tabs picker's right-click menu.

Each action runs on one background worker, never on the Tk thread (spec 002 A2), and checks
right before it acts that the window still belongs to the client the picker showed: the
picker's list can be a second old and process ids are reused. A window that changed owner,
or a process that is not a Roblox client, is skipped and the skip is logged.
"""

import queue
import threading

import win32con
import win32gui

from macro_studio.core.processes import ROBLOX_EXES, terminate_process, trim_process

ACTIONS = ("focus", "trim", "close", "restore", "minimize")
_WORKER_STOP = object()


def _default_process_name(wm):
    return wm._process_name


def _show_window(hwnd, how):
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE if how == "restore" else win32con.SW_MINIMIZE)


class TabActions:
    def __init__(self, wm, log, process_name=None, trim=trim_process, terminate=terminate_process,
                 show_window=_show_window):
        self._wm, self._log = wm, log
        self._process_name = process_name or _default_process_name(wm)
        self._trim, self._terminate = trim, terminate
        self._show = show_window
        self._queue = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()
        self._stopped = False

    # ------------------------------------------------------------ background worker
    @property
    def alive(self):
        return self._thread is not None and self._thread.is_alive()

    def run(self, name, tabs):
        """Queue an action for the worker (started on first use; one thread for all actions)."""
        with self._lock:
            if self._stopped:
                return
            self._queue.put((name, list(tabs)))
            if not self.alive:
                self._thread = threading.Thread(target=self._work, name="tab-actions", daemon=True)
                self._thread.start()

    def stop(self, wait=False):
        with self._lock:
            self._stopped = True
            self._queue.put(_WORKER_STOP)
            thread = self._thread
        if wait and thread is not None:
            thread.join(timeout=5)

    def _work(self):
        while True:
            job = self._queue.get()
            if job is _WORKER_STOP:
                return
            name, tabs = job
            try:
                self.do(name, tabs)
            except Exception as err:          # noqa: BLE001 - keep the worker alive; log the cause
                self._log(f"[TABS] {name} failed: {err}")

    # ------------------------------------------------------------ the actions
    def do(self, name, tabs):
        """Run one action over `tabs` now (on the calling thread). One failure never stops the rest."""
        if name not in ACTIONS:
            self._log(f"[TABS] Unknown action {name!r}.")
            return
        done = skipped = 0
        for tab in tabs:
            reason = self._why_not(name, tab)
            if reason:
                skipped += 1
                self._log(f"[TABS] Skipped {self._who(tab)}: {reason}.")
                continue
            try:
                self._apply(name, tab)
            except (OSError, PermissionError) as err:
                skipped += 1
                self._log(f"[TABS] {name} {self._who(tab)}: {err}")
            else:
                done += 1
        if done:
            self._log(f"[TABS] {self._past(name)} {done} {'tab' if done == 1 else 'tabs'}"
                      + (f" ({skipped} skipped)." if skipped else "."))

    def _apply(self, name, tab):
        if name == "focus":
            self._wm.force_activate_window(tab.hwnd)
        elif name in ("restore", "minimize"):
            self._show(tab.hwnd, name)
        elif name == "trim":
            self._trim(tab.pid)
        else:
            self._terminate(tab.pid)

    def _why_not(self, name, tab):
        if self._wm.get_pid(tab.hwnd) != tab.pid:
            return "its window now belongs to another program"
        if name != "focus" and self._process_name(tab.pid) not in ROBLOX_EXES:
            return "it is not a Roblox client any more"
        return ""

    @staticmethod
    def _who(tab):
        return f"PID {tab.pid}"

    @staticmethod
    def _past(name):
        return {"focus": "Brought to front:", "trim": "Trimmed memory of", "close": "Closed",
                "restore": "Restored", "minimize": "Minimized"}[name]
