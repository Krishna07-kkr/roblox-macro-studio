"""
Window Manager Module
Handles identification, auto-arrangement, and bulletproof foreground activation
of the Roblox game client process (RobloxPlayerBeta.exe, Roblox.exe, etc.).
Completely bypasses Windows focus-stealing locks so the game window is guaranteed
to be the active, focused foreground window before any macro action begins.
"""

import os
import time
import ctypes
from ctypes import wintypes
try:
    import psutil
except ImportError:
    psutil = None
import win32api
import win32con
import win32gui
import win32process
from macro_studio.core.win32 import attach_input_desktop

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class WindowManager:
    def __init__(self, target_procs=None, ignore_procs=None, width=800, height=600):
        self.target_procs = [p.lower() for p in (target_procs or ["robloxplayerbeta.exe", "roblox.exe", "windows10universal.exe"])]
        self.ignore_procs = [p.lower() for p in (ignore_procs or ["roblox account manager.exe", "chrome.exe", "msedge.exe", "brave.exe", "firefox.exe"])]
        self.standard_width = width
        self.standard_height = height
        self.arranged_hwnds = set()
        self.shrunk_hwnds = set()
        self._ensure_desktop_access()

    def _ensure_desktop_access(self):
        """Attaches the thread to the interactive input desktop (no leaked handle)."""
        return attach_input_desktop()

    def count_minimized_clients(self):
        """How many game-client windows are minimized (cannot be captured or clicked)."""
        import psutil
        import win32gui
        import win32process

        counted = []

        def callback(hwnd, _):
            if not (win32gui.IsWindowVisible(hwnd) and win32gui.IsIconic(hwnd)):
                return True
            if not win32gui.GetWindowText(hwnd):
                return True
            try:
                pid = win32process.GetWindowThreadProcessId(hwnd)[1]
                name = psutil.Process(pid).name().lower()
            except (psutil.Error, OSError):
                return True
            if name in self.target_procs and name not in self.ignore_procs:
                counted.append(hwnd)
            return True

        win32gui.EnumWindows(callback, None)
        return len(counted)

    def _process_name(self, pid):
        """Lowercase program name of a process id, or "" when it cannot be read.

        Uses psutil, then falls back to asking Windows directly. The process handle the
        fallback opens is closed on every path.
        """
        if psutil is not None:
            try:
                return psutil.Process(pid).name().lower()
            except (psutil.Error, OSError):
                pass
        handle = None
        try:
            handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
            return os.path.basename(win32process.GetModuleFileNameEx(handle, 0)).lower()
        except (win32api.error, OSError):
            return ""
        finally:
            if handle:
                win32api.CloseHandle(handle)

    def is_minimized(self, hwnd):
        return bool(user32.IsIconic(hwnd))

    def find_windows(self, include_minimized=False):
        """
        Scans all top-level windows and returns only windows that belong to
        the Roblox game client process.
        Filters out browsers, Roblox Account Manager, and background tool windows.
        A minimized client is a tiny off-screen rectangle, so it is skipped unless
        `include_minimized` (the Tabs picker lists those too).
        """
        self._ensure_desktop_access()
        matched_windows = []

        def enum_callback(hwnd, lparam):
            try:
                if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
                    return True

                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                proc_id = pid.value
                if proc_id <= 0:
                    return True

                pname = self._process_name(proc_id)

                if not pname:
                    return True

                # Ignore blacklisted processes
                for ign in self.ignore_procs:
                    if ign in pname:
                        return True

                # Check if matches target Roblox process
                is_target = any(target in pname for target in self.target_procs)
                if not is_target:
                    return True

                # Check size to filter out tray icons / helper windows
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                w = rect.right - rect.left
                h = rect.bottom - rect.top

                if w > 200 and h > 200:
                    matched_windows.append((hwnd, proc_id))
                elif (include_minimized and user32.IsIconic(hwnd)
                      and user32.GetWindowTextLengthW(hwnd)):
                    matched_windows.append((hwnd, proc_id))

            except Exception:
                pass
            return True

        cb = WNDENUMPROC(enum_callback)
        user32.EnumWindows(cb, 0)

        # Clean up stale arranged & shrunk HWNDs
        current_hwnds = {item[0] for item in matched_windows}
        self.arranged_hwnds = self.arranged_hwnds.intersection(current_hwnds)
        self.shrunk_hwnds = self.shrunk_hwnds.intersection(current_hwnds)

        matched_windows.sort(key=lambda x: x[0])
        return [item[0] for item in matched_windows]

    def shrink_all_windows_once(self, windows):
        """
        ONE-SHOT BATCH SHRINK:
        Shrinks all detected game windows to the minimum size once upon initial discovery.
        Only processes windows that have not already been shrunk, saving processing power
        and preventing redundant window redraw calls during the execution loop.
        """
        if not windows:
            return 0

        unshrunk = [h for h in windows if h not in self.shrunk_hwnds]
        if not unshrunk:
            return 0

        count = 0
        for hwnd in unshrunk:
            if self.shrink_to_minimum(hwnd):
                self.shrunk_hwnds.add(hwnd)
                count += 1
            time.sleep(0.04)
        return count

    def shrink_to_minimum(self, hwnd, x=None, y=None):
        """
        Forces the Roblox game window to the smallest resolution allowed by the OS
        and the Roblox client engine (800x600 client / ~816x638 outer).
        Un-maximizes if zoomed, restores if minimized, and clamps to minimum size.
        """
        self._ensure_desktop_access()
        if not user32.IsWindow(hwnd):
            return False

        try:
            # 1. If window is maximized or minimized, restore it to normal state first
            if user32.IsZoomed(hwnd) or user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.08)

            # 2. Get current position if x or y is not specified
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            target_x = rect.left if x is None else x
            target_y = rect.top if y is None else y

            # 3. Passing 100, 100 forces Windows and Roblox ptMinTrackSize to clamp
            # to the absolute minimum allowed window dimensions.
            user32.MoveWindow(hwnd, int(target_x), int(target_y), 100, 100, True)
            time.sleep(0.05)
            return True
        except Exception:
            return False

    def auto_arrange(self, windows, style=None, offset_x=None, offset_y=None, cols=2):
        """
        WINDOW ARRANGEMENT:
        Arranges game client windows across the screen while keeping them locked
        to the standard 800x600 client minimum dimensions.

        Supports two modes:
        - "diagonal" (default): Cascades windows diagonally with a configurable gap
          (default 10px) so the title bar and edge of each running tab remains visible.
          Automatically wraps to a new cascade if windows approach screen boundaries.
        - "grid": Tiles windows in a column-by-row grid without overlap.
        """
        if not windows:
            return

        # Backward compatibility for positional `cols` argument
        if isinstance(style, int):
            cols = style
            style = "diagonal"
        if not style:
            style = "diagonal"
        if offset_x is None:
            offset_x = 10
        if offset_y is None:
            offset_y = 10

        unarranged = [h for h in windows if h not in self.arranged_hwnds]
        if not unarranged and len(self.arranged_hwnds) == len(windows):
            return

        # Outer dimensions of minimum window with borders (~816x638)
        tile_w = 816
        tile_h = 638

        # Fetch desktop usable work area
        screen_left = 0
        screen_top = 0
        screen_w = 1920
        screen_h = 1080
        try:
            rect = wintypes.RECT()
            # SPI_GETWORKAREA = 0x0030
            if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                screen_left = rect.left
                screen_top = rect.top
                screen_w = max(800, rect.right - rect.left)
                screen_h = max(600, rect.bottom - rect.top)
            else:
                screen_w = max(800, user32.GetSystemMetrics(0))
                screen_h = max(600, user32.GetSystemMetrics(1))
        except Exception:
            pass

        if style == "grid":
            cols = max(1, int(cols))
            for idx, hwnd in enumerate(windows):
                row = idx // cols
                col = idx % cols
                x = screen_left + col * tile_w
                y = screen_top + row * tile_h

                self.shrink_to_minimum(hwnd, x=x, y=y)
                self.arranged_hwnds.add(hwnd)
                self.shrunk_hwnds.add(hwnd)
                time.sleep(0.06)
        else:
            # Diagonal cascade layout (5-10px gap)
            step_x = max(1, int(offset_x))
            step_y = max(1, int(offset_y))

            avail_w = max(100, screen_w - tile_w)
            avail_h = max(100, screen_h - tile_h)
            max_per_cascade = max(1, min(avail_w // step_x, avail_h // step_y))

            for idx, hwnd in enumerate(windows):
                cascade_idx = idx // max_per_cascade
                step_in_cascade = idx % max_per_cascade

                cascade_start_x = screen_left + (cascade_idx * 160)
                cascade_start_y = screen_top

                x = cascade_start_x + (step_in_cascade * step_x)
                y = cascade_start_y + (step_in_cascade * step_y)

                # Clamp within screen bounds
                x = min(x, screen_left + screen_w - tile_w)
                y = min(y, screen_top + screen_h - tile_h)

                self.shrink_to_minimum(hwnd, x=x, y=y)
                try:
                    user32.SetWindowPos(hwnd, win32con.HWND_TOP, int(x), int(y), 0, 0,
                                        win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
                except Exception:
                    pass

                self.arranged_hwnds.add(hwnd)
                self.shrunk_hwnds.add(hwnd)
                time.sleep(0.06)

    def force_activate_window(self, hwnd, max_wait_sec=1.5, auto_shrink=False):
        """
        BULLETPROOF WINDOW ACTIVATION:
        Forces Windows to make target window the true active foreground window.
        Uses SPI lock clearing, thread input attachment, and Alt-key simulation.
        Only shrinks if explicitly requested and window hasn't been shrunk yet.
        """
        self._ensure_desktop_access()

        if not user32.IsWindow(hwnd):
            return False

        try:
            # 1. Allow the switch. (The old foreground-lock-timeout change was saved into the
            # user's Windows settings and broadcast to every window on each call.)
            user32.AllowSetForegroundWindow(0xFFFFFFFF)

            # 2. Restore if minimized or maximized
            if user32.IsZoomed(hwnd) or user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.08)
            else:
                user32.ShowWindow(hwnd, win32con.SW_SHOW)

            # 3. Auto-shrink only once if requested and not yet shrunk
            if auto_shrink and hwnd not in self.shrunk_hwnds:
                self.shrink_to_minimum(hwnd)
                self.shrunk_hwnds.add(hwnd)

            # 4. Thread input attachment
            cur_tid = kernel32.GetCurrentThreadId()
            fg_hwnd = user32.GetForegroundWindow()
            fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
            target_tid = user32.GetWindowThreadProcessId(hwnd, None)

            if fg_tid and cur_tid != fg_tid:
                user32.AttachThreadInput(cur_tid, fg_tid, True)
            if target_tid and cur_tid != target_tid:
                user32.AttachThreadInput(cur_tid, target_tid, True)

            # 5. Alt-key bypass & foreground switch
            user32.keybd_event(0x12, 0, 0, 0) # Alt down
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            user32.SetActiveWindow(hwnd)
            user32.keybd_event(0x12, 0, 2, 0) # Alt up

            # 6. Detach thread input
            if fg_tid and cur_tid != fg_tid:
                user32.AttachThreadInput(cur_tid, fg_tid, False)
            if target_tid and cur_tid != target_tid:
                user32.AttachThreadInput(cur_tid, target_tid, False)

            # 7. Verify foreground state with polling
            start = time.time()
            while time.time() - start < max_wait_sec:
                if user32.GetForegroundWindow() == hwnd:
                    return True
                time.sleep(0.05)

            # Final check
            return user32.GetForegroundWindow() == hwnd
        except Exception:
            return False

    def is_foreground(self, hwnd):
        """True only if this window is the real foreground window right now.

        Clicks are delivered to whatever is in front, so this is checked again
        immediately before clicking - activation at the top of an account pass
        can be lost to a notification, another client, or the user.
        """
        try:
            return bool(user32.GetForegroundWindow() == hwnd)
        except Exception:
            return False

    def get_pid(self, hwnd):
        """Returns the process id owning this window, or 0 if it cannot be read.

        The claim scheduler keys accounts by PID because a handle dies with the
        window, while the PID lasts as long as the client itself.
        """
        try:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return int(pid.value)
        except Exception:
            return 0

    def get_window_offset(self, hwnd):
        """Returns (screen_x, screen_y) of the window frame top-left corner."""
        try:
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            return rect.left, rect.top
        except Exception:
            return 0, 0

    def get_client_offset(self, hwnd):
        """Returns (screen_x, screen_y) of the client rendering area top-left corner."""
        try:
            pt = wintypes.POINT(0, 0)
            user32.ClientToScreen(hwnd, ctypes.byref(pt))
            return pt.x, pt.y
        except Exception:
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            return rect.left, rect.top

    def get_window_title(self, hwnd):
        """Returns the window title string."""
        try:
            return win32gui.GetWindowText(hwnd) or "Roblox Game Client"
        except Exception:
            return "Roblox Game Client"
