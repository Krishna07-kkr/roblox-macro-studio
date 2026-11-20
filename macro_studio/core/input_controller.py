"""
Input Controller Module
Executes high-reliability Windows mouse clicks.
Supports dual backends:
1. AutoHotkey Engine (using AutoHotkey v2 / v1 executable for 100% bypass of game input hooks)
2. Enhanced Native SendInput / mouse_event with absolute coordinate normalization and hold time
"""

import os
import time
import shutil
import subprocess
import threading
import ctypes
from ctypes import wintypes
import win32gui
import win32con
from macro_studio.core.win32 import attach_input_desktop

user32 = ctypes.windll.user32


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", INPUT_UNION),
    ]


INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
CREATE_NO_WINDOW = 0x08000000
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_ABSOLUTE = 0x8000


# AutoHotkey is looked up once per process, the first time a click needs to know. Looking it
# up starts up to four child processes with a 3 s timeout each, so it must not run for every
# controller (every Start builds a new one) or at construction.
_AHK_INFO = None
_AHK_LOCK = threading.Lock()


def _which_outside_cwd(name):
    """shutil.which, but ignoring a hit inside the current folder.

    On Windows `which` looks in the current folder before PATH, and this app's current folder
    is its own data folder. A planted AutoHotkey.exe there would be run for the probe and for
    every click, so only a program found elsewhere is trusted.
    """
    found = shutil.which(name)
    if not found:
        return ""
    folder = os.path.normcase(os.path.dirname(os.path.abspath(found)))
    if folder == os.path.normcase(os.path.abspath(os.getcwd())):
        return ""
    return found


def _cached_ahk(controller):
    global _AHK_INFO
    with _AHK_LOCK:
        if _AHK_INFO is None:
            _AHK_INFO = controller._detect_ahk()
        return _AHK_INFO


