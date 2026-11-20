"""
Mutation macro runner - multi-account mutation loop.

Per account the game flow is:
    pet list is already on screen -> click a pet -> click Mutate
    -> the wheel spins on its own -> when it stops the pet list is back

So this runner does NOT sit and wait on one account. It kicks off a mutation
in every window, then cycles through them: any window whose wheel has stopped
gets its next pet mutated immediately, while the still-spinning ones are left
alone. That keeps every account busy instead of serialising on the slowest one.

Spin detection reads a single frame: the wheel REPLACES the pet list with a
large saturated colour wheel, so the two states are separable by how white and
how saturated the scan region is. That is cheaper than comparing two frames and
it cannot be fooled by a wheel that has stopped turning but is still on screen.

The interface mirrors main.MacroRunner so app_gui can drive either one.
"""

import os
import json
import time

import cv2
import numpy as np

from macro_studio.core import scaling
scaling.set_dpi_awareness()

from macro_studio.core.input_controller import InputController  # noqa: E402
from macro_studio.core.overlay_hud import FloatingOverlayHUD  # noqa: E402
from macro_studio.core.vision_scanner import VisionScanner  # noqa: E402
from macro_studio.core.window_manager import WindowManager  # noqa: E402
from macro_studio.runners.base import RunnerBase  # noqa: E402

BASE_DIR = scaling.get_base_dir()
MUTATION_CONFIG = scaling.get_resource_path("config_mutation.json")

# ---------------------------------------------------------------------------
# UI state detection
#
# The wheel is not a small widget inside the pet list - it REPLACES the whole
# panel with a large saturated colour wheel. So the two states are trivially
# separable from a single frame, and there is no need to sample two frames and
# look for movement. That also removes a real failure mode: a wheel that has
# stopped turning but is still on screen reads as "no motion" and would have
# been mistaken for a finished spin.
#
# Measured over 14 pet-list frames and the wheel, inside the calibrated region:
#     pet list : white 38.7-45.5%   saturation 71-92
#     wheel    : white  7.3%        saturation 142
# A 31-point gap, so 23% sits with ~15 points of margin on both sides.
# ---------------------------------------------------------------------------
PETLIST_WHITE_MIN = 23.0   # above this fraction of near-white pixels -> pet list
WHEEL_SAT_MIN = 110.0      # dark-but-saturated -> the wheel is up

STATE_PET_LIST = "PET_LIST"
STATE_WHEEL = "WHEEL"
STATE_UNKNOWN = "UNKNOWN"


