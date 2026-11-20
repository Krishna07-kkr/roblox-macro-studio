"""The Anti-AFK page: keep clients from being kicked for idling (spec 001, US5).

Every interval (plus or minus a random jitter) each client is brought forward and sent a short
key sequence, then your focus is given back. Off until you turn it on. It waits while a macro
runs or while you are using the PC (if that guard is on), and the emergency stop ends a round
at once. Rounds run on an isolated scheduler worker, never on the Tk thread.
"""

import random
import time
import tkinter as tk

from macro_studio.core import ui_theme as ui
from macro_studio.core.ui_theme import (
    BODY,
    CANVAS,
    ERROR_TEXT,
    INK,
    R_LG,
    SP_MD,
    SP_SM,
    SP_XL,
    SURFACE,
    Button,
    Card,
    Field,
    Toggle,
)
from macro_studio.tools.afk import system
from macro_studio.tools.afk.keys import (
    PRESETS,
    BadKey,
    KeyRecorder,
    format_sequence,
    parse_sequence,
)
from macro_studio.tools.afk.service import DEFER_S, AfkService
from macro_studio.tools.macro.validation import FieldError, read_number

POLL_MS = 30
STOP_POLL_S = 0.02


class AfkTool:
    page_titles = ["Anti-AFK"]
    DEFAULTS = {"enabled": False, "keys": "space", "interval_s": 60, "jitter_s": 5, "hold_ms": 80,
                "idle_guard": False, "idle_secs": 30, "restore_focus": True,
                "skip_minimised": True}
    JOB = "afk:round"
    NOW = "afk:now"

    def __init__(self, shell, ctx, service=None, recorder_factory=None):
        self.shell, self.ctx, self.root = shell, ctx, shell.root
        self.settings = ctx.settings.view("afk", self.DEFAULTS)
        self.service = service or self._build_service()
        self._recorder_factory = recorder_factory or self._real_recorder
        self.recorder = None
        self._deferred = False
        self._counter = 0
        self._pending = set()
        self._after = None
        if service is None:
            self._load_keys()

    # ------------------------------------------------------------ wiring
    def _wait(self, seconds):
        """Sleep, but give up the moment the emergency stop is pressed. False = stopped."""
        end = time.monotonic() + seconds
        stop = self.ctx.automation.stop_event
        while time.monotonic() < end:
            if stop.is_set():
                return False
            time.sleep(min(STOP_POLL_S, max(0.0, end - time.monotonic())))
        return not stop.is_set()

    def _build_service(self):
        return AfkService(
            io=system.RealIO(), automation=self.ctx.automation, wait=self._wait,
            clock=time.monotonic, rng=random, publish=self.ctx.events.publish, log=self.ctx.log,
            interval_s=self.settings.get("interval_s"), jitter_s=self.settings.get("jitter_s"),
            hold_ms=self.settings.get("hold_ms"), idle_guard=self.settings.get("idle_guard"),
            idle_secs=self.settings.get("idle_secs"),
            restore_focus=self.settings.get("restore_focus"),
            skip_minimised=self.settings.get("skip_minimised"),
            enabled=self.settings.get("enabled"))

    def _load_keys(self):
        try:
            self.service.set_tokens(parse_sequence(self.settings.get("keys")))
        except BadKey as err:
            self.ctx.log(f"[AFK] The saved keys were not usable ({err}); using space.")
            self.service.set_tokens(parse_sequence("space"))

    def _real_recorder(self):
        return KeyRecorder(is_down=system.key_down, now=time.monotonic, foreground=system.RealIO().foreground,
                           studio_hwnd=self.root.winfo_id())

    def start(self):
        self.ctx.actions["toggle_afk"] = lambda: self.ctx.ui_call(self.toggle_enabled)
        self._schedule()

    def toggle_enabled(self):
        """The F1 action: flip the switch as if it had been clicked."""
        self.var_enabled.set(not self.var_enabled.get())
        self._enabled_changed()

    def stop(self):
        self.ctx.actions.pop("toggle_afk", None)
        self.ctx.scheduler.remove(self.JOB)
        for name in list(self._pending):
            self.ctx.scheduler.remove(name)
        self._pending.clear()
        if self._after is not None:
            self.root.after_cancel(self._after)
            self._after = None

    # ------------------------------------------------------------ scheduling
    def _schedule(self, interval=None, jitter=None):
        self.ctx.scheduler.add(
            self.JOB, self._round_job, isolated=True,
            interval_s=self.settings.get("interval_s") if interval is None else interval,
            jitter_s=self.settings.get("jitter_s") if jitter is None else jitter)

    def _round_job(self):
        report = self.service.run_scheduled()
        if report is None:
            return
        if report.deferred and not self._deferred:
            self._deferred = True
            self._schedule(interval=DEFER_S, jitter=0)          # look again soon
        elif not report.deferred and self._deferred:
            self._deferred = False
            self._schedule()                                    # back to the normal rhythm
        if report.sent or report.skipped:
            self.ctx.ui_call(lambda: self._say(self._describe(report)))

    @staticmethod
    def _describe(report):
        text = f"{time.strftime('%H:%M:%S')}  Sent to {report.sent} client(s)."
        if report.skipped:
            text += "  Skipped: " + ", ".join(f"PID {p} ({why})" for p, why in report.skipped)
        return text

    # ------------------------------------------------------------ page
    def build_pages(self, frames):
        (page,) = frames
        card = Card(page, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        card.pack(fill="x", padx=SP_XL, pady=(SP_XL, SP_SM))
        ui.heading(card.body, "Anti-AFK", size=15).pack(anchor="w")
        ui.caption(card.body, "Sends a short key sequence to every client now and then, so Roblox "
                              "does not kick idle players. It uses real keyboard input. F6, F7 "
                              "and Esc are the studio's own keys and cannot be sent.").pack(
            anchor="w", pady=(2, SP_SM))

        self.var_enabled = tk.BooleanVar(value=self.settings.get("enabled"))
        self._row(card.body, "Send keys automatically",
                  lambda row: Toggle(row, self.var_enabled, page_bg=SURFACE,
                                     command=self._enabled_changed))
        self.e_keys = self._row(card.body, "Keys (comma separated)",
                                lambda row: Field(row, value=self.settings.get("keys"), width=22))
        tools = tk.Frame(card.body, bg=SURFACE)
        tools.pack(fill="x", pady=(0, SP_SM))
        Button(tools, "Presets", command=self._pick_preset, kind="secondary", height=28,
               page_bg=SURFACE).pack(side="left")
        self.btn_record = Button(tools, "Record", command=self.toggle_record, kind="secondary",
                                 height=28, page_bg=SURFACE)
        self.btn_record.pack(side="left", padx=(SP_SM, 0))
        Button(tools, "Clear", command=lambda: self.e_keys.set(""), kind="ghost", height=28,
               page_bg=SURFACE).pack(side="left", padx=(SP_SM, 0))

        self.e_interval = self._number(card.body, "Every (seconds, 5-3600)", "interval_s")
        self.e_jitter = self._number(card.body, "Random extra (seconds, 0-600)", "jitter_s")
        self.e_hold = self._number(card.body, "Hold each key (ms, 10-1000)", "hold_ms")
        self.var_idle = self._toggle(card.body, "Wait while I am using the PC", "idle_guard")
        self.e_idle = self._number(card.body, "...idle for (seconds, 5-3600)", "idle_secs")
        self.var_focus = self._toggle(card.body, "Give focus back afterwards", "restore_focus")
        self.var_min = self._toggle(card.body, "Skip minimised clients", "skip_minimised")

        self.lbl_error = tk.Label(card.body, text="", font=ui.F_CAPTION(), bg=SURFACE,
                                  fg=ERROR_TEXT, anchor="w", justify="left", wraplength=640)
        self.lbl_error.pack(fill="x", pady=(SP_SM, 0))
        buttons = tk.Frame(card.body, bg=SURFACE)
        buttons.pack(fill="x", pady=(SP_SM, 0))
        Button(buttons, "Apply", command=self.apply, kind="primary", height=32,
               page_bg=SURFACE).pack(side="left")
        Button(buttons, "Send now", command=self.send_now, kind="secondary", height=32,
               page_bg=SURFACE).pack(side="left", padx=(SP_SM, 0))
        self.lbl_result = tk.Label(page, text="Nothing sent yet.", font=ui.F_BODY(), bg=CANVAS,
                                   fg=BODY, anchor="w", justify="left", wraplength=700)
        self.lbl_result.pack(fill="x", padx=SP_XL, pady=SP_SM)

    def _row(self, parent, text, build):
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=text, font=ui.F_BODY(), bg=SURFACE, fg=INK, anchor="w").pack(side="left")
        widget = build(row)
        widget.pack(side="right")
        return widget

    def _number(self, parent, text, key):
        return self._row(parent, text, lambda row: Field(row, value=str(self.settings.get(key)),
                                                         width=8))

    def _toggle(self, parent, text, key):
        var = tk.BooleanVar(value=self.settings.get(key))
        self._row(parent, text, lambda row: Toggle(row, var, page_bg=SURFACE,
                                                   command=self._options_changed))
        return var

    # ------------------------------------------------------------ settings
    def _enabled_changed(self):
        self.service.enabled = bool(self.var_enabled.get())
        self.settings.set("enabled", self.service.enabled)
        self.ctx.settings.save()

    def _options_changed(self):
        self.service.idle_guard = bool(self.var_idle.get())
        self.service.restore_focus = bool(self.var_focus.get())
        self.service.skip_minimised = bool(self.var_min.get())
        for key, var in (("idle_guard", self.var_idle), ("restore_focus", self.var_focus),
                         ("skip_minimised", self.var_min)):
            self.settings.set(key, bool(var.get()))
        self.ctx.settings.save()

    def apply(self):
        """Check the keys and numbers; a bad one is outlined and named, nothing is changed."""
        fields = (self.e_keys, self.e_interval, self.e_jitter, self.e_hold, self.e_idle)
        for field in fields:
            field.set_error(None)
        self.lbl_error.config(text="")
        try:
            tokens = parse_sequence(self.e_keys.get())
        except BadKey as err:
            self.e_keys.set_error(str(err))
            self.lbl_error.config(text=str(err))
            return False
        try:
            interval = read_number(self.e_interval, "The interval", minimum=5, maximum=3600)
            jitter = read_number(self.e_jitter, "The random extra", minimum=0, maximum=600)
            hold = read_number(self.e_hold, "The hold time", minimum=10, maximum=1000)
            idle = read_number(self.e_idle, "The idle time", minimum=5, maximum=3600)
        except FieldError as err:
            err.field.set_error(str(err))
            self.lbl_error.config(text=str(err))
            return False
        self.service.set_tokens(tokens)
        self.service.interval_s, self.service.jitter_s = interval, jitter
        self.service.hold_ms, self.service.idle_secs = hold, idle
        for key, value in (("keys", format_sequence(tokens)), ("interval_s", interval),
                           ("jitter_s", jitter), ("hold_ms", hold), ("idle_secs", idle)):
            self.settings.set(key, value)
        self.ctx.settings.save()
        self._schedule()
        self._say(f"Saved: {format_sequence(tokens)} every {interval} s (+/- {jitter}).")
        return True

    def _say(self, text, error=False):
        self.lbl_result.config(text=text, fg=ERROR_TEXT if error else BODY)

    # ------------------------------------------------------------ presets and recording
    def _pick_preset(self):
        menu = tk.Menu(self.root, tearoff=0)
        for name, text in PRESETS.items():
            menu.add_command(label=name, command=lambda t=text: self.e_keys.set(t))
        menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())

    def toggle_record(self):
        if self.recorder is not None:
            self._finish_recording()
            return
        self.recorder = self._recorder_factory()
        self.btn_record.set_text("Stop recording")
        self._say("Recording: press the keys or buttons now. Esc stops; it also stops after 20 s.")
        self._poll_recorder()

    def _poll_recorder(self):
        if self.recorder is None:
            return
        self.recorder.tick()
        if self.recorder.finished:
            self._finish_recording()
        else:
            self._after = self.root.after(POLL_MS, self._poll_recorder)

    def _finish_recording(self):
        recorder, self.recorder = self.recorder, None
        if self._after is not None:
            self.root.after_cancel(self._after)
            self._after = None
        self.btn_record.set_text("Record")
        tokens = recorder.tokens() if recorder is not None else []
        if tokens:
            self.e_keys.set(format_sequence(tokens))
            self._say(f"Recorded: {format_sequence(tokens)}. Press Apply to use it.")
        else:
            self._say("Nothing was recorded.")

    # ------------------------------------------------------------ send now
    def send_now(self):
        self._counter += 1
        name = f"{self.NOW}:{self._counter}"
        self._pending.add(name)

        def work():
            try:
                report = self.service.run_round()
            finally:
                self._pending.discard(name)
            text = report.note or self._describe(report)
            self.ctx.ui_call(lambda: self._say(text, error=bool(report.note) and not report.sent))
        self.ctx.scheduler.add(name, work, interval_s=3600, run_now=True, isolated=True, once=True)
