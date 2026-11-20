"""The Layout service: arrange windows and hold the multi-instance lock (spec 001, US4).

No Tk and no Win32: windows, monitors, placing and the lock arrive as arguments, so it is
unit-tested (tests/unit/test_layout.py). Layouts never move windows while a macro runner owns
the input (Constitution IV), and the order is by process id, so it does not shuffle when the
windows are brought to the front (the AutoHotkey version used stacking order).
"""

from dataclasses import dataclass

from macro_studio.tools.layout.layout import STYLES, compute_layout

MUTEX_NAME = "ROBLOX_singletonEvent"
BUSY = "A macro is running. Layouts wait until it stops."


@dataclass(frozen=True)
class Result:
    ok: bool
    reason: str = ""


class LayoutService:
    def __init__(self, windows, monitors, place, minimise, restore, automation, log):
        self._windows, self._monitors = windows, monitors
        self._place, self._minimise, self._restore = place, minimise, restore
        self._automation, self._log = automation, log

    def _ordered(self):
        return sorted(self._windows(), key=lambda item: (item[1], item[0]))     # (hwnd, pid)

    def _guard(self):
        if not self._automation.try_borrow("layout"):
            return Result(False, BUSY)
        if not self._ordered():
            return Result(False, "No Roblox window is open.")
        return None

    def arrange(self, style, offset, size, monitor):
        if style not in STYLES:
            raise ValueError(f"Unknown layout '{style}'.")
        refused = self._guard()
        if refused:
            return refused
        windows = self._ordered()
        areas = self._monitors()
        area = areas[monitor] if 0 <= monitor < len(areas) else areas[0]
        for (hwnd, _pid), rect in zip(windows, compute_layout(style, len(windows), area, size, offset), strict=True):
            self._place(hwnd, rect)
        self._log(f"[LAYOUT] Arranged {len(windows)} window(s) as {style}.")
        return Result(True)

    def minimise_all(self):
        refused = self._guard()
        if refused:
            return refused
        for hwnd, _pid in self._ordered():
            self._minimise(hwnd)
        return Result(True)

    def restore_all(self):
        refused = self._guard()
        if refused:
            return refused
        for hwnd, _pid in self._ordered():
            self._restore(hwnd)
        return Result(True)


class MultiInstance:
    """Holds Roblox's single-client lock so several clients can run at once.

    The lock only works if it is taken before the first client starts, so it is refused while
    any client is running. It is held for as long as the option is on, and released on
    disable() or at exit.
    """

    def __init__(self, create, close, clients_running):
        self._create, self._close, self._clients_running = create, close, clients_running
        self._handle = None

    @property
    def active(self):
        return self._handle is not None

    def enable(self):
        if self.active:
            return Result(True)
        if self._clients_running():
            return Result(False, "Close every Roblox client first, then turn this on: the lock "
                                 "only works if it is held before the first client starts.")
        handle = self._create(MUTEX_NAME)
        if not handle:
            return Result(False, "Windows would not create the lock.")
        self._handle = handle
        return Result(True)

    def disable(self):
        if self._handle is not None:
            self._close(self._handle)
            self._handle = None
