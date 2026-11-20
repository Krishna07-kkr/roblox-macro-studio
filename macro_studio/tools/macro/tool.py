"""The macro tool: Goliath daycare and Pet Mutation macros, profiles and calibration.

Moved out of app_gui.py unchanged (T027). The shell owns the window, the navigation and
the page title; this tool contributes the profile switcher (sidebar), the mode / save /
status controls (top bar) and its three pages. Runners get the shell's shared hotkey
service and automation lock, so F6 / Esc / F7 are handled by one poller.
"""

import copy
import json
import os
import time
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog

from macro_studio.calibrators.launch import calibrator_command
from macro_studio.core import scaling
from macro_studio.core.fileio import write_json_atomic
from macro_studio.core.live_feed import LiveFeed, Overlay, empty_feed_message
from macro_studio.core.live_feed_view import LiveFeedView
from macro_studio.core.tab_picker import TabPicker
from macro_studio.core.tab_selection import TabSelection, snapshot_tabs
from macro_studio.core.vision_scanner import VisionScanner
from macro_studio.core.window_manager import WindowManager
from macro_studio.core import ui_theme as ui
from macro_studio.core.ui_theme import (
    CANVAS, SURFACE, SURFACE_2, SURFACE_3, HAIRLINE,
    INK, BODY, MUTE, LINK, WARNING, CYAN,
    SP_XXS, SP_XS, SP_SM, SP_MD, SP_LG, SP_XL,
    R_SM, R_MD, R_LG,
    Card, Button, Badge, Toggle, Field,
)
from macro_studio.profiles import ProfileManager
from macro_studio.runners.goliath import MacroRunner
from macro_studio.runners.mutation import MutationRunner
from macro_studio.tools.macro.popup_card import PopupCardMixin
from macro_studio.tools.macro.tab_actions import TabActions
from macro_studio.tools.macro.validation import FieldError, clean_profile_name, read_number

SIDEBAR_W = 232
CHEVRON = "  \u25be"        # marks a button that opens a menu
def _rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


