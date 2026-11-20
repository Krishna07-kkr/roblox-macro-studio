"""The Clients page: every open Roblox client, and what you can do with it (spec 001, US2).

Shows account, game, memory, process id, uptime and status for each client, refreshed on its
own worker thread. Select clients and Focus, Trim memory, Rename or Close them. Nothing
slow runs on the Tk thread: scans run as an isolated scheduler job and actions run one at a
time on a single long-lived worker (a thread per action would leak a Windows handle each).
"""

import queue
from typing import Literal
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from macro_studio.core import ui_theme as ui
from macro_studio.core.net import ROBLOX_HOSTS, Http
from macro_studio.core.roblox import NameLookup, default_log_folder, list_logs
from macro_studio.core.ui_theme import (
    BODY,
    CANVAS,
    INK,
    MUTE,
    SP_MD,
    SP_SM,
    SP_XL,
    SURFACE,
    Button,
    Card,
    Toggle,
)
from macro_studio.tools.clients import system
from macro_studio.tools.clients.service import ClientsService, sort_rows

COLUMNS: tuple[tuple[str, str, int, Literal["w", "e"]], ...] = (   # id, heading, width, anchor
    ("account", "Account", 190, "w"),
    ("game", "Game", 230, "w"),
    ("ram", "RAM (MB)", 90, "e"),
    ("pid", "PID", 80, "e"),
    ("uptime", "Uptime", 90, "e"),
    ("status", "Status", 120, "w"),
)
NUMERIC_FIRST = {"ram", "uptime", "pid"}          # first click on these sorts biggest first
EMPTY = "No Roblox client is open. Start the game and its window shows up here."
NO_NAME = "-"


def format_uptime(seconds):
    hours, rest = divmod(int(seconds), 3600)
    return f"{hours:02d}:{rest // 60:02d}:{rest % 60:02d}"