class InputController:
    #: How often a wait looks at the stop signal. Keeps stop latency well under 0.1 s.
    STOP_POLL_S = 0.02
    #: An AutoHotkey click that has not finished by now is killed.
    AHK_TIMEOUT_S = 4.0

    def __init__(self, click_delay=0.35, engine="auto", hold_time=0.15, stop_check=None):
        # Called before every action; True means stop now (F6/Esc, or the runner stopped).
        self.stop_check = stop_check
        self.click_delay = click_delay
        self.hold_time = hold_time
        self.engine = engine  # "auto", "ahk", "native"

    def __getattr__(self, name):
        # Only reached when the attribute is not set yet: ahk_path / ahk_version are found
        # lazily, once for the whole process, and then stored on the instance.
        if name in ("ahk_path", "ahk_version"):
            self.ahk_path, self.ahk_version = _cached_ahk(self)
            return self.__dict__[name]
        raise AttributeError(name)

    def _stopped(self):
        check = getattr(self, "stop_check", None)
        return bool(check and check())

    def _wait(self, seconds):
        """Sleep in short steps. False as soon as a stop is requested, True if it ran out."""
        end = time.monotonic() + seconds
        while True:
            if self._stopped():
                return False
            left = end - time.monotonic()
            if left <= 0:
                return True
            time.sleep(min(self.STOP_POLL_S, left))

    def _release_button(self):
        """Let go of the left button. Harmless if it is not down; vital if a click was cut short."""
        user32.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    def _detect_ahk(self):
        """Finds the installed AutoHotkey executable and determines its major version (1 or 2)."""
        candidate_paths = [
            r"C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe",
            r"C:\Program Files\AutoHotkey\v2\AutoHotkey32.exe",
            r"C:\Program Files\AutoHotkey\AutoHotkey.exe",
            r"C:\Program Files\AutoHotkey\AutoHotkeyU64.exe",
            _which_outside_cwd("AutoHotkey64.exe"),
            _which_outside_cwd("AutoHotkey.exe"),
        ]

        for p in candidate_paths:
            if p and os.path.isfile(p):
                try:
                    res = subprocess.run(
                        [p, "/ErrorStdOut", "*"],
                        input='#Requires AutoHotkey v2.0+\nFileAppend("v2", "*")\n',
                        text=True,
                        capture_output=True,
                        timeout=3,
                        creationflags=CREATE_NO_WINDOW,
                    )
                    if "v2" in res.stdout:
                        return p, 2

                    res = subprocess.run(
                        [p, "/ErrorStdOut", "*"],
                        input='#NoEnv\nFileAppend, v1, *\n',
                        text=True,
                        capture_output=True,
                        timeout=3,
                        creationflags=CREATE_NO_WINDOW,
                    )
                    if "v1" in res.stdout:
                        return p, 1
                except Exception:
                    continue

        return None, 0

    # A click that lands two pixels off a small button does nothing, and looks
    # exactly like a click that was ignored. So the cursor position is asserted
    # and re-checked before any button goes down.
    CURSOR_TOLERANCE_PX = 2

    def place_cursor(self, screen_x, screen_y, attempts=3):
        """Put the cursor exactly on a screen point and confirm it got there.

        SetCursorPos can be swallowed - another app moving the mouse, a window
        appearing under the pointer, or the pointer being clipped to a window.
        Returns False instead of pretending, so the caller can refuse to click.
        """
        sx, sy = int(screen_x), int(screen_y)
        for _ in range(max(1, attempts)):
            if self._stopped():
                return False
            user32.SetCursorPos(sx, sy)
            if not self._wait(0.02):
                return False
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            if (abs(pt.x - sx) <= self.CURSOR_TOLERANCE_PX
                    and abs(pt.y - sy) <= self.CURSOR_TOLERANCE_PX):
                return True
        return False

    def _ensure_desktop_access(self):
        """Attaches the current thread to the interactive input desktop to permit hardware events."""
        return attach_input_desktop()

    def click(self, x, y, client_offset=(0, 0), relative=True, hold_time=None, hwnd=None, use_postmessage=False):
        """
        Executes a mouse click using the best available click engine.
        If engine is 'ahk' or 'auto' (and AHK is available), uses AutoHotkey with Event mode.
        Otherwise falls back to enhanced native SendInput.
        """
        if self._stopped():
            return False
        hold = hold_time if hold_time is not None else self.hold_time
        target_x = int(x)
        target_y = int(y)

        use_ahk = False
        if self.engine in ("ahk", "auto") and self.ahk_path:
            use_ahk = True
        elif self.engine == "native":
            use_ahk = False

        if use_ahk:
            # AutoHotkey gets a screen point, so its "did the cursor arrive" check compares
            # screen with screen. A client point failed that check and the click was sent twice.
            success = self.click_ahk(target_x, target_y, hold_ms=int(hold * 1000), hwnd=hwnd,
                                     client_offset=client_offset if relative else (0, 0))
            if success:
                self._wait(self.click_delay)
                return True
            if self._stopped():
                return False       # stopped mid-click: never retry it another way

        # Fallback to enhanced native click
        landed = self.click_native(target_x, target_y, client_offset=client_offset,
                                   relative=relative, hold_time=hold, hwnd=hwnd)
        self._wait(self.click_delay)
        return landed

    def click_ahk(self, client_x, client_y, hold_ms=150, hwnd=None, client_offset=(0, 0)):
        """Dispatches a hardware click via AutoHotkey with Event SendMode and smooth hover."""
        if not self.ahk_path:
            return False

        cx = int(client_x + client_offset[0])
        cy = int(client_y + client_offset[1])

        try:
            if self.ahk_version == 2:
                activate_snippet = ""
                if hwnd and user32.IsWindow(hwnd):
                    activate_snippet = f"""
if WinExist("ahk_id {hwnd}") {{
    WinActivate("ahk_id {hwnd}")
    Sleep(50)
}}
"""
                script = f"""#Requires AutoHotkey v2.0+
#NoTrayIcon
SendMode("Event")
SetMouseDelay(15)
SetDefaultMouseSpeed(2)
CoordMode("Mouse", "Screen")
{activate_snippet}
MouseMove({cx}, {cy}, 2)
Sleep(40)
Click({cx}, {cy}, "Down")
Sleep({hold_ms})
Click("Up")
"""
            else:
                activate_snippet = ""
                if hwnd and user32.IsWindow(hwnd):
                    activate_snippet = f"""
if WinExist("ahk_id {hwnd}")
{{
    WinActivate, ahk_id {hwnd}
    Sleep, 50
}}
"""
                script = f"""#NoEnv
#NoTrayIcon
SendMode, Event
SetMouseDelay, 15
SetDefaultMouseSpeed, 2
CoordMode, Mouse, Screen
{activate_snippet}
MouseMove, {cx}, {cy}, 2
Sleep, 40
Click, down, {cx}, {cy}
Sleep, {hold_ms}
Click, up
"""

            if not self._run_ahk(script):
                return False

            # Verify cursor actually reached target vicinity; if not, AHK was blocked
            pt_cur = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt_cur))
            # Tight: a cursor that is more than a few pixels away means the move
            # was blocked or offset, and the click did not land where we asked.
            # Falling through to the native path is safer than assuming it did.
            if abs(pt_cur.x - cx) > 5 or abs(pt_cur.y - cy) > 5:
                return False

            return True
        except Exception:
            return False

    def _run_ahk(self, script):
        """Run an AutoHotkey script, watching for a stop. True if it finished cleanly.

        A stop or a hang kills the child. The script may have pressed the button already,
        so the button is released as well.
        """
        proc = subprocess.Popen([self.ahk_path, "/ErrorStdOut", "*"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                creationflags=CREATE_NO_WINDOW)
        deadline = time.monotonic() + self.AHK_TIMEOUT_S
        first = True
        while True:
            try:
                proc.communicate(input=script if first else None, timeout=self.STOP_POLL_S)
                break
            except subprocess.TimeoutExpired:
                first = False
                if self._stopped() or time.monotonic() > deadline:
                    proc.kill()
                    try:
                        proc.communicate(timeout=1)         # reap it
                    except (subprocess.TimeoutExpired, OSError):
                        pass
                    self._release_button()
                    return False
        return proc.returncode == 0

    def click_native(self, x, y, client_offset=(0, 0), relative=True, hold_time=0.15, hwnd=None):
        """
        Enhanced native SendInput click:
        1. SetCursorPos, verified with GetCursorPos
        2. Micro relative nudge to trigger game hover listeners
        3. Re-assert the exact pixel, then Button Down -> Sleep -> Button Up

        Returns True only if the cursor was verified on target and a button
        pair was delivered.
        """
        if self._stopped():
            return False
        self._ensure_desktop_access()

        screen_x = x
        screen_y = y
        if relative:
            screen_x += client_offset[0]
            screen_y += client_offset[1]

        screen_x = max(0, int(screen_x))
        screen_y = max(0, int(screen_y))

        # Move, hover-nudge so the game registers the pointer, then put the
        # cursor back exactly on target before pressing. Every wait gives up the
        # moment a stop is requested; nothing new is sent after that.
        self.place_cursor(screen_x, screen_y)
        if not self._wait(0.04):
            return False

        user32.mouse_event(win32con.MOUSEEVENTF_MOVE, 1, 0, 0, 0)
        if not self._wait(0.02):
            return False
        user32.mouse_event(win32con.MOUSEEVENTF_MOVE, -1, 0, 0, 0)
        if not self._wait(0.04):
            return False

        landed = self.place_cursor(screen_x, screen_y)
        if not landed:
            # Pressing now would click whatever is under a cursor we do not
            # control. Report the miss instead; the caller can retry.
            return False
        if self._stopped():
            return False

        # Press where the cursor already is. The old code re-specified the
        # point as ABSOLUTE coordinates normalised through a 65535-step grid
        # sized from the PRIMARY monitor only - which quantises the target and,
        # on a multi-monitor desktop, can move the press a pixel or more off a
        # small button. Sending the button alone keeps the exact pixel.
        # Once the button is down it is always released, and at once on a stop.
        success = False
        try:
            down = INPUT(type=INPUT_MOUSE,
                         union=INPUT_UNION(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, None)))
            up = INPUT(type=INPUT_MOUSE,
                       union=INPUT_UNION(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, None)))

            sent_down = user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
            self._wait(hold_time)
            sent_up = user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))

            if sent_down > 0 and sent_up > 0:
                success = True
        except Exception:
            success = False

        # If SendInput was ignored by the game engine, use direct hardware mouse_event
        if not success and not self._stopped():
            try:
                user32.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                self._wait(hold_time)
                user32.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                success = True
            except Exception:
                success = False

        return success

    def double_click(self, x, y, client_offset=(0, 0), relative=True, hwnd=None):
        self.click(x, y, client_offset, relative, hwnd=hwnd)
        if not self._wait(0.08):
            return
        self.click(x, y, client_offset, relative, hwnd=hwnd)


