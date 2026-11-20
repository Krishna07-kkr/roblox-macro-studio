"""
Main Orchestrator Module
Runs the multi-account pet claim & auto-add automation loop.
Features:
- Pure Process-Based Detection (Only targets actual RobloxPlayerBeta.exe / Roblox.exe)
- Explicitly ignores browser tabs and Roblox Account Manager
- Bulletproof Window Activation: Automatically brings game window to front and forces true OS focus
- Dual-Delivery Clicks: Hardware cursor events + PostMessage window queue
- Cached Layout: Sized to standard smallest window size ONCE; no aggressive re-shuffling
- Calm, human-like execution cadence
- Unstoppable Emergency Stop: High-frequency GetAsyncKeyState watchdog (F6, ESC)
- Floating draggable Always-on-Top Stop & Pause overlay button
- UI/GUI Integration with live log callbacks, metric counters, and camera feed
- Auto-saving file logging to 'debug_log.txt'
- CLOSED-LOOP VERIFICATION: Captures Before/After screenshots and verifies visual state changes.
"""

import os
import sys
import json
import time
import winsound

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from macro_studio.core import scaling
from macro_studio.core.input_controller import InputController
from macro_studio.core.overlay_hud import FloatingOverlayHUD
from macro_studio.core.vision_scanner import VisionScanner, SlotState
from macro_studio.core.window_manager import WindowManager
from macro_studio.runners.base import RunnerBase
from macro_studio.runners.claim_scheduler import ClaimScheduler

CONFIG_PATH = scaling.get_resource_path("config.json")