class MacroTool(PopupCardMixin):
    page_titles = ["Overview", "Slots & Coordinates", "Settings"]
    is_macro_tool = True
    FEED_PAGE = "Overview"
    FEED_WINDOW_REFRESH_S = 2.0
    LOG_MAX_LINES = 2000       # the console keeps the newest lines; a long run cannot grow it

    def __init__(self, shell, ctx):
        self.shell = shell
        self.ctx = ctx
        self.root = shell.root

        self.pm = ProfileManager()
        self.profiles = self.pm.list_profiles()
        if "Goliath" in self.profiles:
            self.active_profile_name = "Goliath"
        elif "Mutation" in self.profiles:
            self.active_profile_name = "Mutation"
        else:
            self.active_profile_name = self.profiles[0] if self.profiles else "Goliath"
        self.current_cfg = self.pm.load_profile(self.active_profile_name)

        self.runner = None
        self.runner_thread = None
        self.is_running = False
        # Which client tabs the macros work on (Overview > Tabs). Shared with the
        # running macro, so changes apply on its next pass.
        self.tab_selection = TabSelection()
        self.tab_picker = None
        self._tab_pictures = {}     # last picture of each client, for minimized ones

        # Which macro the Start button launches. "goliath" is the original
        # 3-slot claim/add flow; "mutation" is the multi-account mutate loop.
        # Taken from the loaded profile so the toolbar can never disagree with
        # the data it is about to run.
        self.macro_mode = self.current_cfg.get("meta", {}).get("macro_mode", "goliath")
        if self.macro_mode not in self.MODES:
            self.macro_mode = "goliath"

        self.slot_ui_rows = []
        self.dirty = False            # edits not yet written to the profile file
        self._stop_requested = False
        self._scan_running = False
        self._popup_capturing = False

    # -----------------------------------------------------------
    # Tool contract (macro_studio/registry.py)
    # -----------------------------------------------------------
    def build_sidebar(self, parent):
        # Profile switcher (Vercel-style team switcher)
        ui.eyebrow(parent, "Profile", bg=CANVAS).pack(
            fill="x", padx=SP_MD + SP_XS, pady=(SP_XS, SP_XS))

        # One control for profile and mode: a profile decides which macro runs.
        self.btn_profile = Button(
            parent, self._profile_label() + CHEVRON, command=self._open_profile_menu,
            kind="secondary", width=SIDEBAR_W - 2 * SP_MD, height=34,
            page_bg=CANVAS, font=ui.F_BODY(),
        )
        self.btn_profile.pack(padx=SP_MD)
        self.lbl_mode = tk.Label(parent, text=self._mode_caption(), font=ui.F_CAPTION(),
                                 bg=CANVAS, fg=MUTE, anchor="w")
        self.lbl_mode.pack(fill="x", padx=SP_MD + SP_XS, pady=(SP_XXS, 0))

        prof_actions = tk.Frame(parent, bg=CANVAS)
        prof_actions.pack(fill="x", padx=SP_MD, pady=(SP_XS, SP_MD))
        Button(prof_actions, "New", command=self._create_new_profile, kind="ghost",
               height=28, page_bg=CANVAS, font=ui.F_CAPTION()).pack(side="left")
        Button(prof_actions, "Duplicate", command=self._duplicate_profile, kind="ghost",
               height=28, page_bg=CANVAS, font=ui.F_CAPTION()).pack(side="left")
        Button(prof_actions, "Delete", command=self._delete_profile, kind="ghost",
               height=28, page_bg=CANVAS, font=ui.F_CAPTION()).pack(side="left")

        tk.Frame(parent, bg=HAIRLINE, height=1).pack(fill="x", padx=SP_MD)

    def build_topbar(self, inner):
        # Secondary until there is something to save; Start Macro is the one primary.
        self.btn_save = Button(inner, "Save Profile", command=self._save_active_profile,
                               kind="secondary", width=128, height=32, page_bg=CANVAS)
        self.btn_save.pack(side="right", padx=(SP_SM, 0))
        self.lbl_unsaved = tk.Label(inner, text="", font=ui.F_CAPTION(), bg=CANVAS,
                                    fg=WARNING)
        self.lbl_unsaved.pack(side="right")

    def build_pages(self, frames):
        self.view_overview, self.view_slots, self.view_settings = frames
        self._init_overview()
        self._init_slots_view()
        self._init_settings_view()
        self.live_feed_size = self.docked_view.size
        self._feed_vs = VisionScanner(self.current_cfg)
        self._feed_wm = WindowManager()
        self.tab_actions = TabActions(self._feed_wm, lambda text: self.ctx.ui_call(
            lambda: self._append_log(text)))
        self._feed_windows, self._feed_windows_at = [], 0.0
        self._feed_minimized = 0       # counted on the scheduler thread, shown on the Tk thread
        self.live_feed = LiveFeed(
            capture=self._feed_vs.capture_client_full,
            pick_window=self._feed_window,
            overlay=self._feed_overlay,
            scheduler=self.ctx.scheduler,
            ui_call=self.ctx.ui_call,
            on_frame=self._show_feed_frame,
            name="live_feed:macro",
            max_size=self.docked_view.size,
        )

    # ---------------- live feed (Constitution VI) ----------------
    def _feed_window(self):
        """The account being worked while a macro runs, else the first client."""
        runner = self.runner
        if runner is not None and getattr(runner, "running", False):
            hwnd = runner.stats.get("active_hwnd")
            if hwnd:
                return hwnd
        now = time.monotonic()
        if now - self._feed_windows_at >= self.FEED_WINDOW_REFRESH_S:
            self._feed_windows = self.tab_selection.filter(self._feed_wm.find_windows(),
                                                           self._feed_wm.get_pid)
            self._feed_windows_at = now
            # With no client to show the feed says why. Counting minimized clients walks every
            # window on the desktop, so it happens here on the scheduler thread, never in
            # _show_feed_frame (Tk thread), where it froze the window.
            self._feed_minimized = 0 if self._feed_windows else self._count_minimized_clients()
        return self._feed_windows[0] if self._feed_windows else 0

    def _count_minimized_clients(self):
        try:
            return self._feed_wm.count_minimized_clients()
        except Exception:  # noqa: BLE001 - only wording for an empty feed; never worth a crash
            return 0

    def _feed_overlay(self):
        """Calibrated regions and click points of the loaded profile."""
        cfg = self.current_cfg
        ref = cfg.get("reference_client", {})
        overlay = Overlay(reference=(ref.get("width", 800), ref.get("height", 600)))

        def region(r, color, label):
            if r and r.get("width") and r.get("height"):
                overlay.regions.append((r.get("x", 0), r.get("y", 0), r["width"],
                                        r["height"], _rgb(color), label))

        def point(p, color, label):
            if p and (p.get("x") or p.get("y")):
                overlay.points.append((p["x"], p["y"], _rgb(color), label))

        if self._cfg_mode() == "mutation":
            region(cfg.get("scan_region"), CYAN, "scan")
            for name, p in (cfg.get("targets") or {}).items():
                point(p, WARNING, name.replace("_", " "))
            return overlay
        for s in cfg.get("slots", []):
            region(s.get("scan_region"), CYAN, s.get("name", ""))
            point(s.get("slot_click"), LINK, "")
            point(s.get("claim_click"), CYAN, "")
        for name, p in (cfg.get("common_actions") or {}).items():
            point(p, WARNING, name.replace("_", " "))
        return overlay

    def _show_feed_frame(self, hwnd, image):
        """Tk thread: put a LiveFeed frame on screen."""
        if image is None:
            self.live_view.show(None, empty_feed_message(self._feed_minimized))
            return
        runner = self.runner
        working = runner is not None and getattr(runner, "running", False)
        who = runner.stats.get("active_account", "") if working else ""
        status = f"working {who}".strip() if working else "idle"
        pid = self._feed_wm.get_pid(hwnd)      # 0 when it cannot be read; never raises
        client = f"PID {pid}" if pid else "Roblox client"
        self.live_view.show(image, f"Live  -  {client}  -  {status}")

    # ---------------- tabs picker ----------------
    def _tabs_label(self):
        pids = self.tab_selection.pids
        if pids is None:
            return "Tabs: All"
        return "Tabs: None" if not pids else f"Tabs: {len(pids)} selected"

    def _snapshot_tabs(self):
        """Scheduler thread: every open client with a picture of its screen."""
        wm = self._feed_wm
        return snapshot_tabs(lambda: wm.find_windows(include_minimized=True), wm.get_pid,
                             wm.get_window_title, self._feed_vs.capture_client_full,
                             is_minimized=wm.is_minimized, cache=self._tab_pictures)

    def toggle_tab_picker(self):
        if self.tab_picker is not None and not self.tab_picker.closed:
            self.tab_picker.close()
            return
        self.tab_picker = TabPicker(
            self.root, self.btn_tabs, self.tab_selection, snapshot=self._snapshot_tabs,
            scheduler=self.ctx.scheduler, ui_call=self.ctx.ui_call,
            on_change=self._on_tabs_changed, actions=self.tab_actions)

    def _on_tabs_changed(self):
        self.btn_tabs.set_text(self._tabs_label() + CHEVRON)
        self._feed_windows_at = 0.0          # live feed follows the new choice now
        picker = self.tab_picker
        open_count = len(picker.tabs) if picker is not None else 0
        self._append_log(f"Tabs: {self.tab_selection.describe(open_count)}")

    def on_view_changed(self, title):
        """Shell hook: capture only while the Overview page is on screen."""
        # A popped-out feed has its own window, so it stays live on every page.
        self.live_feed.set_visible(self.feed_window is not None or title == self.FEED_PAGE)
        # Save Profile belongs to the macro's own pages; elsewhere it only confuses.
        if title in self.page_titles:
            if not self.btn_save.winfo_ismapped():
                self.btn_save.pack(side="right", padx=(SP_SM, 0))
                self.lbl_unsaved.pack(side="right")
        else:
            self.btn_save.pack_forget()
            self.lbl_unsaved.pack_forget()

    def confirm_close(self):
        """Shell hook before the window closes. False keeps the app open."""
        return self._resolve_unsaved("closing")

    def start(self):
        # Messages from shared services (scheduler, hotkeys, settings) show in this
        # tool's log until the dedicated Logs page exists (FR-062).
        self._unsubscribe_log = self.ctx.events.subscribe(
            "log", lambda message, **_: self._append_log(message))
        self.live_feed.start()
        self.ctx.scheduler.add(self.COUNTDOWN_JOB, self._countdown_tick, interval_s=1.0,
                               run_now=True)

    def stop(self):
        self.ctx.scheduler.remove(self.COUNTDOWN_JOB)
        if self.tab_picker is not None:
            self.tab_picker.close()
        self.tab_actions.stop()
        self.live_feed.stop()
        unsubscribe = getattr(self, "_unsubscribe_log", None)
        if unsubscribe:
            unsubscribe()
        if self.runner:
            self.runner.stop()

    # ---------------- Overview ----------------
    def _init_overview(self):
        page = self.shell.scrollable(self.view_overview)

        # Control row
        controls = tk.Frame(page, bg=CANVAS)
        controls.pack(fill="x", padx=SP_XL, pady=(SP_LG, SP_MD))

        self.btn_start = Button(controls, "Start Macro", command=self.start_macro,
                                kind="primary", height=36, page_bg=CANVAS)
        self.btn_start.pack(side="left", padx=(0, SP_XS))

        self.btn_stop = Button(controls, "Stop  F6", command=self.stop_macro,
                               kind="danger", height=36, page_bg=CANVAS, state="disabled")
        self.btn_stop.pack(side="left", padx=SP_XS)

        self.btn_pause = Button(controls, "Pause  F7", command=self.toggle_pause,
                                kind="secondary", height=36, page_bg=CANVAS, state="disabled")
        self.btn_pause.pack(side="left", padx=SP_XS)

        # The run state sits with the controls that change it, big enough to read.
        self.status_pill = Badge(controls, "Idle", MUTE, page_bg=CANVAS, height=32,
                                 font=ui.mono(12, "bold"))
        self.status_pill.pack(side="right")

        self.btn_tabs = Button(controls, self._tabs_label() + CHEVRON,
                               command=self.toggle_tab_picker, width=136,
                               kind="secondary", height=36, page_bg=CANVAS)
        self.btn_tabs.pack(side="left", padx=SP_XS)

        self.btn_test = Button(controls, "Test Scan", command=self._test_scan_window,
                               kind="secondary", height=36, page_bg=CANVAS)
        self.btn_test.pack(side="left", padx=SP_XS)
        Button(controls, "Open Logs", command=self._open_log_file,
               kind="ghost", height=36, page_bg=CANVAS).pack(side="left", padx=SP_XS)

        # Metrics row
        stats = tk.Frame(page, bg=CANVAS)
        stats.pack(fill="x", padx=SP_XL, pady=SP_XS)

        def metric(label):
            card = Card(stats, radius=R_MD, padding=(SP_MD, SP_MD), page_bg=CANVAS)
            card.pack(side="left", fill="both", expand=True, padx=(0, SP_SM))
            ui.eyebrow(card.body, label).pack(fill="x")
            val = tk.Label(card.body, text="0", font=ui.F_METRIC(),
                           bg=SURFACE, fg=INK, anchor="w")
            val.pack(fill="x", pady=(SP_XS, 0))
            return val

        self.metric_accs = metric("Accounts")
        self.metric_cycles = metric("Cycles")
        self.metric_claims = metric("Claimed")
        self.metric_adds = metric("Added")

        # Focus bar
        focus_card = Card(page, radius=R_MD, padding=(SP_MD, SP_SM), page_bg=CANVAS)
        focus_card.pack(fill="x", padx=SP_XL, pady=SP_SM)
        fb = tk.Frame(focus_card.body, bg=SURFACE)   # first line; claim timers go below
        fb.pack(fill="x")
        tk.Label(fb, text="Active window", font=ui.F_BODY_STRONG(),
                 bg=SURFACE, fg=INK).pack(side="left")
        self.lbl_active_window = tk.Label(
            fb, text="No account active - run Start Macro or Test Scan",
            font=ui.F_BODY(), bg=SURFACE, fg=MUTE)
        self.lbl_active_window.pack(side="left", padx=SP_SM)
        self.badge_focus = Badge(fb, "Waiting", MUTE, page_bg=SURFACE)
        self.badge_focus.pack(side="right")
        # Each account's claim timer, so a waiting macro shows what it waits for.
        self.lbl_timers = tk.Label(focus_card.body, text="", font=ui.F_CAPTION(),
                                   bg=SURFACE, fg=BODY, anchor="w", justify="left")

        # Vision feed: the actual client, refreshed continuously, with the calibrated
        # regions and click points drawn on it (Constitution VI). It can pop out
        # into its own window; the page then drops this section and the main
        # window shrinks to the controls, stats and console.
        # Feed and console share one row, so both fit the default window.
        self.feed_row = tk.Frame(page, bg=CANVAS)
        self.feed_row.pack(fill="both", expand=True, padx=SP_XL, pady=(SP_SM, SP_LG))
        vision = Card(self.feed_row, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        vision.pack(side="left", fill="y", padx=(0, SP_SM))
        self.vision_card = vision
        vhead = tk.Frame(vision.body, bg=SURFACE)
        vhead.pack(fill="x", pady=(0, SP_SM))
        ui.heading(vhead, "Vision scanner feed", size=15).pack(side="left")
        Button(vhead, "Pop out", command=self.pop_out_feed, kind="secondary",
               height=28, page_bg=SURFACE, font=ui.F_CAPTION()).pack(side="right")
        self.docked_view = LiveFeedView(vision.body, size=self.DOCKED_FEED_SIZE)
        self.docked_view.pack(fill="x")
        self.live_view = self.docked_view
        self.feed_window = None
        self._docked_geometry = None

        # Console
        console = Card(self.feed_row, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        self.console_card = console
        console.pack(side="left", fill="both", expand=True)
        chead = tk.Frame(console.body, bg=SURFACE)
        chead.pack(fill="x", pady=(0, SP_XS))
        ui.heading(chead, "Execution console", size=15).pack(side="left")
        Button(chead, "Clear", command=self._clear_logs, kind="ghost",
               height=26, page_bg=SURFACE, font=ui.F_CAPTION()).pack(side="right")

        log_shell = Card(console.body, radius=R_SM, fill=SURFACE_2,
                         padding=(SP_SM, SP_XS), page_bg=SURFACE)
        log_shell.pack(fill="both", expand=True)
        self.log_text = tk.Text(
            log_shell.body, bg=SURFACE_2, fg=BODY, font=ui.F_CODE(),
            relief="flat", bd=0, wrap="word", height=10, width=30,
            insertbackground=INK, highlightthickness=0, selectbackground=SURFACE_3,
            state="disabled",       # read-only: only the macro writes here
        )
        self.log_text.pack(fill="both", expand=True)
        # Tab leaves the console instead of being swallowed by it.
        self.log_text.bind("<Tab>", self._focus_next)
        self.log_text.bind("<Shift-Tab>", self._focus_prev)

    def _update_slot_frame(self, slot_id, crop, state, reason):
        """Runner frame callback. Per-slot preview cards were removed at the owner's
        request; the live feed shows the whole client instead and every slot reading
        is still written to the execution console by the runner."""

    # ---------------- feed pop-out ----------------
    POPOUT_SIZE = (900, 700)
    COMPACT_HEIGHT = 600  # main window once the feed section is gone
    DOCKED_FEED_SIZE = (448, 336)
    COUNTDOWN_JOB = "macro:claim_timers"

    def pop_out_feed(self):
        """Move the live feed into its own window and shrink the main window."""
        if self.feed_window is not None:
            self.feed_window.lift()
            return
        win = tk.Toplevel(self.root, bg=SURFACE)
        win.title("Vision scanner feed")
        win.geometry(self._popout_geometry())
        win.minsize(320, 260)
        view = LiveFeedView(win, size=(self.POPOUT_SIZE[0] - 24, self.POPOUT_SIZE[1] - 60))
        view.pack(fill="both", expand=True, padx=12, pady=12)
        self.feed_window, self.live_view = win, view
        win.protocol("WM_DELETE_WINDOW", self.dock_feed)
        win.bind("<Destroy>", lambda e: self.dock_feed() if e.widget is win else None)
        win.bind("<Configure>", lambda e: self._fit_feed_to(win) if e.widget is win else None)
        self.live_feed.max_size = (self.POPOUT_SIZE[0] - 24, self.POPOUT_SIZE[1] - 60)

        self.vision_card.pack_forget()
        self.live_feed.set_visible(True)  # keeps running whatever page is shown
        self.root.update_idletasks()
        self._docked_geometry = self.root.geometry()
        self._docked_minsize = self.root.minsize()
        self.root.minsize(self._docked_minsize[0], self.COMPACT_HEIGHT)
        # Keep the main window where it is; only its height changes.
        self.root.geometry(f"{self.root.winfo_width()}x{self.COMPACT_HEIGHT}"
                           f"+{self.root.winfo_x()}+{self.root.winfo_y()}")

    def _popout_geometry(self):
        """Open the feed window beside the main window when the screen has room."""
        w, h = self.POPOUT_SIZE
        self.root.update_idletasks()
        right = self.root.winfo_rootx() + self.root.winfo_width() + SP_SM
        if right + w <= self.root.winfo_screenwidth():
            return f"{w}x{h}+{right}+{max(0, self.root.winfo_rooty())}"
        left = self.root.winfo_rootx() - w - SP_SM
        if left >= 0:
            return f"{w}x{h}+{left}+{max(0, self.root.winfo_rooty())}"
        return f"{w}x{h}"

    def dock_feed(self):
        """Close the feed window and put the feed back on the Overview page."""
        win = self.feed_window
        if win is None:
            return
        self.feed_window = None
        self.live_view = self.docked_view
        self.live_feed.max_size = self.docked_view.size
        if win.winfo_exists():
            win.destroy()
        self.vision_card.pack(side="left", fill="y", padx=(0, SP_SM), before=self.console_card)
        self.live_feed.set_visible(self.shell.view_titles[self.shell.current_view_idx]
                                   == self.FEED_PAGE)
        if getattr(self, "_docked_minsize", None):
            self.root.minsize(*self._docked_minsize)
        if self._docked_geometry:
            self.root.geometry(self._docked_geometry)
            self._docked_geometry = None

    def _fit_feed_to(self, win):
        """Scale the popped-out feed to its window (read by the capture job)."""
        w, h = win.winfo_width() - 24, win.winfo_height() - 60
        if w > 100 and h > 100:
            self.live_feed.max_size = (w, h)

    # ---------------- Slots ----------------
    def _cfg_mode(self):
        """Which macro the loaded profile belongs to ('mutation' or 'goliath')."""
        meta_mode = self.current_cfg.get("meta", {}).get("macro_mode")
        return meta_mode if meta_mode in self.MODES else self.macro_mode

    def _init_slots_view(self):
        self.slots_header = tk.Frame(self.view_slots, bg=CANVAS)
        self.slots_header.pack(fill="x", padx=SP_XL, pady=(SP_LG, SP_SM))

        body = tk.Frame(self.view_slots, bg=CANVAS)
        body.pack(fill="both", expand=True, padx=SP_XL, pady=(0, SP_LG))
        self.slots_scroll_frame = self.shell.scrollable(body)
        self._populate_slots_ui()

    def _render_slots_header(self, mutation):
        for widget in self.slots_header.winfo_children():
            widget.destroy()

        title = "Mutation clicks" if mutation else "Pet slot configuration"
        tk.Label(self.slots_header, text=title, font=ui.sans(16, "bold"),
                 bg=CANVAS, fg=INK).pack(side="left")

        if mutation:
            Button(self.slots_header, "Calibrate Mutation",
                   command=self._open_mutation_calibrator, kind="primary",
                   height=32, page_bg=CANVAS).pack(side="right")
        else:
            Button(self.slots_header, "Add Slot", command=self._add_slot_entry,
                   kind="primary", height=32,
                   page_bg=CANVAS).pack(side="right", padx=(SP_XS, 0))
            Button(self.slots_header, "Calibrate with Mouse",
                   command=self._open_calibration_tool, kind="secondary",
                   height=32, page_bg=CANVAS).pack(side="right")

    def _populate_slots_ui(self):
        mutation = self._cfg_mode() == "mutation"

        self._render_slots_header(mutation)
        for widget in self.slots_scroll_frame.winfo_children():
            widget.destroy()
        self.slot_ui_rows.clear()
        # Widgets below are rebuilt per mode; clear the stale handles so
        # _sync_ui_to_config never reads a destroyed Entry.
        self.e_ps_x = self.e_ps_y = self.e_cy_x = self.e_cy_y = None
        self.e_pc_x = self.e_pc_y = None
        self.e_mut_ps_x = self.e_mut_ps_y = None
        self.e_mut_mt_x = self.e_mut_mt_y = None
        self.e_mut_sr_x = self.e_mut_sr_y = None
        self.e_mut_sr_w = self.e_mut_sr_h = None

        if mutation:
            self._render_mutation_targets_card()
            return

        for idx, s in enumerate(self.current_cfg.get("slots", [])):
            self._render_slot_card(idx, s)
        self._render_common_actions_card()

    def _coord_group(self, parent, label, pairs, hint=None):
        """A labelled row of numeric fields. `pairs` is [(caption, value), ...].

        `hint` is a short plain-English note under the fields saying what the
        point is on screen, so nobody has to guess what a number means.
        """
        group = tk.Frame(parent, bg=SURFACE)
        group.pack(side="left", padx=(0, SP_MD))
        ui.eyebrow(group, label).pack(fill="x", pady=(0, SP_XS))
        row = tk.Frame(group, bg=SURFACE)
        row.pack(fill="x")
        fields = []
        for caption, value in pairs:
            cell = tk.Frame(row, bg=SURFACE)
            cell.pack(side="left", padx=(0, SP_XS))
            tk.Label(cell, text=caption, font=ui.F_CAPTION(),
                     bg=SURFACE, fg=MUTE).pack(anchor="w")
            f = self._field(cell, value)
            f.pack()
            fields.append(f)
        if hint:
            tk.Label(group, text=hint, font=ui.F_CAPTION(), bg=SURFACE, fg=MUTE,
                     anchor="w").pack(fill="x", pady=(SP_XS, 0))
        return fields

    def _render_slot_card(self, idx, s):
        card = Card(self.slots_scroll_frame, radius=R_LG,
                    padding=(SP_MD, SP_MD), page_bg=CANVAS)
        card.pack(fill="x", pady=(0, SP_SM))

        top = tk.Frame(card.body, bg=SURFACE)
        top.pack(fill="x", pady=(0, SP_SM))
        tk.Label(top, text=s.get("name", f"Slot {idx + 1}"), font=ui.F_BODY_STRONG(),
                 bg=SURFACE, fg=INK).pack(side="left")
        Button(top, "Delete", command=lambda i=idx: self._delete_slot_entry(i),
               kind="ghost", height=26, page_bg=SURFACE,
               font=ui.F_CAPTION()).pack(side="right")

        grid = tk.Frame(card.body, bg=SURFACE)
        grid.pack(fill="x")

        sc = s.get("slot_click", {"x": 273, "y": 288})
        cc = s.get("claim_click", {"x": 275, "y": 348})
        sr = s.get("scan_region", {"x": 196, "y": 178, "width": 142, "height": 246})

        f_sc = self._coord_group(grid, "Slot click",
                                 [("X", sc.get("x", 0)), ("Y", sc.get("y", 0))],
                                 hint="the pet slot itself")
        f_cc = self._coord_group(grid, "Claim click",
                                 [("X", cc.get("x", 0)), ("Y", cc.get("y", 0))],
                                 hint="the CLAIM button on that slot")
        f_sr = self._coord_group(grid, "Scan region", [
            ("X", sr.get("x", 0)), ("Y", sr.get("y", 0)),
            ("W", sr.get("width", 0)), ("H", sr.get("height", 0)),
        ], hint="the part of the slot to watch")

        self.slot_ui_rows.append({
            "name": s.get("name", f"Slot {idx + 1}"),
            "id": s.get("id", idx + 1),
            "e_sc_x": f_sc[0], "e_sc_y": f_sc[1],
            "e_cc_x": f_cc[0], "e_cc_y": f_cc[1],
            "e_sr_x": f_sr[0], "e_sr_y": f_sr[1],
            "e_sr_w": f_sr[2], "e_sr_h": f_sr[3],
        })

    def _render_mutation_targets_card(self):
        """The two clicks and the watch box the mutation runner actually reads.

        These bind to config["targets"] / config["scan_region"] - the keys
        MutationRunner looks up - not to the Goliath-shaped common_actions.
        """
        card = Card(self.slots_scroll_frame, radius=R_LG,
                    padding=(SP_MD, SP_MD), page_bg=CANVAS)
        card.pack(fill="x", pady=(0, SP_LG))

        tk.Label(card.body, text="Click points", font=ui.F_BODY_STRONG(),
                 bg=SURFACE, fg=INK).pack(anchor="w", pady=(0, SP_SM))

        targets = self.current_cfg.get("targets", {})
        ps = targets.get("pet_select", {"x": 208, "y": 240})
        mt = targets.get("mutate", {"x": 555, "y": 414})
        sr = self.current_cfg.get("scan_region",
                                  {"x": 176, "y": 114, "width": 426, "height": 412})

        grid = tk.Frame(card.body, bg=SURFACE)
        grid.pack(fill="x")
        f_ps = self._coord_group(grid, "Pet select",
                                 [("X", ps.get("x", 0)), ("Y", ps.get("y", 0))],
                                 hint="the pet you pick from the list")
        f_mt = self._coord_group(grid, "Mutate",
                                 [("X", mt.get("x", 0)), ("Y", mt.get("y", 0))],
                                 hint="the MUTATE button")
        f_sr = self._coord_group(grid, "Scan region", [
            ("X", sr.get("x", 0)), ("Y", sr.get("y", 0)),
            ("W", sr.get("width", 0)), ("H", sr.get("height", 0)),
        ], hint="box around the spin wheel")

        self.e_mut_ps_x, self.e_mut_ps_y = f_ps
        self.e_mut_mt_x, self.e_mut_mt_y = f_mt
        self.e_mut_sr_x, self.e_mut_sr_y, self.e_mut_sr_w, self.e_mut_sr_h = f_sr

    def _render_common_actions_card(self):
        card = Card(self.slots_scroll_frame, radius=R_LG,
                    padding=(SP_MD, SP_MD), page_bg=CANVAS)
        card.pack(fill="x", pady=(SP_XS, SP_LG))

        tk.Label(card.body, text="Pop-up buttons", font=ui.F_BODY_STRONG(),
                 bg=SURFACE, fg=INK).pack(anchor="w", pady=(0, SP_SM))

        grid = tk.Frame(card.body, bg=SURFACE)
        grid.pack(fill="x")

        comm = self.current_cfg.get("common_actions", {})
        ps = comm.get("pet_select", {"x": 237, "y": 234})
        cy = comm.get("confirm_yes", {"x": 326, "y": 338})
        # 0,0 means "not calibrated" - the macro clicks nothing.
        pc = comm.get("popup_close", {"x": 0, "y": 0})

        f_ps = self._coord_group(grid, "Pet select",
                                 [("X", ps.get("x", 237)), ("Y", ps.get("y", 234))],
                                 hint="the pet in the list")
        f_cy = self._coord_group(grid, "Confirm yes",
                                 [("X", cy.get("x", 326)), ("Y", cy.get("y", 338))],
                                 hint="the Yes button")
        pc_set = bool(pc.get("x") or pc.get("y"))
        f_pc = self._coord_group(grid, "Pop-up close",
                                 [("X", pc.get("x", "") if pc_set else ""),
                                  ("Y", pc.get("y", "") if pc_set else "")],
                                 hint="X on a stray pop-up - blank = not set")
        self.e_ps_x, self.e_ps_y = f_ps
        self.e_cy_x, self.e_cy_y = f_cy
        self.e_pc_x, self.e_pc_y = f_pc

        tk.Label(card.body,
                 text="Pop-up close is only for closing an unexpected pop-up. "
                      "Never put it on a Buy or Yes button - leave it blank if "
                      "you are not sure.",
                 font=ui.F_CAPTION(), bg=SURFACE, fg=WARNING, anchor="w",
                 justify="left", wraplength=760).pack(fill="x", pady=(SP_SM, 0))

    def _add_slot_entry(self):
        # Keep what was typed into the other cards; they are rebuilt below.
        if not self._sync_fields(save=False):
            return
        slots = self.current_cfg.setdefault("slots", [])
        # Ids key the per-slot grow timers, so a new slot never reuses one.
        new_id = max((int(s.get("id", 0)) for s in slots), default=0) + 1
        slots.append({
            "id": new_id,
            "name": f"Slot {new_id}",
            "slot_click": {"x": 273, "y": 288},
            "claim_click": {"x": 275, "y": 348},
            "scan_region": {"x": 196, "y": 178, "width": 142, "height": 246},
        })
        self._populate_slots_ui()
        self._mark_dirty()

    def _delete_slot_entry(self, index):
        if not self._sync_fields(save=False):
            return
        slots = self.current_cfg.get("slots", [])
        if 0 <= index < len(slots):
            removed = slots.pop(index)
            self._populate_slots_ui()
            self._mark_dirty()
            self._append_log(f"[SLOTS] Removed {removed.get('name', 'a slot')}. "
                             f"Save Profile to keep this change.")

    # ---------------- Settings ----------------
    def _setting_row(self, parent, label, description, control_builder):
        """A settings row: label + description on the left, control on the right."""
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", pady=SP_XS)
        text = tk.Frame(row, bg=SURFACE)
        text.pack(side="left", fill="x", expand=True)
        tk.Label(text, text=label, font=ui.F_BODY(), bg=SURFACE, fg=INK,
                 anchor="w").pack(fill="x")
        if description:
            tk.Label(text, text=description, font=ui.F_CAPTION(), bg=SURFACE,
                     fg=MUTE, anchor="w").pack(fill="x")
        control = control_builder(row)
        control.pack(side="right", padx=(SP_MD, 0))
        return control

    def _init_settings_view(self):
        page = self.shell.scrollable(self.view_settings)
        w_cfg = self.current_cfg.get("window_settings", {})
        delays = self.current_cfg.get("delays", {})

        # Window card
        card1 = Card(page, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        card1.pack(fill="x", padx=SP_XL, pady=(SP_LG, SP_SM))
        ui.heading(card1.body, "Window & resolution", size=15).pack(anchor="w")
        ui.caption(card1.body, "The client size the vision scanner expects.").pack(
            anchor="w", pady=(2, SP_SM))

        dims = tk.Frame(card1.body, bg=SURFACE)
        dims.pack(fill="x", pady=(0, SP_SM))
        f_dims = self._coord_group(dims, "Standard client size", [
            ("Width", w_cfg.get("standard_width", 800)),
            ("Height", w_cfg.get("standard_height", 600)),
        ])
        self.e_win_w, self.e_win_h = f_dims

        tk.Frame(card1.body, bg=HAIRLINE, height=1).pack(fill="x", pady=SP_XS)

        self.var_arrange = tk.BooleanVar(value=w_cfg.get("auto_arrange_windows", True))
        self._setting_row(card1.body, "Auto-arrange game windows",
                          "Cascade detected clients diagonally",
                          lambda p: Toggle(p, self.var_arrange, page_bg=SURFACE, command=self._mark_dirty))

        self.e_diag_gap = None
        def _build_gap_field(p):
            f = self._field(p, w_cfg.get("diagonal_gap", 10))
            self.e_diag_gap = f
            return f

        self._setting_row(card1.body, "Diagonal tab gap (px)",
                          "Offset between stacked client windows (5-10px recommended)",
                          _build_gap_field)

        self.var_shrink = tk.BooleanVar(value=w_cfg.get("auto_shrink_to_minimum", True))
        self._setting_row(card1.body, "Force smallest window size",
                          "Snap each client to an 800x600 client area",
                          lambda p: Toggle(p, self.var_shrink, page_bg=SURFACE, command=self._mark_dirty))

        self.var_hud = tk.BooleanVar(
            value=self.current_cfg.get("ui", {}).get("show_floating_stop_button", True))
        self._setting_row(card1.body, "Floating stop button",
                          "Show the on-screen killswitch overlay while running",
                          lambda p: Toggle(p, self.var_hud, page_bg=SURFACE, command=self._mark_dirty))

        tk.Frame(card1.body, bg=HAIRLINE, height=1).pack(fill="x", pady=SP_XS)
        self._setting_row(card1.body, "Snap windows now",
                          "Apply the smallest size to every detected client",
                          lambda p: Button(p, "Snap Now", command=self._snap_roblox_minimum,
                                           kind="secondary", height=30, page_bg=SURFACE))

        # Timing card
        card2 = Card(page, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        card2.pack(fill="x", padx=SP_XL, pady=(SP_SM, SP_LG))
        ui.heading(card2.body, "Speed & Delays", size=15).pack(anchor="w")
        ui.caption(card2.body, "How long the macro waits between actions.").pack(
            anchor="w", pady=(2, SP_SM))

        timing = tk.Frame(card2.body, bg=SURFACE)
        timing.pack(fill="x")
        f_delays = self._coord_group(timing, "Seconds", [
            ("Tab switch", delays.get("window_focus_delay", 0.6)),
            ("Click delay", delays.get("between_slots_delay", delays.get("after_pet_select_delay", 0.35))),
            ("Round delay", delays.get("cycle_delay", delays.get("mutation_poll_delay", 2.0))),
        ])
        self.e_d_focus, self.e_d_slot, self.e_d_cycle = f_delays

        self._build_popup_card(page)

        # Claim scheduler card (Goliath only - mutation has no claim step)
        sched = self.current_cfg.get("claim_schedule", {})
        self.card_claim = Card(page, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        card3 = self.card_claim
        ui.heading(card3.body, "Claim timer", size=15).pack(anchor="w")
        ui.caption(card3.body, "Goliath profiles only.").pack(anchor="w", pady=(2, SP_SM))

        self.var_sched = tk.BooleanVar(value=sched.get("enabled", True))
        self._setting_row(card3.body, "Wait before checking an account again",
                          "Skips an account until its pets have had time to finish",
                          lambda p: Toggle(p, self.var_sched, page_bg=SURFACE, command=self._mark_dirty))

        self.e_sched_wait = None

        def _build_wait_field(p):
            f = self._field(p, sched.get("wait_minutes", 10))
            self.e_sched_wait = f
            return f

        self._setting_row(card3.body, "Wait time (minutes)",
                          "Starts counting when a pet goes into a slot",
                          _build_wait_field)

        tk.Frame(card3.body, bg=HAIRLINE, height=1).pack(fill="x", pady=SP_XS)

        fill = self.current_cfg.get("slot_fill", {})
        self.var_verify_fill = tk.BooleanVar(value=fill.get("verify_all_slots", True))
        self._setting_row(card3.body, "Check the account before moving on",
                          "Re-reads all slots and finishes anything left to claim or fill",
                          lambda p: Toggle(p, self.var_verify_fill, page_bg=SURFACE, command=self._mark_dirty))

        self._sync_mode_visibility()

    def _sync_mode_visibility(self):
        """Show mode-specific settings only for the mode they belong to.

        The claim timer is a Goliath idea; a mutation profile should not even
        see the control, let alone be affected by it.
        """
        if self._cfg_mode() == "goliath":
            self.card_claim.pack(fill="x", padx=SP_XL, pady=(0, SP_LG))
        else:
            self.card_claim.pack_forget()

    # -----------------------------------------------------------
    # Config sync
    # -----------------------------------------------------------
    def _sync_goliath_slots(self):
        """Write the Goliath slot cards + shared pop-up buttons back to config."""
        new_slots = []
        for row in self.slot_ui_rows:
            name = row["name"]
            new_slots.append({
                "id": row["id"],
                "name": name,
                "slot_click": {"x": read_number(row["e_sc_x"], f"{name} slot click X", minimum=0),
                               "y": read_number(row["e_sc_y"], f"{name} slot click Y", minimum=0)},
                "claim_click": {"x": read_number(row["e_cc_x"], f"{name} claim click X", minimum=0),
                                "y": read_number(row["e_cc_y"], f"{name} claim click Y", minimum=0)},
                "scan_region": {
                    "x": read_number(row["e_sr_x"], f"{name} scan region X", minimum=0),
                    "y": read_number(row["e_sr_y"], f"{name} scan region Y", minimum=0),
                    "width": read_number(row["e_sr_w"], f"{name} scan region width", minimum=1),
                    "height": read_number(row["e_sr_h"], f"{name} scan region height", minimum=1),
                },
            })
        comm = {}
        if self.e_ps_x is not None:
            comm["pet_select"] = {"x": read_number(self.e_ps_x, "Pet select X", minimum=0),
                                  "y": read_number(self.e_ps_y, "Pet select Y", minimum=0)}
        if self.e_cy_x is not None:
            comm["confirm_yes"] = {"x": read_number(self.e_cy_x, "Confirm yes X", minimum=0),
                                   "y": read_number(self.e_cy_y, "Confirm yes Y", minimum=0)}
        popup = None
        if self.e_pc_x is not None:
            popup = (read_number(self.e_pc_x, "Pop-up close X", minimum=0, blank=0),
                     read_number(self.e_pc_y, "Pop-up close Y", minimum=0, blank=0))

        # Every value read: only now change the config, so a bad field changes nothing.
        self.current_cfg["slots"] = new_slots
        common = self.current_cfg.setdefault("common_actions", {})
        common.update(comm)
        if popup is not None:
            if popup[0] or popup[1]:
                common["popup_close"] = {"x": popup[0], "y": popup[1]}
            else:
                # Blank (or 0,0) is "not set". Removing the key is what keeps the
                # runner from ever clicking the client corner as a close button.
                common.pop("popup_close", None)

    def _sync_mutation_targets(self):
        """Write the mutation card back to the keys MutationRunner reads.

        MutationRunner looks up config["targets"]["pet_select"/"mutate"] and
        config["scan_region"], and nothing else. No Goliath-shaped mirror is
        written: a mutation profile must not carry a slot_click or a
        confirm_yes that some other code path could pick up and click.
        """
        if self.e_mut_ps_x is None:
            return
        pet = {"x": read_number(self.e_mut_ps_x, "Pet select X", minimum=0),
               "y": read_number(self.e_mut_ps_y, "Pet select Y", minimum=0)}
        mutate = {"x": read_number(self.e_mut_mt_x, "Mutate X", minimum=0),
                  "y": read_number(self.e_mut_mt_y, "Mutate Y", minimum=0)}
        region = {
            "x": read_number(self.e_mut_sr_x, "Scan region X", minimum=0),
            "y": read_number(self.e_mut_sr_y, "Scan region Y", minimum=0),
            "width": read_number(self.e_mut_sr_w, "Scan region width", minimum=1),
            "height": read_number(self.e_mut_sr_h, "Scan region height", minimum=1),
        }

        targets = self.current_cfg.setdefault("targets", {})
        targets["pet_select"] = pet
        targets["mutate"] = mutate
        self.current_cfg["scan_region"] = region

    def _read_settings(self):
        """Every Settings value, checked. Raises FieldError on the first bad one."""
        out = {
            "width": read_number(self.e_win_w, "Client width", minimum=100),
            "height": read_number(self.e_win_h, "Client height", minimum=100),
            "gap": (read_number(self.e_diag_gap, "Diagonal tab gap", minimum=0)
                    if self.e_diag_gap is not None else None),
            "focus": read_number(self.e_d_focus, "Tab switch delay", float, minimum=0),
            "action": read_number(self.e_d_slot, "Click delay", float, minimum=0),
            # A round delay of 0 would make the macro hammer every client nonstop.
            "cycle": read_number(self.e_d_cycle, "Round delay", float, minimum=0.5),
            "wait": None,
        }
        if self._cfg_mode() == "goliath" and self.e_sched_wait is not None:
            out["wait"] = read_number(self.e_sched_wait, "Claim wait time", float, minimum=1)
        return out

    def _sync_fields(self, save=True):
        """Check every field and copy it into the config; optionally save the profile.

        Returns False, after pointing at the bad field, when a value can't be used.
        """
        try:
            if self._cfg_mode() == "mutation":
                self._sync_mutation_targets()
            else:
                self._sync_goliath_slots()
            vals = self._read_settings()
        except FieldError as e:
            self._show_field_error(e)
            return False

        w_cfg = self.current_cfg.setdefault("window_settings", {})
        w_cfg["standard_width"] = vals["width"]
        w_cfg["standard_height"] = vals["height"]
        w_cfg["auto_arrange_windows"] = bool(self.var_arrange.get())
        w_cfg["auto_shrink_to_minimum"] = bool(self.var_shrink.get())
        if vals["gap"] is not None:
            w_cfg["diagonal_gap"] = vals["gap"]
            w_cfg["diagonal_offset_x"] = vals["gap"]
            w_cfg["diagonal_offset_y"] = vals["gap"]
        w_cfg.setdefault("layout_style", "diagonal")

        d_cfg = self.current_cfg.setdefault("delays", {})
        d_cfg["window_focus_delay"] = vals["focus"]
        d_cfg["between_slots_delay"] = vals["action"]
        d_cfg["after_pet_select_delay"] = vals["action"]
        d_cfg["click_delay"] = min(0.35, max(0.05, vals["action"]))
        d_cfg["cycle_delay"] = vals["cycle"]
        d_cfg["mutation_poll_delay"] = vals["cycle"]

        # Claim timers are a Goliath concept; never write them into a
        # mutation profile (save_profile would strip them anyway).
        if self._cfg_mode() == "goliath":
            s_cfg = self.current_cfg.setdefault("claim_schedule", {})
            s_cfg["enabled"] = bool(self.var_sched.get())
            if vals["wait"] is not None:
                s_cfg["wait_minutes"] = vals["wait"]

            f_cfg = self.current_cfg.setdefault("slot_fill", {})
            f_cfg["verify_all_slots"] = bool(self.var_verify_fill.get())
            f_cfg.setdefault("retries", 2)

        self.current_cfg.setdefault("ui", {})["show_floating_stop_button"] = bool(self.var_hud.get())
        if save:
            return self._write_profile()
        return True

    def _sync_ui_to_config(self):
        """Read every field into self.current_cfg and save the profile."""
        return self._sync_fields(save=True)

    def _write_profile(self, name=None):
        """Save the config to its profile file; report a failure instead of hiding it."""
        name = name or self.active_profile_name
        try:
            self.pm.save_profile(name, self.current_cfg)
        except OSError as e:
            self._append_log(f"[PROFILE] Could not save '{name}': {e}")
            messagebox.showerror("Profile not saved",
                                 f"Could not save the profile '{name}'.\n\n{e}")
            return False
        if name == self.active_profile_name:
            self._mark_clean()
        return True

    def _show_field_error(self, error):
        """Show which value is wrong: open its page, outline it, focus it, say why."""
        field = error.field
        field.set_error(str(error))
        for idx, view in enumerate(self.shell.views):
            if str(field).startswith(str(view) + "."):
                if idx != self.shell.current_view_idx:
                    self.shell._switch_view(idx)
                break
        self.root.update_idletasks()
        field.entry.focus_set()
        field.entry.select_range(0, tk.END)
        self._append_log(f"[SETTINGS] {error}")
        messagebox.showerror("Check this value", str(error))

    # ---------------- unsaved changes ----------------
    def _field(self, parent, value):
        f = Field(parent, width=5, value=value, page_bg=SURFACE)
        f.entry.bind("<Key>", self._on_field_key, add="+")
        return f

    def _on_field_key(self, event):
        # Navigation keys do not change the value.
        if event.keysym not in ("Tab", "ISO_Left_Tab", "Shift_L", "Shift_R", "Control_L",
                                "Control_R", "Alt_L", "Alt_R", "Left", "Right", "Home",
                                "End", "Up", "Down", "Escape"):
            self._mark_dirty()

    def _mark_dirty(self):
        if self.dirty:
            return
        self.dirty = True
        self.btn_save.set_kind("primary")
        self.btn_save.set_text("Save changes")
        self.lbl_unsaved.config(text="Unsaved changes")

    def _mark_clean(self):
        self.dirty = False
        self.btn_save.set_kind("secondary")
        self.btn_save.set_text("Save Profile")
        self.lbl_unsaved.config(text="")

    def _resolve_unsaved(self, action):
        """Before `action` replaces the loaded profile: save, discard or cancel.

        True when it is fine to go ahead.
        """
        if not self.dirty:
            return True
        answer = messagebox.askyesnocancel(
            "Unsaved changes",
            f"Save your changes to '{self.active_profile_name}' before {action}?")
        if answer is None:
            return False
        if answer:
            return self._sync_ui_to_config()
        self._mark_clean()
        return True

    # -----------------------------------------------------------
    # Macro mode
    # -----------------------------------------------------------
    MODES = {
        "goliath": "Goliath",
        "mutation": "Mutation",
    }

    def _mode_label(self):
        return f"Mode: {self.MODES[self.macro_mode]}"

    def _mode_caption(self):
        return f"Runs the {self.MODES[self.macro_mode]} macro"

    def _refresh_profile_controls(self):
        self.btn_profile.set_text(self._profile_label() + CHEVRON)
        self.lbl_mode.config(text=self._mode_caption())

    def _set_mode(self, mode):
        if mode == self.macro_mode or mode not in self.MODES:
            return
        if self.is_running:
            messagebox.showwarning("Macro Running",
                                   "Stop the macro before switching modes.")
            return
        target_prof = "Mutation" if mode == "mutation" else "Goliath"
        if (target_prof in self.profiles and self.active_profile_name != target_prof
                and not self._resolve_unsaved("switching profiles")):
            return
        self.macro_mode = mode
        self._refresh_profile_controls()
        name = self.MODES[mode]
        self._append_log(f"Mode switched to {name}")

        if target_prof in self.profiles and self.active_profile_name != target_prof:
            self.active_profile_name = target_prof
            self._on_profile_switched()
        else:
            # Same profile, different mode: the coordinate page still has to
            # swap between the Goliath slot cards and the mutation targets.
            self._populate_slots_ui()

        if mode == "mutation" and not os.path.isfile(
                scaling.get_resource_path("config_mutation.json")):
            self._append_log("Warning: config_mutation.json not found.")

    def _start_calibrator(self, kind, on_close):
        """Run a calibrator in its own process; call on_close when it exits.

        The calibrators write config.json / config_mutation.json, but the runner
        is fed the profile. Pulling the fresh points in when the window closes
        keeps one set of coordinates in play.
        """
        import subprocess
        try:
            proc = subprocess.Popen(calibrator_command(kind))
        except OSError as e:
            self._append_log(f"[CALIBRATE] Could not start the calibrator: {e}")
            messagebox.showerror("Calibrator", f"Could not start the calibrator:\n{e}")
            return
        threading.Thread(target=self._await_calibration, args=(proc, on_close),
                         daemon=True).start()

    def _open_mutation_calibrator(self):
        self._start_calibrator("mutation", self._import_mutation_calibration)

    def _await_calibration(self, proc, on_close):
        try:
            proc.wait()
        except Exception:
            return
        self.root.after(0, on_close)

    def _import_mutation_calibration(self):
        """Copy config_mutation.json's points into the active mutation profile."""
        if self._cfg_mode() != "mutation":
            return
        path = scaling.get_resource_path("config_mutation.json")
        if not os.path.isfile(path):
            path = os.path.join(scaling.get_base_dir(), "config_mutation.json")
        try:
            import json
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except Exception as e:
            self._append_log(f"[CALIBRATE] Could not read the calibration file: {e}")
            return

        targets = data.get("targets")
        region = data.get("scan_region")
        if not targets or not region:
            self._append_log("[CALIBRATE] Calibration file had no click points in it.")
            return

        self.current_cfg["targets"] = targets
        self.current_cfg["scan_region"] = region
        self.pm.save_profile(self.active_profile_name, self.current_cfg)
        self._populate_slots_ui()
        self._append_log(f"[CALIBRATE] Saved new points into '{self.active_profile_name}': "
                         f"pet={targets.get('pet_select')}, mutate={targets.get('mutate')}")

    # -----------------------------------------------------------
    # Profiles
    # -----------------------------------------------------------
    def _profile_label(self):
        name = self.active_profile_name
        return name if len(name) <= 24 else name[:22] + "..."

    def _open_profile_menu(self):
        menu = tk.Menu(self.root, tearoff=0, bg=SURFACE_2, fg=INK,
                       activebackground=SURFACE_3, activeforeground=INK,
                       bd=0, relief="flat", font=ui.F_BODY())
        for name in self.profiles:
            mark = "\u2713 " if name == self.active_profile_name else "    "
            menu.add_command(label=f"{mark}{name}  ({self._profile_mode(name)})",
                             command=lambda n=name: self._select_profile(n))
        x = self.btn_profile.winfo_rootx()
        y = self.btn_profile.winfo_rooty() + self.btn_profile.winfo_height()
        menu.tk_popup(x, y)

    def _profile_mode(self, name):
        """'Goliath' or 'Mutation' for the menu, from the profile's own metadata."""
        try:
            data = self.pm.load_profile(name)
        except (OSError, ValueError):     # missing or unreadable profile file
            return "?"
        mode = data.get("meta", {}).get("macro_mode")
        if mode not in self.MODES:
            mode = "mutation" if data.get("targets") and not data.get("slots") else "goliath"
        return self.MODES[mode]

    def _select_profile(self, name):
        if name == self.active_profile_name:
            return
        if self.is_running:
            messagebox.showwarning("Macro Running", "Stop the macro before switching profiles.")
            return
        if not self._resolve_unsaved("switching profiles"):
            return
        self.active_profile_name = name

        # Load first, then take the mode from the profile's own metadata.
        # Guessing it from the file name meant a profile called "Mutation_v2"
        # holding Goliath data would arm the wrong runner.
        self._on_profile_switched()
        self.macro_mode = self._cfg_mode()
        self._refresh_profile_controls()

    def _save_active_profile(self):
        # Confirmation is the button itself: no dialog to dismiss on every save.
        if self._sync_ui_to_config():
            self._append_log(f"[PROFILE] Saved '{self.active_profile_name}'.")
            self.btn_save.set_text("Saved")
            self.root.after(1500, lambda: (not self.dirty) and self.btn_save.set_text(
                "Save Profile"))

    def _snap_roblox_minimum(self):
        from macro_studio.core.window_manager import WindowManager
        wm = WindowManager()
        windows = wm.find_windows()
        if not windows:
            messagebox.showwarning("Window Manager",
                                   "No active Roblox client windows found!\nPlease start Roblox first.")
            return
        w_cfg = self.current_cfg.get("window_settings", {})
        try:
            gap = (read_number(self.e_diag_gap, "Diagonal tab gap", minimum=0)
                   if self.e_diag_gap is not None else w_cfg.get("diagonal_gap", 10))
        except FieldError as e:
            self._show_field_error(e)
            return
        style = w_cfg.get("layout_style", "diagonal")
        cols = int(w_cfg.get("grid_columns", 2))

        wm.arranged_hwnds.clear()
        wm.auto_arrange(windows, style=style, offset_x=gap, offset_y=gap, cols=cols)
        self._append_log(f"[WINDOW] Arranged {len(windows)} Roblox window(s) ({style} layout, {gap}px gap).")

    def _on_profile_switched(self, event=None):
        self.current_cfg = self.pm.load_profile(self.active_profile_name)
        self.macro_mode = self._cfg_mode()
        self._refresh_profile_controls()
        self._mark_clean()
        self._populate_slots_ui()
        self._refresh_popup_status()

        w_cfg = self.current_cfg.get("window_settings", {})
        self.e_win_w.set(w_cfg.get("standard_width", 800))
        self.e_win_h.set(w_cfg.get("standard_height", 600))
        self.var_arrange.set(w_cfg.get("auto_arrange_windows", True))
        self.var_shrink.set(w_cfg.get("auto_shrink_to_minimum", True))
        if self.e_diag_gap is not None:
            self.e_diag_gap.set(w_cfg.get("diagonal_gap", 10))

        d_cfg = self.current_cfg.get("delays", {})
        self.e_d_focus.set(d_cfg.get("window_focus_delay", 0.6))
        self.e_d_slot.set(d_cfg.get("between_slots_delay", d_cfg.get("after_pet_select_delay", 0.35)))
        self.e_d_cycle.set(d_cfg.get("cycle_delay", d_cfg.get("mutation_poll_delay", 2.0)))

        s_cfg = self.current_cfg.get("claim_schedule", {})
        self.var_sched.set(s_cfg.get("enabled", True))
        if self.e_sched_wait is not None:
            self.e_sched_wait.set(s_cfg.get("wait_minutes", 10))
        self.var_verify_fill.set(
            self.current_cfg.get("slot_fill", {}).get("verify_all_slots", True))
        self._sync_mode_visibility()

        self._append_log(f"[PROFILE] Switched active profile to: '{self.active_profile_name}'")

    def _ask_profile_name(self, title, prompt):
        """Ask until the name can be used or the user cancels (None)."""
        initial = ""
        while True:
            raw = simpledialog.askstring(title, prompt, initialvalue=initial, parent=self.root)
            if raw is None:
                return None
            name, problem = clean_profile_name(raw, self.profiles)
            if name:
                return name
            messagebox.showerror(title, problem)
            initial = raw

    def _create_new_profile(self):
        if not self._resolve_unsaved("creating a new profile"):
            return
        name = self._ask_profile_name("New Profile", "Name for the new profile:")
        if name:
            safe_name = name
            # Build the shape for the mode currently selected, and stamp the
            # mode into the file - a half-Goliath "mutation" profile is exactly
            # the mismatch that made the wrong coordinates get clicked.
            if self.macro_mode == "mutation":
                data = self.pm._get_default_mutation_data()
            else:
                data = self.pm._get_default_3slot_data()
                data["meta"]["macro_mode"] = "goliath"
            data["meta"]["name"] = name
            try:
                self.pm.save_profile(safe_name, data)
            except OSError as e:
                messagebox.showerror("New Profile", f"Could not create '{safe_name}'.\n\n{e}")
                return
            self.profiles = self.pm.list_profiles()
            self.active_profile_name = safe_name
            self._on_profile_switched()

    def _duplicate_profile(self):
        name = self._ask_profile_name("Duplicate Profile",
                                      f"Name for the copy of '{self.active_profile_name}':")
        if name:
            # The copy includes what is on screen; the original keeps its saved state.
            if not self._sync_fields(save=False):
                return
            if not self._write_profile(name):
                return
            self.profiles = self.pm.list_profiles()
            self.active_profile_name = name
            self._on_profile_switched()

    def _delete_profile(self):
        if len(self.profiles) <= 1:
            messagebox.showwarning("Cannot Delete", "You must keep at least one profile.")
            return
        if messagebox.askyesno("Delete Profile",
                               f"Are you sure you want to delete '{self.active_profile_name}'?"):
            self.pm.delete_profile(self.active_profile_name)
            self.profiles = self.pm.list_profiles()
            self.active_profile_name = self.profiles[0]
            self._on_profile_switched()

    # -----------------------------------------------------------
    # Tools
    # -----------------------------------------------------------
    def _open_calibration_tool(self):
        if self._cfg_mode() != "goliath":
            messagebox.showinfo("Calibrator", "Switch to a Goliath profile first - "
                                "this calibrator records the 3-slot daycare points.")
            return
        self._seed_goliath_calibration()
        self._start_calibrator("goliath", self._import_goliath_calibration)

    def _goliath_calibration_path(self):
        return os.path.join(scaling.get_base_dir(), "config.json")

    def _seed_goliath_calibration(self):
        """Start the calibrator from the points the macro actually uses.

        It saves every target, so any point it loads stale from config.json
        would overwrite the profile's value on import.
        """
        path = self._goliath_calibration_path()
        data = {}
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
            except (OSError, ValueError) as e:
                self._append_log(f"[CALIBRATE] config.json unreadable, rewriting it: {e}")
                data = {}
        data["slots"] = copy.deepcopy(self.current_cfg.get("slots", []))
        data["common_actions"] = copy.deepcopy(self.current_cfg.get("common_actions", {}))
        write_json_atomic(path, data)

    def _import_goliath_calibration(self):
        """Copy the calibrator's click points into the active Goliath profile."""
        if self._cfg_mode() != "goliath":
            return
        try:
            with open(self._goliath_calibration_path(), "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            self._append_log(f"[CALIBRATE] Could not read the calibration file: {e}")
            return

        slots = self.current_cfg.setdefault("slots", [])
        for idx, new in enumerate(data.get("slots", [])):
            if idx >= len(slots):
                break
            # Click points only: the profile's scan regions stay as they are.
            for key in ("slot_click", "claim_click"):
                if isinstance(new.get(key), dict):
                    slots[idx][key] = dict(new[key])
        self.current_cfg.setdefault("common_actions", {}).update(
            copy.deepcopy(data.get("common_actions", {})))

        self.pm.save_profile(self.active_profile_name, self.current_cfg)
        self._populate_slots_ui()
        self._append_log(f"[CALIBRATE] Loaded new points into '{self.active_profile_name}'.")

    def _open_log_file(self):
        log_file = os.path.join(scaling.get_base_dir(), "debug_log.txt")
        if os.path.exists(log_file):
            os.startfile(log_file)  # no shell command built from a string
        else:
            messagebox.showinfo("Log Notice", "debug_log.txt has not been created yet.")

    def _test_scan_window(self):
        # The test scan analyses Goliath daycare slots (CLAIM / EMPTY / BUSY).
        # Running it against a mutation profile would report nonsense.
        if self._cfg_mode() == "mutation":
            self._append_log("[TEST] Test Scan only works on Goliath profiles.")
            messagebox.showinfo("Test Scan",
                                "Test Scan looks at the Goliath daycare slots.\n\n"
                                "Switch to a Goliath profile to use it.")
            return
        if self._scan_running or self.is_running:
            return
        if self.tab_selection.pids == frozenset():
            messagebox.showwarning("Test Scan", "No tabs are selected. Open Tabs and "
                                                "pick the Roblox client to scan.")
            return
        self._scan_running = True
        self.btn_test.set_state("disabled")
        self.btn_test.set_text("Scanning...")
        cfg = copy.deepcopy(self.current_cfg)
        # Focusing the client and capturing it takes about half a second: never on
        # the Tk thread, or the whole window freezes while it runs.
        threading.Thread(target=self._run_test_scan, args=(cfg,), daemon=True).start()

    def _run_test_scan(self, cfg):
        from macro_studio.core.window_manager import WindowManager
        from macro_studio.core.vision_scanner import VisionScanner
        ui_call = self.ctx.ui_call
        try:
            self._append_log("=== RUNNING INSTANT TEST SCAN ===")
            w_cfg = cfg.get("window_settings", {})
            wm = WindowManager(
                target_procs=w_cfg.get("target_process_names",
                                       ["robloxplayerbeta.exe", "roblox.exe",
                                        "windows10universal.exe"]),
                ignore_procs=w_cfg.get("ignore_process_names",
                                       ["roblox account manager.exe", "chrome.exe",
                                        "msedge.exe", "brave.exe", "firefox.exe"]),
                width=w_cfg.get("standard_width", 800),
                height=w_cfg.get("standard_height", 600),
            )
            windows = self.tab_selection.filter(wm.find_windows(), wm.get_pid)
            if not windows:
                self._append_log("[TEST] No selected Roblox client is open.")
                ui_call(lambda: messagebox.showwarning(
                    "Test Scan", "No Roblox client to scan.\n\nStart the game, make sure "
                                 "its window is not minimized, and check it is ticked in Tabs."))
                return

            target_hwnd = windows[0]
            title = wm.get_window_title(target_hwnd)
            pid = wm.get_pid(target_hwnd)
            self._append_log(f"[TEST] Scanning PID {pid} ('{title}'), window {target_hwnd}")
            ui_call(lambda: (self.lbl_active_window.config(
                text=f"Test scan - PID {pid} ('{title}')", fg=INK),
                self.badge_focus.set("Scanning", CYAN)))

            wm.force_activate_window(target_hwnd)
            time.sleep(0.4)
            vs = VisionScanner(cfg)
            full_img = vs.capture_client_full(target_hwnd)
            if full_img is None:
                self._append_log("[TEST] Could not capture the game client. "
                                 "Is the window minimized?")
                return
            for slot in cfg.get("slots", []):
                state, reason, _crop = vs.analyze_slot_advanced(full_img, slot)
                self._append_log(f"   - {slot.get('name')}: State={state} ({reason})")
            self._append_log("=== TEST SCAN FINISHED ===")
        except Exception as e:  # noqa: BLE001 - top of a worker thread: report, re-enable UI
            self._append_log(f"[TEST] Test scan failed: {e}")
        finally:
            ui_call(self._on_test_scan_done)

    def _on_test_scan_done(self):
        self._scan_running = False
        self.btn_test.set_text("Test Scan")
        self.btn_test.set_state("normal")
        if not self.is_running:
            self.badge_focus.set("Waiting", MUTE)

    def _append_log(self, text):
        def _insert():
            self.log_text.configure(state="normal")
            self.log_text.insert(tk.END, time.strftime("[%H:%M:%S] ") + text + "\n")
            lines = int(self.log_text.index("end-1c").split(".")[0])
            if lines > self.LOG_MAX_LINES:
                self.log_text.delete("1.0", f"{lines - self.LOG_MAX_LINES + 1}.0")
            self.log_text.configure(state="disabled")
            self.log_text.see(tk.END)
        self.ctx.ui_call(_insert)

    def _clear_logs(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

    @staticmethod
    def _focus_next(event):
        nxt = event.widget.tk_focusNext()
        if nxt is not None:
            nxt.focus_set()
        return "break"

    @staticmethod
    def _focus_prev(event):
        prev = event.widget.tk_focusPrev()
        if prev is not None:
            prev.focus_set()
        return "break"

    # ---------------- claim timers ----------------
    def _claim_timer_lines(self):
        """Scheduler thread: 'Account #1 (PID 21992): claim in 4m 12s' per open client."""
        if self._cfg_mode() != "goliath":
            return []
        runner = self.runner
        sched = getattr(runner, "scheduler", None) if runner is not None else None
        if sched is None:
            from macro_studio.runners.claim_scheduler import ClaimScheduler
            sched = ClaimScheduler.from_config(self.current_cfg)
        if not getattr(sched, "enabled", True):
            return []
        try:
            pids = [self._feed_wm.get_pid(h) for h in self._feed_wm.find_windows()]
        except OSError:              # window listing failed this second; try again next
            return []
        records = dict(getattr(sched, "records", {}) or {})
        lines = []
        for pid in pids:
            if not self.tab_selection.includes(pid):
                continue
            rec = records.get(str(pid)) or {}
            label = rec.get("label") or "Account"
            left = round(sched.seconds_remaining(pid))
            when = "due now" if left <= 0 else f"claim in {left // 60}m {left % 60:02d}s"
            lines.append(f"{label} (PID {pid}): {when}")
        return lines

    def _countdown_tick(self):
        lines = self._claim_timer_lines()
        self.ctx.ui_call(lambda: self._show_timers(lines))

    def _show_timers(self, lines):
        if not self.lbl_timers.winfo_exists():
            return
        if lines:
            self.lbl_timers.config(text="   ".join(lines[:4]) + (
                f"   +{len(lines) - 4} more" if len(lines) > 4 else ""))
            if not self.lbl_timers.winfo_ismapped():
                self.lbl_timers.pack(fill="x", pady=(SP_XS, 0))
        elif self.lbl_timers.winfo_ismapped():
            self.lbl_timers.pack_forget()

    def _update_stats(self, stats):
        def _apply():
            self.metric_accs.config(text=str(stats.get("accounts", 0)))
            self.metric_cycles.config(text=str(stats.get("cycles", 0)))
            self.metric_claims.config(text=str(stats.get("claims", 0)))
            self.metric_adds.config(text=str(stats.get("adds", 0)))

            hwnd = stats.get("active_hwnd", 0)
            acc = stats.get("active_account", "")
            title = stats.get("active_title", "")
            if hwnd:
                self.lbl_active_window.config(text=f"{acc} - {title or 'Roblox'}", fg=INK)
                self.badge_focus.set("Working", CYAN)
            else:
                self.lbl_active_window.config(
                    text="Waiting for the next account to be due", fg=MUTE)
                self.badge_focus.set("Waiting", MUTE)
        self.ctx.ui_call(_apply)

    # -----------------------------------------------------------
    # Runner control
    # -----------------------------------------------------------
    def _preflight_check(self):
        """Refuse to start a run whose profile does not match its macro.

        Returns a message describing the problem, or None when it is safe to
        start. Catching it here means the user sees a dialog instead of the
        runner silently substituting some other file's coordinates.
        """
        cfg = self.current_cfg
        mode = self._cfg_mode()

        if self.tab_selection.pids == frozenset():
            return "No tabs are selected. Open Tabs and pick at least one Roblox client."

        if mode == "mutation":
            targets = cfg.get("targets", {})
            if not targets.get("pet_select") or not targets.get("mutate"):
                return ("This profile has no mutation click points yet.\n\n"
                        "Press 'Calibrate Mutation' on the Slots & Coordinates page.")
            if not cfg.get("scan_region"):
                return "This profile has no scan region set yet."
            if cfg.get("slots") or cfg.get("common_actions"):
                return ("This mutation profile still has Goliath slots in it. "
                        "Press Save Profile once to clear them.")
            return None

        if not cfg.get("slots"):
            return "This profile has no slots set up."
        if not cfg.get("common_actions", {}).get("confirm_yes"):
            return "This profile is missing the 'Confirm yes' point."
        if cfg.get("targets"):
            return ("This Goliath profile still has mutation points in it. "
                    "Press Save Profile once to clear them.")
        return None

    def start_macro(self):
        if self.is_running:
            return
        # A previous run's thread can still be winding down after Stop. Letting a
        # second runner start now means two loops fighting over window focus.
        if self.runner_thread is not None and self.runner_thread.is_alive():
            self._append_log("Still stopping the last run. Try again in a second.")
            return
        if not self._sync_ui_to_config():
            return

        problem = self._preflight_check()
        if problem:
            self._append_log(f"Cannot start: {problem}")
            messagebox.showwarning("Cannot start", problem)
            return

        self.is_running = True
        self._stop_requested = False
        self.status_pill.set("Running", CYAN)
        self.btn_start.set_state("disabled")
        self.btn_stop.set_state("normal")
        self.btn_pause.set_state("normal")

        # Pick the runner from the loaded profile, not from the toolbar toggle:
        # a Goliath profile must never be fed to MutationRunner or vice versa.
        run_mode = self._cfg_mode()
        mode_name = self.MODES[run_mode]
        self._append_log(f"Starting {mode_name} macro ({self.active_profile_name})")
        self._append_log(scaling.describe_environment())

        runner_cls = MutationRunner if run_mode == "mutation" else MacroRunner

        def run_worker():
            try:
                self.runner = runner_cls(
                    config_data=self.current_cfg,
                    log_callback=self._append_log,
                    stats_callback=self._update_stats,
                    frame_callback=self._update_slot_frame,
                    hotkeys=self.ctx.hotkeys,
                    automation=self.ctx.automation,
                    tab_selection=self.tab_selection,
                )
                # Stop may have been pressed while the runner was being built.
                if not self._stop_requested:
                    self.runner.run()
            except Exception as e:
                self._append_log(f"Error during execution: {e}")
            finally:
                self.ctx.ui_call(self._on_runner_stopped)

        self.runner_thread = threading.Thread(target=run_worker, daemon=True)
        self.runner_thread.start()

    def stop_macro(self):
        self._stop_requested = True
        if self.runner:
            self.runner.stop()
        if self.runner_thread is not None and self.runner_thread.is_alive():
            # The worker calls _on_runner_stopped once the run has really ended;
            # until then say so, rather than claiming the macro is already idle.
            self.status_pill.set("Stopping", WARNING)
            self.lbl_active_window.config(text="Stopping after the current step...", fg=MUTE)
            self.btn_stop.set_state("disabled")
            self.btn_pause.set_state("disabled")
            return
        self._on_runner_stopped()

    def toggle_pause(self):
        if not self.runner:
            return
        self.runner.toggle_pause()
        if self.runner.paused:
            self.status_pill.set("Paused", WARNING)
            self.btn_pause.set_text("Resume  F7")
        else:
            self.status_pill.set("Running", CYAN)
            self.btn_pause.set_text("Pause  F7")

    def _on_runner_stopped(self):
        self.is_running = False
        self.status_pill.set("Idle", MUTE)
        self.badge_focus.set("Stopped", MUTE)
        self.lbl_active_window.config(text="Macro stopped. Ready.", fg=MUTE)
        self.btn_start.set_state("normal")
        self.btn_stop.set_state("disabled")
        self.btn_pause.set_state("disabled")
        self.btn_pause.set_text("Pause  F7")