class ClientsTool:
    page_titles = ["Clients"]
    DEFAULTS = {"scan_seconds": 3.0, "rename": False, "rename_with_game": False,
                "close_dialogs": False}
    JOB = "clients:scan"

    def __init__(self, shell, ctx, service=None, lookup=None):
        self.shell, self.ctx, self.root = shell, ctx, shell.root
        self.settings = ctx.settings.view("clients", self.DEFAULTS)
        self.lookup = lookup or NameLookup(Http(ROBLOX_HOSTS))
        self.service = service or self._build_service()
        self.sort_key, self.sort_ascending = "ram", False
        self.rows = []
        self._shown = None
        self._actions = queue.Queue()
        self._worker = None
        self._unsubscribers = []

    # ------------------------------------------------------------ wiring
    def _build_service(self):
        return ClientsService(
            processes=system.list_processes, windows=system.list_windows,
            list_logs=lambda: list_logs(default_log_folder()), read_text=system.read_log_text,
            lookup=self.lookup, publish=self.ctx.events.publish, clock=time.monotonic,
            wall_clock=time.time, process_info=system.process_info,
            set_title=system.set_window_title, focus=system.focus_window,
            trim=system.trim_process, terminate=system.terminate_process,
            rename=self.settings.get("rename"),
            rename_with_game=self.settings.get("rename_with_game"),
            close_dialogs=self.settings.get("close_dialogs"),
            dialogs=system.list_error_dialogs, close_dialog=system.close_window,
            log=self.ctx.log,
            on_rows=lambda rows: self.ctx.events.publish("clients_updated", rows=rows))

    def start(self):
        self._worker = threading.Thread(target=self._work, name="studio-clients-actions",
                                        daemon=True)
        self._worker.start()
        self.ctx.scheduler.add(self.JOB, self._scan_job, interval_s=self.settings.get("scan_seconds"),
                               run_now=True, isolated=True)
        for event, text in (("client_started", "A Roblox client opened"),
                            ("client_closed", "A Roblox client closed"),
                            ("client_hung", "A Roblox client stopped responding"),
                            ("client_recovered", "A Roblox client is responding again")):
            self._unsubscribers.append(self.ctx.events.subscribe(event, self._logger(text)))

    def stop(self):
        self.ctx.scheduler.remove(self.JOB)
        self._actions.put(None)
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    def _logger(self, text):
        def handler(**payload):
            row = payload.get("row")
            who = f" ({row.account or 'PID ' + str(row.pid)})" if row is not None else ""
            self.ctx.log(f"[CLIENTS] {text}{who}.")
        return handler

    # ------------------------------------------------------------ background work
    def _scan_job(self):
        """Scheduler worker thread: look, resolve one name, rename, then hand rows to Tk."""
        rows = self.service.scan()
        self.lookup.tick()
        self.service.auto_rename()
        self.service.close_error_dialogs()
        self.ctx.ui_call(lambda: self.show(rows))

    def _work(self):
        while True:
            job = self._actions.get()
            if job is None:
                return
            try:
                job()
            except OSError as err:
                self.ctx.log(f"[CLIENTS] {err}")
            self._scan_job()

    def _later(self, job):
        self._actions.put(job)

    # ------------------------------------------------------------ page
    def build_pages(self, frames):
        (page,) = frames
        ui.table_style(self.root)
        head = tk.Frame(page, bg=CANVAS)
        head.pack(fill="x", padx=SP_XL, pady=(SP_XL, SP_SM))
        self.lbl_summary = tk.Label(head, text="Looking for Roblox clients...", font=ui.F_BODY(),
                                    bg=CANVAS, fg=BODY, anchor="w")
        self.lbl_summary.pack(side="left")

        bar = tk.Frame(page, bg=CANVAS)
        bar.pack(fill="x", padx=SP_XL, pady=(0, SP_SM))
        for text, command, kind in (("Focus", self.focus_selected, "secondary"),
                                    ("Trim RAM", self.trim_selected, "secondary"),
                                    ("Rename", self.rename_selected, "secondary"),
                                    ("Close", self.close_selected, "danger")):
            Button(bar, text, command=command, kind=kind, height=30, page_bg=CANVAS).pack(
                side="left", padx=(0, SP_SM))
        Button(bar, "Refresh", command=lambda: self._later(lambda: None), kind="ghost", height=30,
               page_bg=CANVAS).pack(side="right")
        Button(bar, "Select none", command=self.select_none, kind="ghost", height=30,
               page_bg=CANVAS).pack(side="right", padx=(0, SP_SM))
        Button(bar, "Select all", command=self.select_all, kind="ghost", height=30,
               page_bg=CANVAS).pack(side="right", padx=(0, SP_SM))

        holder = tk.Frame(page, bg=CANVAS)
        holder.pack(fill="both", expand=True, padx=SP_XL)
        self.tree = ttk.Treeview(holder, columns=[c[0] for c in COLUMNS], show="headings",
                                 style=ui.TABLE_STYLE, selectmode="extended", height=10)
        for key, label, width, anchor in COLUMNS:
            self.tree.heading(key, text=label, command=lambda k=key: self.sort_by(k))
            self.tree.column(key, width=width, anchor=anchor, stretch=key in ("account", "game"))
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda _event: self.focus_selected())
        self.empty_label = tk.Label(holder, text=EMPTY, font=ui.F_BODY(), bg=SURFACE, fg=MUTE)
        self._mark_sort()

        options = Card(page, radius=ui.R_MD, padding=(SP_MD, SP_SM), page_bg=CANVAS)
        options.pack(fill="x", padx=SP_XL, pady=SP_XL)
        self.var_rename = tk.BooleanVar(value=self.settings.get("rename"))
        self.var_game = tk.BooleanVar(value=self.settings.get("rename_with_game"))
        self.var_dialogs = tk.BooleanVar(value=self.settings.get("close_dialogs"))
        self._option(options.body, "Rename windows to the account name", self.var_rename,
                     "rename")
        self._option(options.body, "Add the game name to the title", self.var_game,
                     "rename_with_game")
        self._option(options.body, "Close Roblox error dialogs", self.var_dialogs,
                     "close_dialogs")

    def _option(self, parent, text, var, key):
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", pady=2)
        tk.Label(row, text=text, font=ui.F_BODY(), bg=SURFACE, fg=INK, anchor="w").pack(side="left")
        Toggle(row, var, page_bg=SURFACE, command=lambda: self._set_option(key, var.get())).pack(
            side="right")

    def _set_option(self, key, value):
        self.settings.set(key, bool(value))
        self.ctx.settings.save()
        attribute = {"rename": "rename", "rename_with_game": "rename_with_game",
                     "close_dialogs": "close_dialogs"}[key]
        setattr(self.service, attribute, bool(value))

    # ------------------------------------------------------------ showing rows
    def show(self, rows):
        """Tk thread: put the latest rows on screen, keeping the selection."""
        self.rows = list(rows)
        ordered = sort_rows(self.rows, self.sort_key, self.sort_ascending)
        shown = tuple((r.pid, r.account, r.game, round(r.ram_mb), r.uptime_s, r.status)
                      for r in ordered)
        total = sum(r.ram_mb for r in self.rows)
        noun = "client" if len(self.rows) == 1 else "clients"
        self.lbl_summary.config(text=(f"{len(self.rows)} {noun}, {total:,.0f} MB in use"
                                      if self.rows else "No clients"))
        if shown == self._shown:
            return
        self._shown = shown
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for r in ordered:
            self.tree.insert("", "end", iid=str(r.pid), values=(
                r.account or NO_NAME, r.game or NO_NAME, f"{r.ram_mb:,.0f}", r.pid,
                format_uptime(r.uptime_s), r.status))
        self.tree.selection_set([iid for iid in selected if self.tree.exists(iid)])
        if self.rows:
            self.empty_label.place_forget()
        else:
            self.empty_label.place(relx=0.5, rely=0.5, anchor="center")

    def sort_by(self, key):
        if key == self.sort_key:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_key, self.sort_ascending = key, key not in NUMERIC_FIRST
        self._mark_sort()
        self._shown = None
        self.show(self.rows)

    def _mark_sort(self):
        for key, label, _width, _anchor in COLUMNS:
            arrow = ("  ▲" if self.sort_ascending else "  ▼") if key == self.sort_key else ""
            self.tree.heading(key, text=label + arrow)

    # ------------------------------------------------------------ actions
    def selected_pids(self):
        return [int(iid) for iid in self.tree.selection()]

    def _names(self, pids):
        by_pid = {r.pid: r for r in self.rows}
        return [f"{by_pid[p].account or 'Unnamed'} (PID {p})" for p in pids if p in by_pid]

    def select_all(self):
        self.tree.selection_set(self.tree.get_children())

    def select_none(self):
        self.tree.selection_remove(self.tree.selection())

    def focus_selected(self):
        pids = self.selected_pids()
        if pids:
            self._later(lambda: [self.service.focus(p) for p in pids])

    def trim_selected(self):
        pids = self.selected_pids()
        if pids:
            self._later(lambda: self._report("trimmed", [p for p in pids if self.service.trim_one(p)],
                                             len(pids)))

    def rename_selected(self):
        pids = self.selected_pids()
        if len(pids) != 1:
            messagebox.showinfo("Rename", "Select one client to rename.")
            return
        title = simpledialog.askstring("Rename window", "New window title:", parent=self.root)
        if title and title.strip():
            self._later(lambda: self.service.rename_by_hand(pids[0], title))

    def close_selected(self):
        pids = self.selected_pids()
        if not pids:
            return
        names = "\n".join(self._names(pids))
        if not messagebox.askyesno("Close clients",
                                   f"Close these Roblox clients?\n\n{names}\n\nThe game closes "
                                   f"without saving."):
            return
        self._later(lambda: self._report("closed", [p for p in pids if self.service.close_one(p)],
                                         len(pids)))

    def _report(self, verb, done, asked):
        self.ctx.log(f"[CLIENTS] {len(done)} of {asked} client(s) {verb}.")
