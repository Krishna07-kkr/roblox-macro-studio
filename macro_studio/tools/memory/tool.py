"""The Memory page: keep Roblox clients from hogging RAM (spec 001, US3).

Automatic trimming is off until you turn it on. When on, every few seconds each client over
the limit (or every client, if you choose) has its idle memory moved out of RAM. The game keeps
running and pulls back what it needs. "Trim now" does the same once, on demand.

The client list arrives through the event bus (`clients_updated`), so this tool never imports
the Clients tool.
"""

import time
import tkinter as tk

from macro_studio.core import ui_theme as ui
from macro_studio.core.processes import process_info, trim_process
from macro_studio.core.ui_theme import (
    BODY,
    CANVAS,
    ERROR_TEXT,
    INK,
    MUTE,
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
from macro_studio.tools.macro.validation import FieldError, read_number
from macro_studio.tools.memory.service import MAX_THRESHOLD_MB, MIN_THRESHOLD_MB, MemoryService

MIN_INTERVAL_S, MAX_INTERVAL_S = 1, 600


class MemoryTool:
    page_titles = ["Memory"]
    DEFAULTS = {"enabled": False, "threshold_mb": 350, "interval_s": 3, "trim_all": False}
    AUTO_JOB = "memory:auto"
    NOW_JOB = "memory:trim_now"

    def __init__(self, shell, ctx, service=None):
        self.shell, self.ctx, self.root = shell, ctx, shell.root
        self.settings = ctx.settings.view("memory", self.DEFAULTS)
        self.service = service or MemoryService(
            trim=trim_process, process_info=process_info, publish=ctx.events.publish,
            clock=time.monotonic, log=ctx.log,
            threshold_mb=self.settings.get("threshold_mb"), trim_all=self.settings.get("trim_all"),
            enabled=self.settings.get("enabled"))
        self._unsubscribers = []
        self.last_text = ""

    # ------------------------------------------------------------ wiring
    def start(self):
        self.ctx.actions["optimize"] = self.trim_now
        self._unsubscribers.append(self.ctx.events.subscribe("clients_updated", self._on_clients))
        self._unsubscribers.append(self.ctx.events.subscribe("ram_over", self._on_over))
        self._schedule_auto()

    def stop(self):
        self.ctx.actions.pop("optimize", None)
        self.ctx.scheduler.remove(self.AUTO_JOB)
        self.ctx.scheduler.remove(self.NOW_JOB)
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    def _schedule_auto(self):
        self.ctx.scheduler.add(self.AUTO_JOB, self._auto_job,
                               interval_s=self.settings.get("interval_s"))

    def _on_clients(self, rows, **_extra):
        self.service.set_rows(rows)

    def _on_over(self, rows, threshold_mb, **_extra):
        names = ", ".join(r.account or f"PID {r.pid}" for r in rows)
        self.ctx.log(f"[MEMORY] Over {threshold_mb} MB: {names}.")

    # ------------------------------------------------------------ jobs (scheduler thread)
    def _auto_job(self):
        self.service.check_limit()
        report = self.service.run_auto()
        if report is not None and (report.trimmed or report.skipped):
            self.ctx.ui_call(lambda: self._show(report))

    def _trim_job(self):
        report = self.service.trim_now()
        self.ctx.ui_call(lambda: self._show(report))

    def trim_now(self):
        """Button: one pass now, on the scheduler thread, then the job removes itself."""
        self.ctx.scheduler.add(self.NOW_JOB, self._trim_job, interval_s=3600, run_now=True,
                               once=True)

    # ------------------------------------------------------------ page
    def build_pages(self, frames):
        (page,) = frames
        card = Card(page, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        card.pack(fill="x", padx=SP_XL, pady=(SP_XL, SP_SM))
        ui.heading(card.body, "Automatic trimming", size=15).pack(anchor="w")
        ui.caption(card.body, "Moves a client's idle memory out of RAM. The game keeps running "
                              "and takes back what it needs. Trimming very often can make a "
                              "game stutter.").pack(anchor="w", pady=(2, SP_SM))

        self.var_enabled = tk.BooleanVar(value=self.settings.get("enabled"))
        self._row(card.body, "Trim automatically",
                  lambda row: Toggle(row, self.var_enabled, page_bg=SURFACE,
                                     command=self._toggle_changed))
        self.e_limit = self._row(
            card.body, f"Limit per client (MB, {MIN_THRESHOLD_MB}-{MAX_THRESHOLD_MB})",
            lambda row: Field(row, value=str(self.settings.get("threshold_mb")), width=8))
        self.e_interval = self._row(
            card.body, f"Check every (seconds, {MIN_INTERVAL_S}-{MAX_INTERVAL_S})",
            lambda row: Field(row, value=str(self.settings.get("interval_s")), width=8))
        self.var_all = tk.BooleanVar(value=self.settings.get("trim_all"))
        self._row(card.body, "Trim every client, not only the big ones",
                  lambda row: Toggle(row, self.var_all, page_bg=SURFACE,
                                     command=self._toggle_changed))

        self.lbl_error = tk.Label(card.body, text="", font=ui.F_CAPTION(), bg=SURFACE,
                                  fg=ERROR_TEXT, anchor="w", justify="left")
        self.lbl_error.pack(fill="x", pady=(SP_SM, 0))
        buttons = tk.Frame(card.body, bg=SURFACE)
        buttons.pack(fill="x", pady=(SP_SM, 0))
        Button(buttons, "Apply", command=self.apply, kind="primary", height=32,
               page_bg=SURFACE).pack(side="left")
        Button(buttons, "Trim now", command=self.trim_now, kind="secondary", height=32,
               page_bg=SURFACE).pack(side="left", padx=(SP_SM, 0))

        self.lbl_result = tk.Label(page, text="Nothing trimmed yet.", font=ui.F_BODY(), bg=CANVAS,
                                   fg=BODY, anchor="w", justify="left")
        self.lbl_result.pack(fill="x", padx=SP_XL, pady=SP_SM)

    def _row(self, parent, text, build):
        """A label on the left and the widget `build(row)` makes on the right."""
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=text, font=ui.F_BODY(), bg=SURFACE, fg=INK, anchor="w").pack(side="left")
        widget = build(row)
        widget.pack(side="right")
        return widget

    # ------------------------------------------------------------ settings
    def _toggle_changed(self):
        self.service.enabled = bool(self.var_enabled.get())
        self.service.trim_all = bool(self.var_all.get())
        self.settings.set("enabled", self.service.enabled)
        self.settings.set("trim_all", self.service.trim_all)
        self.ctx.settings.save()

    def apply(self):
        """Check the two numbers, then use and save them. A bad value is outlined and named."""
        for field in (self.e_limit, self.e_interval):
            field.set_error(None)
        self.lbl_error.config(text="")
        try:
            limit = read_number(self.e_limit, "The limit", minimum=MIN_THRESHOLD_MB,
                                maximum=MAX_THRESHOLD_MB)
            interval = read_number(self.e_interval, "The check interval", minimum=MIN_INTERVAL_S,
                                   maximum=MAX_INTERVAL_S)
        except FieldError as err:
            err.field.set_error(str(err))
            self.lbl_error.config(text=str(err))
            return False
        self.service.threshold_mb = limit
        self.settings.set("threshold_mb", limit)
        self.settings.set("interval_s", interval)
        self.ctx.settings.save()
        self._schedule_auto()                        # picks up the new interval
        self.lbl_result.config(text=f"Saved: limit {limit} MB, every {interval} s.", fg=BODY)
        return True

    # ------------------------------------------------------------ showing results
    def _show(self, report):
        """Tk thread: say what the last pass did."""
        stamp = time.strftime("%H:%M:%S")
        if report.trimmed:
            text = (f"{stamp}  Trimmed {len(report.trimmed)} client(s), freed "
                    f"{report.freed_mb:,.0f} MB.")
        else:
            text = f"{stamp}  Nothing needed trimming."
        if report.skipped:
            text += "  Skipped: " + ", ".join(f"PID {pid} ({why})" for pid, why in report.skipped)
        self.last_text = text
        self.lbl_result.config(text=text, fg=INK if report.trimmed else MUTE)