def ui_metrics(frame):
    """(near-white percentage, mean saturation) of a BGR frame."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    sat, val = hsv[:, :, 1], hsv[:, :, 2]
    white = float(np.mean((sat < 40) & (val > 170)) * 100)
    return white, float(sat.mean())


def detect_ui_state(frame):
    """
    Classify one frame of the scan region.

    Returns (state, white_pct, saturation). UNKNOWN means neither the pet list
    nor the wheel is showing - usually the modal was closed - and the caller
    should say so rather than quietly waiting forever.
    """
    if frame is None or frame.size == 0:
        return STATE_UNKNOWN, 0.0, 0.0
    white, sat = ui_metrics(frame)
    if white >= PETLIST_WHITE_MIN:
        return STATE_PET_LIST, white, sat
    if sat >= WHEEL_SAT_MIN:
        return STATE_WHEEL, white, sat
    return STATE_UNKNOWN, white, sat


class WindowState:
    """Per-account bookkeeping for one Roblox window."""

    def __init__(self, hwnd, label):
        self.hwnd = hwnd
        self.label = label
        self.mutations = 0
        self.last_action = 0.0
        self.status = "PENDING"
        self.reason = "not started yet"
        # True once a pet has been clicked but its Mutate press has not yet
        # been confirmed. Guards against stacking sacrifice pets.
        self.pet_selected = False
        self.retries = 0


class MutationRunner(RunnerBase):
    name = "mutation"

    def __init__(self, config_data=None, log_callback=None, stats_callback=None,
                 frame_callback=None, max_cycles=None, hotkeys=None, automation=None,
                 tab_selection=None):
        # stats "claims" is reused by the GUI for mutations started, "adds" for
        # spins completed.
        self._init_runtime(log_callback=log_callback, stats_callback=stats_callback,
                           frame_callback=frame_callback, max_cycles=max_cycles,
                           hotkeys=hotkeys, automation=automation,
                           tab_selection=tab_selection)

        self.config = config_data or {}
        self.mutation_cfg = self._load_mutation_config()

        w_cfg = self.config.get("window_settings", {})
        self.wm = WindowManager(
            target_procs=w_cfg.get("target_process_names",
                                   ["robloxplayerbeta.exe", "roblox.exe",
                                    "windows10universal.exe"]),
            ignore_procs=w_cfg.get("ignore_process_names",
                                   ["roblox account manager.exe", "chrome.exe",
                                    "msedge.exe", "brave.exe", "firefox.exe"]),
            width=w_cfg.get("standard_width", 800),
            height=w_cfg.get("standard_height", 600),
        )
        self.auto_arrange = w_cfg.get("auto_arrange_windows", True)
        self.auto_shrink = w_cfg.get("auto_shrink_to_minimum", True)
        self.layout_style = w_cfg.get("layout_style", "diagonal")
        self.offset_x = w_cfg.get("diagonal_offset_x", w_cfg.get("diagonal_gap", 10))
        self.offset_y = w_cfg.get("diagonal_offset_y", w_cfg.get("diagonal_gap", 10))
        self.grid_cols = w_cfg.get("grid_columns", 2)

        delays = self.config.get("delays", {})
        self.focus_delay = float(delays.get("window_focus_delay", 0.6))
        self.after_pet_delay = float(delays.get("between_slots_delay", delays.get("after_pet_select_delay", 0.35)))
        self.after_mutate_delay = float(delays.get("after_mutate_delay", 0.4))
        self.poll_delay = float(delays.get("cycle_delay", delays.get("mutation_poll_delay", 2.0)))

        self.vs = VisionScanner(self.config)
        self.input = InputController(click_delay=delays.get("click_delay", min(0.35, max(0.05, self.after_pet_delay))))
        self._connect_input_stop()
        self._build_popup_guard()

        self.windows = []

        if self.log_callback is None and self.config.get("ui", {}).get(
                "show_floating_stop_button", True):
            self.hud = FloatingOverlayHUD(on_stop_callback=self.stop,
                                          on_pause_callback=self.toggle_pause)
            self.hud.start()

    # ---------------- config ----------------
    def _load_mutation_config(self):
        """Resolve the coordinates this run will click, and say where from.

        This used to fall back to config_mutation.json silently. That meant a
        profile without `targets` - a Goliath profile, or a copy made before
        calibration - would run against coordinates from a completely different
        file, clicking points the user never saw in the UI. Every source switch
        is now logged, and a Goliath profile is refused outright instead of
        being quietly swapped for something clickable.
        """
        mode = self.config.get("meta", {}).get("macro_mode")
        if mode == "goliath":
            raise ValueError(
                "This is a Goliath profile - the mutation macro cannot run it. "
                "Switch to a mutation profile before starting."
            )

        if self.config.get("targets") and self.config.get("scan_region"):
            return self.config

        if os.path.isfile(MUTATION_CONFIG):
            try:
                with open(MUTATION_CONFIG, "r", encoding="utf-8") as f:
                    fallback = json.load(f)
                self.log("[CONFIG] Profile has no calibrated targets; falling back "
                         "to config_mutation.json. Re-run 'Calibrate Mutation' to "
                         "store them in the profile itself.")
                return fallback
            except Exception as e:
                self.log(f"[CONFIG] config_mutation.json unreadable ({e}).")

        raise ValueError(
            "No mutation targets found. Run 'Calibrate Mutation' before starting."
        )

    # ---------------- RunnerBase hooks ----------------
    def _reference_config(self):
        """Mutation coordinates come from the resolved mutation config."""
        return self.mutation_cfg

    def on_emergency_stop(self):
        self.log("Killswitch pressed - stopping.")
        self.stop()

    def _on_pause_changed(self):
        self.log("Paused." if self.paused else "Resumed.")

    # ---------------- vision ----------------
    def _capture_scan_region(self, hwnd, mapper):
        """Grab just the calibrated scan region from a window's client area."""
        full = self.vs.capture_client_full(hwnd)
        if full is None:
            return None
        r = mapper.region(self.mutation_cfg.get("scan_region", {}))
        h, w = full.shape[:2]
        x1 = max(0, min(w - 1, r["x"]))
        y1 = max(0, min(h - 1, r["y"]))
        x2 = max(x1 + 1, min(w, r["x"] + r["width"]))
        y2 = max(y1 + 1, min(h, r["y"] + r["height"]))
        return full[y1:y2, x1:x2]

    def read_state(self, hwnd, mapper):
        """
        Classify what is on screen right now from a single frame.

        Returns (state, white_pct, saturation, frame).
        """
        frame = self._capture_scan_region(hwnd, mapper)
        state, white, sat = detect_ui_state(frame)
        return state, white, sat, frame

    # ---------------- actions ----------------
    def _focus(self, state):
        """Bring a window to the real foreground before any click is sent.

        Mouse input is window-agnostic: a click lands on whatever is in front
        at the moment it is injected, not on the window we meant. So if
        activation fails and we click anyway, the pet select and Mutate presses
        go into someone else's game window. A failed activation therefore skips
        the account for this pass instead of clicking blind; the next round
        tries it again.
        """
        if not self.wm.force_activate_window(state.hwnd):
            state.status = "STUCK"
            state.reason = "could not bring the window to the front"
            self.log(f"{state.label}: window would not come to the front - "
                     f"skipping it this pass.")
            return False
        self.sleep_interruptible(self.focus_delay)
        return True

    def _click_target(self, hwnd, mapper, key):
        pt = self.mutation_cfg.get("targets", {}).get(key)
        if not pt:
            self.log(f"No calibrated point for '{key}' - run calibrate_mutation.py")
            return False
        # A Robux purchase popup may have opened since the last look; this click could land
        # on its Buy button. The guard closes it (or pauses/stops) and says when to hold back.
        if not self._popup_clear(hwnd):
            self.log(f"{key}: a purchase pop-up is in the way - click not sent.")
            return False
        x, y = mapper.point(pt["x"], pt["y"])
        # Client offset, NOT window offset. Every calibrated point is measured
        # from the top-left of the game's render area, but the window frame
        # starts a title bar and a border earlier - (8, 31) on Windows 11 at
        # 100% scaling. Adding the frame origin instead put every mutation
        # click 8px left and 31px high: the Mutate press landed on the pet row
        # above the button, which reads as "it clicked a pet slot again".
        # Re-read per click, because a window that moved mid-pass would send
        # every remaining click to the wrong place on screen.
        ox, oy = self.wm.get_client_offset(hwnd)
        landed = self.input.click(x, y, client_offset=(ox, oy), relative=True, hwnd=hwnd)
        if landed is False:
            self.log(f"{key}: cursor could not be placed on target - click not sent.")
            return False
        return True

    def start_mutation(self, state):
        """
        Select one pet and press Mutate on a focused window.

        Clicking a pet ADDS it to the mutation as a sacrifice - it does not
        replace a previous selection. So a pet click must never be repeated
        while an earlier selection is still pending, or pets pile up and get
        consumed silently. `state.pet_selected` tracks that: if a previous
        Mutate press did not take, the retry presses Mutate only.
        """
        mapper = self._mapper(state.hwnd)

        if not state.pet_selected:
            if not self._click_target(state.hwnd, mapper, "pet_select"):
                state.status = "ERROR"
                state.reason = "pet_select not calibrated"
                return False
            state.pet_selected = True
            self.sleep_interruptible(self.after_pet_delay)
        else:
            self.log(f"{state.label}: pet already selected, pressing Mutate only "
                     f"(not selecting a second pet)")

        if not self._click_target(state.hwnd, mapper, "mutate"):
            state.status = "ERROR"
            state.reason = "mutate not calibrated"
            return False

        # Poll for the wheel to appear (gives server response and animation 2.5s to start)
        wheel_started = False
        start_poll = time.time()
        after_state = STATE_UNKNOWN
        white = 0.0
        while time.time() - start_poll < 2.5 and self.running:
            self.sleep_interruptible(0.12)
            after_state, white, _sat, _f = self.read_state(state.hwnd, mapper)
            if after_state == STATE_WHEEL:
                wheel_started = True
                break

        if not wheel_started:
            state.retries = getattr(state, "retries", 0) + 1
            if state.retries >= 2:
                # If Mutate failed repeatedly, reset pet_selected to allow a clean retry from pet selection
                state.pet_selected = False
                state.retries = 0
                self.log(f"{state.label}: Mutate retry limit reached - resetting pet selection to re-select")
            else:
                state.pet_selected = True
            state.status = "STUCK"
            state.reason = f"Mutate did not take (still {after_state}, white {white:.0f}%)"
            self.log(f"{state.label}: Mutate did not register (screen is still "
                     f"{after_state}) - will retry")
            return False

        state.retries = 0
        state.pet_selected = False
        state.mutations += 1
        state.last_action = time.time()
        state.status = "SPINNING"
        state.reason = f"mutation #{state.mutations} started"
        self.stats["claims"] += 1
        return True

    # ---------------- main loop ----------------
    def _discover_windows(self):
        hwnds = self._selected_windows(self.wm.find_windows())
        if not hwnds:
            return []

        if self.auto_shrink:
            for h in hwnds:
                try:
                    self.wm.shrink_to_minimum(h)
                except Exception:
                    pass
        if self.auto_arrange:
            try:
                self.wm.auto_arrange(
                    hwnds,
                    style=self.layout_style,
                    offset_x=self.offset_x,
                    offset_y=self.offset_y,
                    cols=self.grid_cols
                )
            except Exception:
                pass

        states = []
        for i, h in enumerate(hwnds):
            title = self.wm.get_window_title(h)
            states.append(WindowState(h, f"Account #{i + 1}"))
            self.log(f"  {states[-1].label}: HWND {h} ('{title[:28]}')")
        return states

    def _run_loop(self):
        self.log("=== MUTATION MODE ===")
        self.log(scaling.describe_environment())

        self.windows = self._discover_windows()
        if not self.windows:
            self.log("No Roblox windows found. Launch the game and try again.")
            return

        self.stats["accounts"] = len(self.windows)
        self._notify_stats()
        self.log(f"Arranged {len(self.windows)} account(s). Starting first pass.")

        # First pass: kick off a mutation in every window.
        for state in self.windows:
            if not self.running:
                break
            while self.paused and self.running:
                time.sleep(0.1)

            if not self._focus(state):
                self._notify_frame(self._slot_id(state), None,
                                   state.status, state.reason)
                continue

            self.stats["active_hwnd"] = state.hwnd
            self._notify_stats()

            mapper = self._mapper(state.hwnd)
            if not mapper.is_identity:
                self.log(f"{state.label}: {mapper.describe()}")

            if self.start_mutation(state):
                self.log(f"{state.label}: mutation started")
            self._notify_frame(self._slot_id(state), None, state.status, state.reason)

        # Steady state: cycle the windows, re-mutating any whose wheel stopped.
        while self.running:
            while self.paused and self.running:
                time.sleep(0.1)
            if not self.running:
                break

            self.stats["cycles"] += 1
            if self.max_cycles and self.stats["cycles"] > self.max_cycles:
                self.log("Reached max cycles - stopping.")
                break

            for state in self.windows:
                if not self.running:
                    break
                while self.paused and self.running:
                    time.sleep(0.1)

                if not self._focus(state):
                    self._notify_frame(self._slot_id(state), None,
                                       state.status, state.reason)
                    continue

                self.stats["active_hwnd"] = state.hwnd

                mapper = self._mapper(state.hwnd)
                ui_state, white, sat, frame = self.read_state(state.hwnd, mapper)

                if ui_state == STATE_WHEEL:
                    state.status = "SPINNING"
                    state.reason = f"wheel is up (white {white:.0f}%, sat {sat:.0f})"
                elif ui_state == STATE_PET_LIST:
                    self.stats["adds"] += 1
                    state.status = "READY"
                    state.reason = f"pet list showing (white {white:.0f}%)"
                    self.log(f"{state.label}: spin finished - mutating again")
                    self.start_mutation(state)
                else:
                    state.status = "UNKNOWN"
                    state.reason = (f"neither pet list nor wheel "
                                    f"(white {white:.0f}%, sat {sat:.0f})")
                    self.log(f"{state.label}: screen shows neither the pet list nor "
                             f"the wheel - is the Mutate menu still open?")

                self._notify_frame(self._slot_id(state), frame, state.status, state.reason)
                self._notify_stats()

            if self.poll_delay > 0:
                self.log(f"Round #{self.stats['cycles']} complete. Waiting {self.poll_delay:.1f}s before next check...")
                self.sleep_interruptible(self.poll_delay)

        self.log("=== MUTATION MODE STOPPED ===")
        self.stats["active_hwnd"] = 0
        self._notify_stats()
        if self.hud:
            try:
                self.hud.close()
            except Exception:
                pass

    def _slot_id(self, state):
        """Map a window onto a GUI preview card slot (1-based)."""
        try:
            return self.windows.index(state) + 1
        except ValueError:
            return 1


def main():
    runner = MutationRunner()
    try:
        runner.run()
    except KeyboardInterrupt:
        runner.stop()


if __name__ == "__main__":
    main()
