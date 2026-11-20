"""The Layout page: arrange Roblox windows, and let several clients run (spec 001, US4).

Buttons arrange the open windows (cascade, grid, side by side, stack), minimise or restore them
all, on the monitor you pick. Nothing moves while a macro is running. The multi-instance switch
holds Roblox's single-client lock; the first time it is turned on, a notice explains the risk.
Work runs as one-shot scheduler jobs, never on the Tk thread.
"""

import tkinter as tk
from tkinter import messagebox

from macro_studio.core import monitors as monitors_mod
from macro_studio.core import ui_theme as ui
from macro_studio.core import win32
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
from macro_studio.tools.layout import system
from macro_studio.tools.layout.service import LayoutService, MultiInstance
from macro_studio.tools.macro.validation import FieldError, read_number

STYLE_BUTTONS = (("Cascade", "cascade"), ("Grid", "grid"), ("Side by side", "columns"),
                 ("Stack", "stack"))
NOTICE = ("Running several Roblox clients at once, and automating them, may break Roblox's "
          "Terms of Use and can lead to action against your account. You use this at your own "
          "risk, and this app does not hide it from Roblox.\n\nTurn it on?")


class LayoutTool:
    page_titles = ["Layout"]
    DEFAULTS = {"offset_x": 30, "offset_y": 30, "width": 800, "height": 600, "monitor": 0,
                "multi_instance": False, "notice_shown": False}
    JOB = "layout:run"

    def __init__(self, shell, ctx, service=None, multi=None):
        self.shell, self.ctx, self.root = shell, ctx, shell.root
        self.settings = ctx.settings.view("layout", self.DEFAULTS)
        self.service = service or LayoutService(
            windows=system.roblox_windows,
            monitors=lambda: [m.work for m in monitors_mod.list_monitors()],
            place=system.place_window, minimise=system.minimise_window,
            restore=system.restore_window, automation=ctx.automation, log=ctx.log)
        self.multi = multi or MultiInstance(create=win32.create_named_mutex,
                                            close=win32.close_handle,
                                            clients_running=system.clients_running)
        self._pending = set()          # one-shot jobs queued and not yet run
        self._counter = 0

    # ------------------------------------------------------------ lifecycle
    def start(self):
        self.ctx.actions["arrange_grid"] = lambda: self.ctx.ui_call(lambda: self.arrange("grid"))
        if self.settings.get("multi_instance"):          # it was on last time: take the lock again
            result = self.multi.enable()
            if not result.ok:
                self.ctx.log(f"[LAYOUT] Multi-instance could not be turned on at start: "
                             f"{result.reason}")

    def stop(self):
        self.ctx.actions.pop("arrange_grid", None)
        for name in list(self._pending):
            self.ctx.scheduler.remove(name)
        self._pending.clear()
        self.multi.disable()                             # the lock never outlives the app

    # ------------------------------------------------------------ page
    def build_pages(self, frames):
        (page,) = frames
        card = Card(page, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        card.pack(fill="x", padx=SP_XL, pady=(SP_XL, SP_SM))
        ui.heading(card.body, "Arrange windows", size=15).pack(anchor="w")
        ui.caption(card.body, "Windows are ordered by process id, so they keep their places. "
                              "Width and height are the window size.").pack(
            anchor="w", pady=(2, SP_SM))

        bar = tk.Frame(card.body, bg=SURFACE)
        bar.pack(fill="x", pady=(0, SP_SM))
        for text, style in STYLE_BUTTONS:
            Button(bar, text, command=lambda s=style: self.arrange(s), kind="secondary",
                   height=30, page_bg=SURFACE).pack(side="left", padx=(0, SP_SM))
        Button(bar, "Minimise all", command=self.minimise_all, kind="ghost", height=30,
               page_bg=SURFACE).pack(side="left", padx=(SP_SM, SP_SM))
        Button(bar, "Restore all", command=self.restore_all, kind="ghost", height=30,
               page_bg=SURFACE).pack(side="left")

        self.e_ox = self._field(card.body, "Cascade offset X", "offset_x")
        self.e_oy = self._field(card.body, "Cascade offset Y", "offset_y")
        self.e_w = self._field(card.body, "Window width", "width")
        self.e_h = self._field(card.body, "Window height", "height")
        self.btn_monitor = self._labelled(
            card.body, "Monitor",
            lambda row: Button(row, "", command=self._pick_monitor, kind="secondary", height=30,
                               page_bg=SURFACE))
        self._monitor_text()

        self.lbl_result = tk.Label(card.body, text="", font=ui.F_CAPTION(), bg=SURFACE, fg=BODY,
                                   anchor="w", justify="left", wraplength=640)
        self.lbl_result.pack(fill="x", pady=(SP_SM, 0))

        multi = Card(page, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        multi.pack(fill="x", padx=SP_XL, pady=SP_SM)
        ui.heading(multi.body, "Several clients at once", size=15).pack(anchor="w")
        ui.caption(multi.body, "Holds Roblox's single-client lock while this app is open. Turn "
                               "it on before you start the first client.").pack(
            anchor="w", pady=(2, SP_SM))
        self.var_multi = tk.BooleanVar(value=self.multi.active)
        self._labelled(multi.body, "Allow several clients",
                       lambda row: Toggle(row, self.var_multi, page_bg=SURFACE,
                                          command=self.toggle_multi))
        self.lbl_multi = tk.Label(multi.body, text="", font=ui.F_CAPTION(), bg=SURFACE,
                                  fg=ERROR_TEXT, anchor="w", justify="left", wraplength=640)
        self.lbl_multi.pack(fill="x", pady=(SP_SM, 0))

    def _labelled(self, parent, text, build):
        """A label on the left and the widget `build(row)` makes on the right."""
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=text, font=ui.F_BODY(), bg=SURFACE, fg=INK, anchor="w").pack(side="left")
        widget = build(row)
        widget.pack(side="right")
        return widget

    def _field(self, parent, label, key):
        return self._labelled(parent, label, lambda row: Field(
            row, value=str(self.settings.get(key)), width=8))

    # ------------------------------------------------------------ settings and monitors
    def _read(self):
        """The four numbers, or None after outlining the bad one."""
        for field in (self.e_ox, self.e_oy, self.e_w, self.e_h):
            field.set_error(None)
        try:
            values = (
                read_number(self.e_ox, "Offset X", minimum=0, maximum=500),
                read_number(self.e_oy, "Offset Y", minimum=0, maximum=500),
                read_number(self.e_w, "Width", minimum=200, maximum=7680),
                read_number(self.e_h, "Height", minimum=200, maximum=4320))
        except FieldError as err:
            err.field.set_error(str(err))
            self._say(str(err), error=True)
            return None
        for key, value in zip(("offset_x", "offset_y", "width", "height"), values, strict=True):
            self.settings.set(key, value)
        self.ctx.settings.save()
        return values

    def _monitor_names(self):
        found = monitors_mod.list_monitors()
        return [f"Monitor {i + 1}{' (main)' if m.primary else ''}  {m.full.w}x{m.full.h}"
                for i, m in enumerate(found)]

    def _monitor_text(self):
        names = self._monitor_names()
        index = min(self.settings.get("monitor"), len(names) - 1)
        self.btn_monitor.set_text(names[index] if names else "No monitor found")

    def _pick_monitor(self):
        menu = tk.Menu(self.root, tearoff=0)
        for i, name in enumerate(self._monitor_names()):
            menu.add_command(label=name, command=lambda n=i: self._set_monitor(n))
        menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())

    def _set_monitor(self, index):
        self.settings.set("monitor", index)
        self.ctx.settings.save()
        self._monitor_text()

    def _say(self, text, error=False):
        self.lbl_result.config(text=text, fg=ERROR_TEXT if error else BODY)

    # ------------------------------------------------------------ actions
    def _run(self, job):
        """One-shot scheduler job; the result text goes back to the Tk thread.

        Each click gets its own job name: a shared name would let a quick second click replace
        the first action before it ran.
        """
        self._counter += 1
        name = f"{self.JOB}:{self._counter}"
        self._pending.add(name)

        def work():
            try:
                result = job()
            finally:
                self._pending.discard(name)
            self.ctx.ui_call(lambda: self._say(
                "Done." if result.ok else result.reason, error=not result.ok))
        self.ctx.scheduler.add(name, work, interval_s=3600, run_now=True, once=True)

    def arrange(self, style):
        values = self._read()
        if values is None:
            return
        ox, oy, w, h = values
        self._say("")
        monitor = self.settings.get("monitor")
        self._run(lambda: self.service.arrange(style, (ox, oy), (w, h), monitor))

    def minimise_all(self):
        self._run(self.service.minimise_all)

    def restore_all(self):
        self._run(self.service.restore_all)

    def toggle_multi(self):
        if not self.var_multi.get():
            self.multi.disable()
            self.settings.set("multi_instance", False)
            self.ctx.settings.save()
            self.lbl_multi.config(text="")
            return
        if not self.settings.get("notice_shown"):
            if not messagebox.askokcancel("Several clients at once", NOTICE):
                self.var_multi.set(False)
                return
            self.settings.set("notice_shown", True)
        result = self.multi.enable()
        if not result.ok:
            self.var_multi.set(False)
            self.lbl_multi.config(text=result.reason)
            return
        self.lbl_multi.config(text="")
        self.settings.set("multi_instance", True)
        self.ctx.settings.save()
