"""Plumbing every macro runner shares (Constitution I and II).

Each runner keeps its own loop (`_run_loop`) and its own messages through small hooks;
everything else lives here once: logging, stats and preview callbacks, pause and stop,
interruptible sleep, coordinate mapping, and the stop/pause keys.

Stop/pause keys go through the shared HotkeyService and AutomationLock instead of a
private watchdog thread per runner. A runner built without them (command line, or an
app that has not set up shared services) creates its own, so F6 / Esc / F7 always work.
"""

import os
import time
from typing import Any

from macro_studio.core import scaling
from macro_studio.core.fileio import rotate_log
from macro_studio.core.paths import get_base_dir
from macro_studio.services.automation import PAUSE_BINDING, STOP_BINDINGS, AutomationLock
from macro_studio.services.hotkeys import HotkeyService

LOG_FILE_PATH = os.path.join(get_base_dir(), "debug_log.txt")
# debug_log.txt moves to debug_log.txt.1 once it passes this size (checked every few lines).
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_CHECK_EVERY = 100


class RunnerBase:
    #: Owner name used for the automation lock and in log lines.
    name = "runner"

    # Collaborators each runner builds in its own __init__ (window manager, vision, input).
    wm: Any
    vs: Any
    input: Any

    # ------------------------------------------------------------ setup
    def _init_runtime(self, log_callback=None, stats_callback=None, frame_callback=None,
                      max_cycles=None, hotkeys=None, automation=None, tab_selection=None):
        self.log_callback = log_callback
        # Shared with the Overview "Tabs" picker; None works on every client.
        self.tab_selection = tab_selection
        self.stats_callback = stats_callback
        self.frame_callback = frame_callback
        self.max_cycles = max_cycles

        self.running = True
        self.paused = False
        self.stats = {"cycles": 0, "claims": 0, "adds": 0, "accounts": 0, "active_hwnd": 0}
        self.hud = None

        self._owns_hotkeys = hotkeys is None
        self._hotkeys = hotkeys if hotkeys is not None else HotkeyService()
        self._automation = automation if automation is not None else AutomationLock()

    # ------------------------------------------------------------ hooks
    def _run_loop(self):
        raise NotImplementedError

    def _on_stopped(self):
        """Runner-specific feedback once a stop takes effect (beep, log line)."""

    def _on_pause_changed(self):
        """Runner-specific feedback after pause/resume."""

    def _reference_config(self):
        """The config dict holding `reference_client` for coordinate mapping."""
        return getattr(self, "config", {}) or {}

    def _selected_windows(self, hwnds):
        """Only the client tabs ticked in the Tabs picker, in window order."""
        selection = getattr(self, "tab_selection", None)
        if selection is None:
            return list(hwnds)
        chosen = selection.filter(hwnds, self.wm.get_pid)
        summary = (len(chosen), len(hwnds))
        if len(chosen) != len(hwnds) and summary != getattr(self, "_tab_summary", None):
            self.log(f"[TABS] Working on {len(chosen)} of {len(hwnds)} open tab(s) "
                     f"(selected in Tabs).")
        self._tab_summary = summary
        return chosen

    def on_emergency_stop(self):
        """Called by AutomationLock.emergency_stop (F6 / Esc, or any tool)."""
        self.stop()

    # ------------------------------------------------------------ run
    def run(self):
        """Run the loop while holding the automation lock and the stop/pause keys."""
        automation = getattr(self, "_automation", None)
        hotkeys = getattr(self, "_hotkeys", None)
        if automation is not None and not automation.acquire(self.name):
            self.log(f"[RUN] '{automation.owner}' is already running - not starting "
                     f"'{self.name}'.")
            return
        if automation is not None:
            automation.register_stoppable(self)
        if hotkeys is not None and automation is not None:
            for action, key in STOP_BINDINGS:
                hotkeys.bind_system(action, key, automation.emergency_stop)
            hotkeys.bind_system(*PAUSE_BINDING, self.toggle_pause)
            if getattr(self, "_owns_hotkeys", False):
                hotkeys.start()
        try:
            self._run_loop()
        finally:
            if automation is not None:
                automation.unregister_stoppable(self)
                automation.release(self.name)
            if hotkeys is not None:
                hotkeys.unbind(PAUSE_BINDING[0])
                if getattr(self, "_owns_hotkeys", False):
                    hotkeys.stop()

    # ------------------------------------------------------------ plumbing
    def log(self, message):
        line = time.strftime("[%H:%M:%S] ") + message
        print(line)
        self._log_lines = getattr(self, "_log_lines", 0) + 1
        if self._log_lines % LOG_CHECK_EVERY == 1:       # also on the first line of every run
            rotate_log(LOG_FILE_PATH, LOG_MAX_BYTES)
        try:
            with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass            # a log that cannot be written must never stop the macro
        callback = getattr(self, "log_callback", None)
        if callback:
            try:
                callback(message)
            except Exception:
                pass

    def _notify_stats(self):
        callback = getattr(self, "stats_callback", None)
        if callback:
            try:
                callback(self.stats)
            except Exception:
                pass

    def _notify_frame(self, slot_id, crop, state, reason):
        callback = getattr(self, "frame_callback", None)
        if callback:
            try:
                callback(slot_id, crop, state, reason)
            except Exception:
                pass

    def stop(self):
        if not getattr(self, "running", False):
            return
        self.running = False
        self._on_stopped()
        hud = getattr(self, "hud", None)
        if hud:
            try:
                hud.close()
            except Exception:
                pass

    def toggle_pause(self):
        self.paused = not self.paused
        self._on_pause_changed()

    def sleep_interruptible(self, seconds):
        """Sleep in 20 ms steps; False as soon as the run stops or F6 is pressed."""
        stop_event = getattr(getattr(self, "_automation", None), "stop_event", None)
        start = time.time()
        while time.time() - start < seconds:
            if not self.running or (stop_event is not None and stop_event.is_set()):
                return False
            time.sleep(0.02)
        return True

    # ------------------------------------------------------------ stop signal for input
    def _should_stop(self):
        """True after F6/Esc (the shared stop event) or when this runner was stopped."""
        event = getattr(getattr(self, "_automation", None), "stop_event", None)
        return (not self.running) or bool(event is not None and event.is_set())

    def _connect_input_stop(self):
        """Let the input layer see the stop signal, so a click in progress is cut short."""
        self.input.stop_check = self._should_stop

    # ------------------------------------------------------------ purchase popup guard
    def _build_popup_guard(self, template_dir=None):
        """Create the guard that checks for a Robux purchase popup before every click."""
        from macro_studio.core.popup_guard import PopupGuard
        self.popup_guard = PopupGuard(
            getattr(self, "config", {}) or {},
            template_dir or scaling.get_resource_path("templates"),
            capture=self.vs.capture_client_full, click=self._guard_click, log=self.log,
            wait=self.sleep_interruptible, pause=self._guard_pause, stop=self._guard_stop,
            fallback_close=self._fallback_close_point)

    def _fallback_close_point(self):
        """A close point from the older per-macro settings, in 800x600 reference pixels."""
        return None

    def _popup_clear(self, hwnd):
        """True when it is safe to click `hwnd`: no purchase popup is covering the game."""
        guard = getattr(self, "popup_guard", None)
        return guard is None or guard.ensure_clear(hwnd)

    def _guard_pause(self):
        if not self.paused:
            self.paused = True
            self._on_pause_changed()

    def _guard_stop(self):
        self.stop()

    def _guard_click(self, hwnd, x, y):
        """Click a point in the client area for the guard. Never calls the guard itself."""
        if not self.wm.is_foreground(hwnd):
            self.wm.force_activate_window(hwnd, max_wait_sec=1.0, auto_shrink=False)
            if not self.wm.is_foreground(hwnd):
                return False
        offset = self.wm.get_client_offset(hwnd)
        return self.input.click(x, y, client_offset=offset, relative=True, hwnd=hwnd) is not False

    def _mapper(self, hwnd):
        """Scale calibrated points into this window's actual client area.

        Every coordinate was captured on an 800x600 client. When the window manager
        snaps the client to that size this is the identity mapping; when it cannot
        (small screen, DPI scaling, a resized client) it is the difference between a
        click on the button and a click a few pixels off it. An unreadable client size
        would collapse every coordinate, so it falls back to the calibrated numbers.
        """
        ref = (self._reference_config() or {}).get("reference_client", {})
        reference = (ref.get("width", 800), ref.get("height", 600))
        try:
            mapper = scaling.CoordinateMapper.for_window(hwnd, reference=reference)
            w, h = mapper.client_size
            if w <= 0 or h <= 0:
                return scaling.CoordinateMapper(reference, reference=reference)
            return mapper
        except Exception:
            return scaling.CoordinateMapper(reference, reference=reference)