class MacroRunner(RunnerBase):
    name = "goliath"

    def __init__(self, config_data=None, log_callback=None, stats_callback=None,
                 frame_callback=None, max_cycles=None, hotkeys=None, automation=None,
                 tab_selection=None):
        self._init_runtime(log_callback=log_callback, stats_callback=stats_callback,
                           frame_callback=frame_callback, max_cycles=max_cycles,
                           hotkeys=hotkeys, automation=automation,
                           tab_selection=tab_selection)

        if config_data:
            self.config = config_data
        else:
            self.config = self.load_config()

        w_cfg = self.config.get("window_settings", {})
        self.wm = WindowManager(
            target_procs=w_cfg.get("target_process_names", ["robloxplayerbeta.exe", "roblox.exe", "windows10universal.exe"]),
            ignore_procs=w_cfg.get("ignore_process_names", ["roblox account manager.exe", "chrome.exe", "msedge.exe", "brave.exe", "firefox.exe"]),
            width=w_cfg.get("standard_width", 800),
            height=w_cfg.get("standard_height", 600)
        )
        self.auto_arrange = w_cfg.get("auto_arrange_windows", True)
        self.auto_shrink = w_cfg.get("auto_shrink_to_minimum", True)
        self.layout_style = w_cfg.get("layout_style", "diagonal")
        self.offset_x = w_cfg.get("diagonal_offset_x", w_cfg.get("diagonal_gap", 10))
        self.offset_y = w_cfg.get("diagonal_offset_y", w_cfg.get("diagonal_gap", 10))
        self.grid_cols = w_cfg.get("grid_columns", 2)
        self.use_relative = w_cfg.get("use_window_relative_coords", True)

        # Per-account claim timers. Goliath only - the mutation loop has no
        # claim step and keeps its own pacing in mutation_runner.py.
        self.scheduler = ClaimScheduler.from_config(self.config, log=self.log)

        fill_cfg = self.config.get("slot_fill", {})
        self.verify_fill = bool(fill_cfg.get("verify_all_slots", True))
        self.fill_retries = int(fill_cfg.get("retries", 2))
        # Save the whole client frame when an action does not verify, so an
        # unexpected pop-up leaves evidence instead of just a failed step.
        self.capture_failures = bool(fill_cfg.get("capture_failures", True))
        self.dismiss_popups = bool(fill_cfg.get("dismiss_popups", True))

        d = self.config.get("delays", {})
        # Every step of the add path gets its own wait. The UI animates between
        # these clicks; clicking into a menu that has not finished opening is
        # how a click lands on the wrong thing.
        self.d_settle = float(d.get("ui_settle_delay", 0.25))
        self.d_after_slot = float(d.get("after_slot_click_delay", d.get("click_delay", 0.40)))
        self.d_after_pet = float(d.get("after_pet_select_delay", 0.50))
        self.d_after_confirm = float(d.get("after_confirm_delay", 0.80))
        self.d_after_claim = float(d.get("after_claim_delay", 1.0))

        self.vs = VisionScanner(self.config)
        self.input = InputController(
            click_delay=self.config.get("delays", {}).get("click_delay", 0.35)
        )
        self._connect_input_stop()
        self._build_popup_guard()

        # Only spawn floating HUD in standalone CLI mode (prevent duplicate Tk thread when app_gui is running)
        if self.log_callback is None and self.config.get("ui", {}).get("show_floating_stop_button", True):
            self.hud = FloatingOverlayHUD(
                on_stop_callback=self.stop,
                on_pause_callback=self.toggle_pause
            )
            self.hud.start()

    def load_config(self):
        if not os.path.exists(CONFIG_PATH):
            raise FileNotFoundError(f"Configuration file not found: {CONFIG_PATH}")
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    # ------------------------------------------------------------ RunnerBase hooks
    def _on_stopped(self):
        try:
            winsound.Beep(1200, 150)
            winsound.Beep(800, 200)
        except Exception:
            pass
        self.log("🛑 [EMERGENCY STOP TRIGGERED] Macro stopped immediately by user!")

    def _on_pause_changed(self):
        state = "PAUSED ⏸️" if self.paused else "RESUMED ▶️"
        try:
            winsound.Beep(900 if self.paused else 1400, 100)
        except Exception:
            pass
        self.log(f"[HOTKEY] Macro {state}")

    def _map_slot(self, slot, mapper):
        """A copy of a slot with every coordinate scaled to this window."""
        if mapper.is_identity:
            return slot
        mapped = dict(slot)
        for key in ("slot_click", "claim_click"):
            if slot.get(key):
                x, y = mapper.point_dict(slot[key])
                mapped[key] = {"x": x, "y": y}
        if slot.get("scan_region"):
            mapped["scan_region"] = mapper.region(slot["scan_region"])
        return mapped

    def _map_actions(self, common_actions, mapper):
        if mapper.is_identity:
            return common_actions
        mapped = {}
        for name, pt in (common_actions or {}).items():
            if isinstance(pt, dict) and "x" in pt:
                x, y = mapper.point_dict(pt)
                mapped[name] = {"x": x, "y": y}
            else:
                mapped[name] = pt
        return mapped

    def _fallback_close_point(self):
        """The Goliath 'popup_close' point, when the guard has no close button of its own."""
        point = (self.config.get("common_actions") or {}).get("popup_close") or {}
        if point.get("x") or point.get("y"):
            return point.get("x", 0), point.get("y", 0)
        return None

    def _click_point(self, hwnd, point, label, what):
        """Click one calibrated point, refusing to click into the wrong window.

        The client offset is re-read for every click because an account pass
        takes seconds, and a window that moved in between would send every
        remaining click to the wrong place on screen.
        """
        if not point or "x" not in point:
            self.log(f"      [!] {label}: no coordinate for {what} - skipping.")
            return False

        # A Robux purchase popup may have opened since the last look; this click could land
        # on its Buy button. The guard closes it (or pauses/stops) and says when to hold back.
        if not self._popup_clear(hwnd):
            self.log(f"      [!] {label}: a purchase pop-up is in the way - "
                     f"not clicking {what}.")
            return False

        if not self.wm.is_foreground(hwnd):
            self.wm.force_activate_window(hwnd, max_wait_sec=1.0, auto_shrink=False)
            if not self.wm.is_foreground(hwnd):
                self.log(f"      [!] {label}: window is not in front - "
                         f"not clicking {what}.")
                return False

        offset = self.wm.get_client_offset(hwnd)
        landed = self.input.click(point["x"], point["y"], offset,
                                  relative=self.use_relative, hwnd=hwnd)
        if landed is False:
            self.log(f"      [!] {label}: could not place the cursor for {what} "
                     f"- click not sent.")
            return False
        return True

    def _run_loop(self):
        slots = self.config.get("slots", [])
        common_actions = self.config.get("common_actions", {})
        delays = self.config.get("delays", {})

        self.log("=" * 68)
        self.log("🤖 MULTI-ACCOUNT PET AUTO-CLAIM & AUTO-ADD (CLOSED-LOOP VISION)")
        self.log("=" * 68)
        self.log("Features: Auto-Tab Force Activation + Closed-Loop Vision Verification")
        self.log(f"Slots Configured: {len(slots)} slots per account")
        for s in slots:
            self.log(f"  - {s['name']}: SlotClick={s['slot_click']}, ClaimClick={s['claim_click']}")
        self.log("-" * 68)
        self.log("🛑 EMERGENCY STOP: Press [F6] or [ESC] or click Red On-Screen Button!")
        self.log("⏸️ PAUSE / RESUME: Press [F7]")
        self.log("=" * 68 + "\n")

        try:
            winsound.Beep(1000, 120)
        except Exception:
            pass

        cycle_count = 0

        while self.running:
            if self.paused:
                self.sleep_interruptible(0.2)
                continue

            cycle_count += 1
            self.stats["cycles"] = cycle_count
            self._notify_stats()

            self.log(f"\n🔄 [CYCLE #{cycle_count}] Scanning Roblox Game Accounts...")

            windows = self._selected_windows(self.wm.find_windows())
            self.stats["accounts"] = len(windows)
            self._notify_stats()

            if not windows:
                if getattr(self, "_tab_summary", (0, 0))[1]:
                    self.log("[!] None of the selected tabs is open. Pick tabs in "
                             "Overview > Tabs. Retrying in 4s...")
                else:
                    self.log("[!] No active Roblox game clients found. Retrying in 4s...")
                self.sleep_interruptible(4.0)
                continue

            self.log(f"[INFO] Detected {len(windows)} active Roblox game client window(s).")

            # Drop timers for clients that were closed since the last cycle, so
            # a relaunched account is scanned instead of waiting on a dead PID. Counted from every
            # open client: a minimized or unticked one keeps its timers.
            open_clients = self.wm.find_windows(include_minimized=True)
            self.scheduler.prune([p for p in (self.wm.get_pid(h) for h in open_clients) if p])

            # One-shot batch shrink upon discovery (saves CPU and prevents repetitive redraws)
            if self.auto_shrink:
                shrunk_count = self.wm.shrink_all_windows_once(windows)
                if shrunk_count > 0:
                    self.log(f"   ⚡ [POWER SAVE] One-shot shrunk {shrunk_count} game window(s) to smallest size (800x600 client).")

            if self.auto_arrange:
                self.wm.auto_arrange(
                    windows,
                    style=self.layout_style,
                    offset_x=self.offset_x,
                    offset_y=self.offset_y,
                    cols=self.grid_cols
                )

            for acc_idx, hwnd in enumerate(windows):
                if not self.running:
                    break
                while self.paused and self.running:
                    self.sleep_interruptible(0.2)

                acc_label = f"Account #{acc_idx + 1}"
                pid = self.wm.get_pid(hwnd)

                # Skip accounts whose pets were only just added - no focus
                # steal, no clicks, until this account's own timer expires.
                if pid and not self.scheduler.is_due(pid):
                    self.log(f"   ⏱️ [{acc_label}] (HWND {hwnd}, PID {pid}) last action "
                             f"under {self._wait_label()} ago - next pass in "
                             f"{self.scheduler.describe_wait(pid)}. Skipping.")
                    continue

                self.stats["active_hwnd"] = hwnd
                self.stats["active_account"] = acc_label
                self.stats["active_title"] = self.wm.get_window_title(hwnd)
                self._notify_stats()

                self.log(f"\n>> [{acc_label}] (HWND: {hwnd}) Auto-activating game tab...")

                # 1. BULLETPROOF WINDOW ACTIVATION: Force True OS Foreground Focus (zero redraw overhead)
                activated = self.wm.force_activate_window(hwnd, max_wait_sec=1.5, auto_shrink=False)
                if not activated:
                    self.log(f"   [!] Windows focus could not be locked onto {acc_label}. Retrying...")
                    time.sleep(0.3)
                    self.wm.force_activate_window(hwnd, max_wait_sec=1.0, auto_shrink=False)

                self.log(f"   🎯 [{acc_label}] Tab Active & Focused! Ready to process slots.")

                if not self.sleep_interruptible(delays.get("window_focus_delay", 0.6)):
                    break

                # Scale the calibrated points into this window's real client
                # area. Identity when the client is the reference 800x600.
                mapper = self._mapper(hwnd)
                if not mapper.is_identity:
                    self.log(f"   📐 [{acc_label}] {mapper.describe()}")
                acct_slots = [self._map_slot(s, mapper) for s in slots]
                acct_actions = self._map_actions(common_actions, mapper)

                # Capture full client frame
                full_img = self.vs.capture_client_full(hwnd)
                if full_img is None:
                    self.log(f"   [!] Failed to capture {acc_label} frame. Skipping.")
                    continue

                pet_added = False
                for slot in acct_slots:
                    if not self.running:
                        break
                    while self.paused and self.running:
                        self.sleep_interruptible(0.2)

                    slot_name = slot.get("name", f"Slot_{slot.get('id', 1)}")

                    # State analysis
                    state, reason, slot_crop = self.vs.analyze_slot_advanced(full_img, slot)
                    self.log(f"   🔍 [{slot_name}] Status: {state} ({reason})")

                    # Send frame to UI live camera feed
                    self._notify_frame(slot.get("id", 1), slot_crop, state, reason)

                    if state == SlotState.CLAIM_READY and self._too_fresh_to_claim(pid, slot):
                        self.log(f"      ⏳ [{slot_name}] Pet went in less than "
                                 f"{self._wait_label()} ago (ready in "
                                 f"{self.scheduler.slot_wait(pid, slot.get('id'))}) - "
                                 f"not claiming.")
                    elif state == SlotState.CLAIM_READY:
                        pet_added |= bool(self._handle_claim_and_add(hwnd, slot, acct_actions, delays, acc_label, slot_name, slot_crop, pid))
                    elif state == SlotState.EMPTY_SLOT:
                        pet_added |= bool(self._handle_empty_slot(hwnd, slot, acct_actions, delays, acc_label, slot_name, slot_crop, pid))
                    elif state == SlotState.BUSY_TIMER:
                        self.log(f"      ⏳ [{slot_name}] Timer running. Moving to next slot.")
                    else:
                        self.log(f"      ❓ [{slot_name}] Unknown state. Skipping.")

                    if not self.sleep_interruptible(delays.get("between_slots_delay", 0.5)):
                        break

                # Before switching tabs: is anything still unclaimed or empty?
                # A missed claim or a missed add is otherwise silent - the
                # account just looks finished and gets skipped for the timer.
                settled = True
                if self.verify_fill and self.running:
                    settled, swept_added = self._verify_account_settled(
                        hwnd, acct_slots, acct_actions, delays, acc_label, pid)
                    pet_added |= swept_added

                if pid and pet_added and settled:
                    rec = self.scheduler.mark_pet_added(
                        pid, hwnd=hwnd, label=acc_label,
                        title=self.stats.get("active_title"))
                    self.log(f"   ⏱️ [{acc_label}] (HWND {hwnd}, PID {pid}) pets in at "
                             f"{rec['last_action']}. Next claim pass at "
                             f"{rec['next_claim']}.")
                elif pid and pet_added and not settled:
                    # Do not start the timer on an account that still has work
                    # pending, or it would be ignored for the whole wait.
                    self.log(f"   ⏱️ [{acc_label}] Still has pending work - "
                             f"no claim timer set, will retry next cycle.")

                if not self.sleep_interruptible(0.4):
                    break

            if self.max_cycles and cycle_count >= self.max_cycles:
                self.log(f"\n[GOAL REACHED] Completed configured {self.max_cycles} cycle(s).")
                break

            cycle_wait = delays.get("cycle_delay", 6.0)
            self.log(f"\n[CYCLE #{cycle_count} COMPLETE] Waiting {cycle_wait}s before next round...")
            self.sleep_interruptible(cycle_wait)

        self.log("\n[SHUTDOWN] Macro has terminated cleanly.")

    def _capture_evidence(self, hwnd, tag, acc_label):
        """Screenshot the whole client so a stray pop-up can be identified later."""
        if not self.capture_failures:
            return None
        frame = self.vs.capture_client_full(hwnd)
        path = self.vs.save_full_capture(frame, tag)
        if path:
            self.log(f"      📸 [{acc_label}] Saved a screenshot of what is on "
                     f"screen: {os.path.basename(path)}")
        return path

    def _dismiss_popup(self, hwnd, acct_actions, acc_label):
        """Close a stray pop-up - but only via a point the user calibrated.

        A pop-up can be a Robux purchase prompt, so clicking a guessed pixel to
        get rid of it is not acceptable: the wrong pixel could confirm a
        purchase. This clicks 'popup_close' when the profile defines it, and
        otherwise does nothing at all and says so.
        """
        if not self.dismiss_popups:
            return False
        guard = getattr(self, "popup_guard", None)
        if guard is not None and guard.active:
            # The guard already looks for the popup before every click, so this blind
            # click at a fixed point (which fires even when nothing is open) is not needed.
            return False
        point = (acct_actions or {}).get("popup_close")
        # 0,0 is the "not calibrated" value the UI writes. Treat it as absent
        # rather than clicking the top-left corner of the game window.
        if point and not (point.get("x") or point.get("y")):
            point = None
        if not point or "x" not in point:
            self.log(f"      [!] [{acc_label}] Something unexpected is on screen. "
                     f"No 'popup_close' point is calibrated, so nothing will be "
                     f"clicked - calibrate one to let the macro close it.")
            return False

        self.log(f"      ✖️ [{acc_label}] Closing the pop-up at "
                 f"({point['x']}, {point['y']})...")
        if not self._click_point(hwnd, point, acc_label, "the pop-up close button"):
            return False
        self.sleep_interruptible(self.d_after_confirm)
        return True

    def _read_slot_states(self, hwnd, acct_slots):
        """One capture, then the state of every slot in it."""
        full_img = self.vs.capture_client_full(hwnd)
        if full_img is None:
            return None
        return [self.vs.analyze_slot_advanced(full_img, slot) for slot in acct_slots]

    def _too_fresh_to_claim(self, pid, slot):
        """True when this macro filled the slot less than the claim wait ago."""
        scheduler = getattr(self, "scheduler", None)
        if not pid or scheduler is None or not hasattr(scheduler, "slot_claimable"):
            return False
        return not scheduler.slot_claimable(pid, slot.get("id"))

    def _wait_label(self):
        minutes = getattr(getattr(self, "scheduler", None), "wait_seconds", 600) / 60
        return f"{minutes:g} min"

    def _pending_work(self, acct_slots, readings, pid=0):
        """Slots still needing action: (to_claim, to_fill).

        A slot this macro filled less than the claim wait ago is not pending even if
        it reads CLAIM_READY: the pet cannot have grown yet.
        """
        to_claim = [(slot, r) for slot, r in zip(acct_slots, readings)
                    if r[0] == SlotState.CLAIM_READY
                    and not self._too_fresh_to_claim(pid, slot)]
        to_fill = [(slot, r) for slot, r in zip(acct_slots, readings)
                   if r[0] == SlotState.EMPTY_SLOT]
        return to_claim, to_fill

    @staticmethod
    def _slot_names(pairs):
        return ", ".join(s.get("name", f"Slot {s.get('id', '?')}") for s, _ in pairs)

    def _verify_account_settled(self, hwnd, acct_slots, acct_actions, delays, acc_label, pid=0):
        """Leave nothing half-done on this account before moving to the next tab.

        Both halves of the pass can fail quietly: a claim click that misses
        leaves the pet sitting there ready, and an add that misses leaves the
        slot empty. Neither says anything - the account just looks done. So
        before switching tabs, every slot is read again and anything still
        CLAIM_READY is claimed, anything still EMPTY is filled.

        Returns (settled, pets_added):
            settled     - nothing left to claim and nothing left empty
            pets_added  - a pet went into a slot during this sweep
        """
        pets_added = False

        for attempt in range(1, max(1, self.fill_retries) + 1):
            if not self.running:
                return False, pets_added

            readings = self._read_slot_states(hwnd, acct_slots)
            if readings is None:
                self.log(f"   [!] [{acc_label}] Could not capture the window to "
                         f"check slots.")
                return False, pets_added

            to_claim, to_fill = self._pending_work(acct_slots, readings, pid)
            if not to_claim and not to_fill:
                self.log(f"   ✅ [{acc_label}] Nothing pending - "
                         f"{len(acct_slots)}/{len(acct_slots)} slots filled, "
                         f"nothing left to claim.")
                return True, pets_added

            pending = []
            if to_claim:
                pending.append(f"to claim: {self._slot_names(to_claim)}")
            if to_fill:
                pending.append(f"still empty: {self._slot_names(to_fill)}")
            self.log(f"   ⚠️ [{acc_label}] Not done yet - {'; '.join(pending)} "
                     f"(retry {attempt}/{self.fill_retries})")

            # Work did not take on the first pass. Something may be sitting on
            # top of the UI - close it (if a safe point is calibrated) before
            # clicking into the same place again.
            if attempt > 1:
                self._capture_evidence(hwnd, f"retry_{attempt}", acc_label)
                self._dismiss_popup(hwnd, acct_actions, acc_label)

            # Claim first: claiming empties the slot, and the same handler
            # refills it, so this also clears work the fill pass would find.
            for slot, reading in to_claim:
                if not self.running:
                    return False, pets_added
                slot_name = slot.get("name", f"Slot_{slot.get('id', 1)}")
                pets_added |= bool(self._handle_claim_and_add(
                    hwnd, slot, acct_actions, delays, acc_label, slot_name,
                    reading[2], pid))
                if not self.sleep_interruptible(delays.get("between_slots_delay", 0.5)):
                    return False, pets_added

            for slot, reading in to_fill:
                if not self.running:
                    return False, pets_added
                slot_name = slot.get("name", f"Slot_{slot.get('id', 1)}")
                pets_added |= bool(self._add_pet_to_slot(
                    hwnd, slot, acct_actions, delays, acc_label, slot_name,
                    reading[2], pid=pid))
                if not self.sleep_interruptible(delays.get("between_slots_delay", 0.5)):
                    return False, pets_added

        # Final read, so the log states the outcome rather than the last attempt.
        readings = self._read_slot_states(hwnd, acct_slots)
        if readings is None:
            return False, pets_added

        to_claim, to_fill = self._pending_work(acct_slots, readings, pid)
        if to_claim or to_fill:
            left = []
            if to_claim:
                left.append(f"unclaimed: {self._slot_names(to_claim)}")
            if to_fill:
                left.append(f"empty: {self._slot_names(to_fill)}")
            self.log(f"   ❌ [{acc_label}] Gave up after {self.fill_retries} retries - "
                     f"{'; '.join(left)}")
            return False, pets_added

        self.log(f"   ✅ [{acc_label}] Nothing pending - "
                 f"{len(acct_slots)}/{len(acct_slots)} slots filled, "
                 f"nothing left to claim.")
        return True, pets_added

    def _handle_claim_and_add(self, hwnd, slot, common_actions, delays, acc_label, slot_name, before_crop, pid=0):
        """Claim a ready pet then refill the slot. Returns True if a pet went in."""
        claim_pt = slot.get("claim_click", {})
        self.log(f"      🎁 [{slot_name}] Claiming pet at ({claim_pt['x']}, {claim_pt['y']})...")
        self.sleep_interruptible(self.d_settle)
        if not self._click_point(hwnd, claim_pt, slot_name, "the Claim button"):
            return False

        if not self.sleep_interruptible(self.d_after_claim):
            return False

        # Verification step for Claim
        full_after = self.vs.capture_client_full(hwnd)
        _, _, after_crop = self.vs.analyze_slot_advanced(full_after, slot)
        is_verified, v_reason = self.vs.verify_action_success(before_crop, after_crop, "CLAIM", slot_name)

        if is_verified:
            self.stats["claims"] += 1
            self._notify_stats()
            self.log(f"      ✅ [VERIFIED CLAIM] {v_reason}")
            if pid:
                self.scheduler.mark_claimed(pid, hwnd=hwnd, label=acc_label)
        else:
            self.log(f"      ⚠️ [CLAIM UNVERIFIED] {v_reason}")
            self._capture_evidence(hwnd, f"claim_failed_{slot_name}", acc_label)

        # Add new pet back
        return self._add_pet_to_slot(hwnd, slot, common_actions, delays, acc_label, slot_name,
                                     pid=pid, claim_verified=is_verified)

    def _handle_empty_slot(self, hwnd, slot, common_actions, delays, acc_label, slot_name,
                           before_crop, pid=0):
        self.log(f"      ⭕ [{slot_name}] Slot is EMPTY! Adding new pet...")
        return self._add_pet_to_slot(hwnd, slot, common_actions, delays, acc_label, slot_name,
                                     before_crop, pid=pid)

    def _add_pet_to_slot(self, hwnd, slot, common_actions, delays, acc_label, slot_name,
                         before_crop=None, pid=0, claim_verified=False):
        """Put a pet into one slot. Returns True only on a verified add.

        A verified add is recorded per slot, so this pet is not claimed until it has had
        the full wait to grow (ClaimScheduler.slot_claimable).
        """
        state_before = None
        if before_crop is None:
            full_b = self.vs.capture_client_full(hwnd)
            state_before, _, before_crop = self.vs.analyze_slot_advanced(full_b, slot)

        # Each step waits for the previous menu to finish opening before the
        # next click goes out, and every click is refused unless the window is
        # in front and the cursor is verified on the exact pixel.

        # 1. Click slot to open pet inventory
        slot_pt = slot.get("slot_click", {})
        self.log(f"      👉 [{slot_name}] Clicking Slot at ({slot_pt['x']}, {slot_pt['y']})...")
        self.sleep_interruptible(self.d_settle)
        if not self._click_point(hwnd, slot_pt, slot_name, "the slot"):
            return False

        if not self.sleep_interruptible(self.d_after_slot):
            return False

        # 2. Click pet in inventory list
        pet_pt = common_actions.get("pet_select", {})
        self.log(f"      🐾 [{slot_name}] Selecting Pet at ({pet_pt['x']}, {pet_pt['y']})...")
        self.sleep_interruptible(self.d_settle)
        if not self._click_point(hwnd, pet_pt, slot_name, "the pet"):
            return False

        if not self.sleep_interruptible(self.d_after_pet):
            return False

        # 3. Click 'Yes' to confirm addition
        yes_pt = common_actions.get("confirm_yes", {})
        self.log(f"      ✅ [{slot_name}] Confirming 'Yes' at ({yes_pt['x']}, {yes_pt['y']})...")
        self.sleep_interruptible(self.d_settle)
        if not self._click_point(hwnd, yes_pt, slot_name, "the Yes button"):
            return False

        if not self.sleep_interruptible(self.d_after_confirm):
            return False

        # 4. Closed-Loop Verification: Did the pet actually get added?
        full_after = self.vs.capture_client_full(hwnd)
        state_after, _, after_crop = self.vs.analyze_slot_advanced(full_after, slot)

        is_verified, v_reason = self.vs.verify_action_success(before_crop, after_crop, "ADD", slot_name)
        if is_verified:
            self.stats["adds"] += 1
            self._notify_stats()
            self.log(f"      ✨ [VERIFIED ADD] {v_reason} (Proof collage saved!)")
            # Start this slot's grow timer. A verified claim emptied the slot, so this
            # is a new pet even though the game keeps showing "ready" until it is in
            # (real run 21:16). Only an unverified claim that still reads "ready" before
            # and after the add means the old pet is still there - hiding it for the
            # whole wait would strand it.
            claim_did_not_take = (not claim_verified
                                  and state_before == SlotState.CLAIM_READY
                                  and state_after == SlotState.CLAIM_READY)
            scheduler = getattr(self, "scheduler", None)
            if (pid and not claim_did_not_take and scheduler is not None
                    and hasattr(scheduler, "mark_slot_added")):
                scheduler.mark_slot_added(pid, slot.get("id"))
        else:
            self.log(f"      ⚠️ [ADD NOT VERIFIED] {v_reason}")
            self._capture_evidence(hwnd, f"add_failed_{slot_name}", acc_label)
        return bool(is_verified)


if __name__ == "__main__":
    from macro_studio.cli import main as _cli_main
    _cli_main()
