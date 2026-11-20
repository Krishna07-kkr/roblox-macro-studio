"""The Notify page: Discord alerts about your clients (spec 001, US6).

Webhook addresses are secrets: they are checked when added, stored encrypted for your Windows
user (never in the settings file as text), shown only masked, and never written to the log.
Events from the other tools arrive over the event bus. Delivery happens on one long-lived
background worker so a slow or unreachable Discord never freezes the window.
"""

import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from macro_studio.core import ui_theme as ui
from macro_studio.core.net import DISCORD_HOSTS, Http
from macro_studio.core.ui_theme import (
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
from macro_studio.tools.macro.validation import FieldError, read_number
from macro_studio.tools.notify import webhook as wh
from macro_studio.tools.notify.service import RULES, NotifyService, default_options

EVENTS = tuple(RULES)
PINGS = (("No ping", "none"), ("@everyone", "everyone"), ("@here", "here"), ("A user (id)", "user"),
         ("A role (id)", "role"))
MAX_NAME = 30
SUMMARY_CHECK_S = 60


class NotifyTool:
    page_titles = ["Notifications"]
    DEFAULTS = {"hook_names": [], "enabled": False, "embeds": True, "ping": "none", "ping_id": "",
                "summary_min": 0, **{f"ev_{e}": r.default_on for e, r in RULES.items()}}
    SUMMARY_JOB = "notify:summary"

    def __init__(self, shell, ctx, service=None, http=None):
        self.shell, self.ctx, self.root = shell, ctx, shell.root
        self.settings = ctx.settings.view("notify", self.DEFAULTS)
        self.http = http or Http(DISCORD_HOSTS)
        self.service = service or NotifyService(
            hooks=self.hooks, send=self._send, clock=time.monotonic, log=ctx.log,
            options=self._options())
        self._wake = threading.Event()
        self._stopping = False
        self._worker = None
        self._unsubscribers = []

    # ------------------------------------------------------------ settings and secrets
    def _options(self):
        options = default_options()
        for key in options:
            options[key] = self.settings.get(key)
        return options

    def hooks(self):
        """[(name, address)] - addresses are read from the encrypted store on demand."""
        found = []
        for name in self.settings.get("hook_names"):
            url = self.ctx.settings.get_secret("notify", f"hook:{name}")
            if url:
                found.append((name, url))
        return found

    def _send(self, url, message, file=None):
        return wh.send(self.http, url, message, file=file)

    # ------------------------------------------------------------ lifecycle
    def start(self):
        self._worker = threading.Thread(target=self._work, name="studio-notify", daemon=True)
        self._worker.start()
        for event in EVENTS:
            self._unsubscribers.append(self.ctx.events.subscribe(
                event, lambda _event=event, **payload: self._on_event(_event, **payload)))
        self._unsubscribers.append(self.ctx.events.subscribe(
            "clients_updated", lambda rows, **_x: self.service.set_rows(rows)))
        self.ctx.scheduler.add(self.SUMMARY_JOB, self._summary_job, interval_s=SUMMARY_CHECK_S)

    def stop(self):
        self.ctx.scheduler.remove(self.SUMMARY_JOB)
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        self._stopping = True
        self._wake.set()

    def _on_event(self, name, **payload):
        self.service.on_event(name, **payload)
        self._wake.set()

    def _summary_job(self):
        if self.service.summary_due():
            self.service.send_summary()
            self._wake.set()

    def _work(self):
        while True:
            self._wake.wait()
            self._wake.clear()
            if self._stopping:
                return
            if self.service.deliver_pending():
                self.ctx.ui_call(self.refresh_list)

    # ------------------------------------------------------------ page
    def build_pages(self, frames):
        (page,) = frames
        hooks = Card(page, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        hooks.pack(fill="x", padx=SP_XL, pady=(SP_XL, SP_SM))
        ui.heading(hooks.body, "Discord webhooks", size=15).pack(anchor="w")
        ui.caption(hooks.body, "Create a webhook in your Discord channel settings, then paste its "
                               "address here. It is stored encrypted for your Windows user and "
                               "only ever shown hidden.").pack(anchor="w", pady=(2, SP_SM))
        self.tree = ttk.Treeview(hooks.body, columns=("name", "address", "result"),
                                 show="headings", height=4, style=ui.table_style(self.root))
        for key, label, width in (("name", "Name", 140), ("address", "Address", 300),
                                  ("result", "Last result", 260)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w")
        self.tree.pack(fill="x")
        add = tk.Frame(hooks.body, bg=SURFACE)
        add.pack(fill="x", pady=(SP_SM, 0))
        self.e_name = Field(add, value="", width=14)
        self.e_name.pack(side="left")
        self.e_url = Field(add, value="", width=34)
        self.e_url.entry.config(show="•")
        self.e_url.pack(side="left", padx=SP_SM)
        Button(add, "Add", command=self.add_hook, kind="primary", height=30, page_bg=SURFACE).pack(
            side="left")
        buttons = tk.Frame(hooks.body, bg=SURFACE)
        buttons.pack(fill="x", pady=(SP_SM, 0))
        Button(buttons, "Test selected", command=self.test_selected, kind="secondary", height=30,
               page_bg=SURFACE).pack(side="left")
        Button(buttons, "Remove selected", command=self.remove_selected, kind="ghost", height=30,
               page_bg=SURFACE).pack(side="left", padx=(SP_SM, 0))
        self.lbl_hook_error = tk.Label(hooks.body, text="", font=ui.F_CAPTION(), bg=SURFACE,
                                       fg=ERROR_TEXT, anchor="w", justify="left", wraplength=640)
        self.lbl_hook_error.pack(fill="x", pady=(SP_SM, 0))

        options = Card(page, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        options.pack(fill="x", padx=SP_XL, pady=SP_SM)
        ui.heading(options.body, "What to tell me", size=15).pack(anchor="w", pady=(0, SP_SM))
        self.var = {"enabled": self._toggle(options.body, "Send notifications", "enabled"),
                    "embeds": self._toggle(options.body, "Use coloured cards (embeds)", "embeds")}
        for event, rule in RULES.items():
            self.var[f"ev_{event}"] = self._toggle(options.body, rule.title, f"ev_{event}")
        self.btn_ping = self._row(options.body, "Ping on serious alerts", lambda row: Button(
            row, "", command=self._pick_ping, kind="secondary", height=28, page_bg=SURFACE))
        self.e_ping_id = self._row(options.body, "User or role id (for those pings)",
                                   lambda row: Field(row, value=str(self.settings.get("ping_id")), width=22))
        self.e_summary = self._row(
            options.body, "Status summary every (minutes, 0 = off, up to 1440)",
            lambda row: Field(row, value=str(self.settings.get("summary_min")), width=8))
        self.lbl_error = tk.Label(options.body, text="", font=ui.F_CAPTION(), bg=SURFACE,
                                  fg=ERROR_TEXT, anchor="w", justify="left", wraplength=640)
        self.lbl_error.pack(fill="x", pady=(SP_SM, 0))
        Button(options.body, "Save", command=self.save_options, kind="primary", height=32,
               page_bg=SURFACE).pack(anchor="w", pady=(SP_SM, 0))
        self._ping_text()
        self.refresh_list()

    def _row(self, parent, text, build):
        """A label on the left and the widget `build(row)` makes on the right."""
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=text, font=ui.F_BODY(), bg=SURFACE, fg=INK, anchor="w").pack(side="left")
        widget = build(row)
        widget.pack(side="right")
        return widget

    def _toggle(self, parent, text, key):
        var = tk.BooleanVar(value=bool(self.settings.get(key)))
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=text, font=ui.F_BODY(), bg=SURFACE, fg=INK, anchor="w").pack(side="left")
        Toggle(row, var, page_bg=SURFACE, command=self._toggles_changed).pack(side="right")
        return var

    # ------------------------------------------------------------ webhooks
    def refresh_list(self):
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        urls = dict(self.hooks())
        for name in self.settings.get("hook_names"):
            result = self.service.results.get(name)
            text = "" if result is None else (
                f"{time.strftime('%H:%M:%S', time.localtime())}  " + ("Sent" if result[1] else result[2]))
            self.tree.insert("", "end", iid=name, values=(name, wh.mask_webhook(urls.get(name, "")), text))
        self.tree.selection_set([i for i in selected if self.tree.exists(i)])

    def add_hook(self):
        name = self.e_name.get().strip()
        url = self.e_url.get().strip()
        names = list(self.settings.get("hook_names"))
        problem = None
        if not name or len(name) > MAX_NAME or any(ord(c) < 32 for c in name):
            problem = f"Give the webhook a short name (1-{MAX_NAME} characters)."
        elif name.lower() in {n.lower() for n in names}:
            problem = f"A webhook called '{name}' already exists."
        else:
            ok, why = wh.validate_webhook(url)
            if not ok:
                problem = why
        if problem:
            self.lbl_hook_error.config(text=problem)
            return False
        self.ctx.settings.set_secret("notify", f"hook:{name}", url)
        self.settings.set("hook_names", [*names, name])
        self.ctx.settings.save()
        self.e_url.set("")                                  # do not keep the secret in the widget
        self.e_name.set("")
        self.lbl_hook_error.config(text="")
        self.refresh_list()
        return True

    def _selected(self):
        return list(self.tree.selection())

    def remove_selected(self):
        names = self._selected()
        if not names:
            return
        if not messagebox.askyesno("Remove webhook", "Remove these webhooks?\n\n" + "\n".join(names)):
            return
        kept = [n for n in self.settings.get("hook_names") if n not in names]
        for name in names:
            self.ctx.settings.set_secret("notify", f"hook:{name}", "")     # empties the stored secret
            self.service.results.pop(name, None)
        self.settings.set("hook_names", kept)
        self.ctx.settings.save()
        self.refresh_list()

    def test_selected(self):
        names = self._selected()
        if not names:
            self.lbl_hook_error.config(text="Select a webhook to test.")
            return
        self.lbl_hook_error.config(text="")
        self.ctx.scheduler.add(f"notify:test:{names[0]}", lambda: self._test(names[0]),
                               interval_s=3600, run_now=True, isolated=True, once=True)

    def _test(self, name):
        self.service.test(name)
        self.ctx.ui_call(self.refresh_list)

    # ------------------------------------------------------------ options
    def _toggles_changed(self):
        for key, var in self.var.items():
            self.settings.set(key, bool(var.get()))
            self.service.options[key] = bool(var.get())
        self.ctx.settings.save()

    def _pick_ping(self):
        menu = tk.Menu(self.root, tearoff=0)
        for label, kind in PINGS:
            menu.add_command(label=label, command=lambda k=kind: self._set_ping(k))
        menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())

    def _set_ping(self, kind):
        self.settings.set("ping", kind)
        self.service.options["ping"] = kind
        self.ctx.settings.save()
        self._ping_text()

    def _ping_text(self):
        names = dict((kind, label) for label, kind in PINGS)
        self.btn_ping.set_text(names.get(self.settings.get("ping"), "No ping"))

    def save_options(self):
        self.e_summary.set_error(None)
        self.lbl_error.config(text="")
        try:
            minutes = read_number(self.e_summary, "The summary interval", minimum=0, maximum=1440)
            if 0 < minutes < 5:
                raise FieldError(self.e_summary, "The summary interval can't be between 1 and 4 "
                                                 "minutes. Use 0 to switch it off, or 5 or more.")
        except FieldError as err:
            err.field.set_error(str(err))
            self.lbl_error.config(text=str(err))
            return False
        ping_id = self.e_ping_id.get().strip()
        if ping_id and not ping_id.isdigit():
            self.e_ping_id.set_error("The id is a number.")
            self.lbl_error.config(text="The user or role id must be a number.")
            return False
        self.settings.set("summary_min", minutes)
        self.settings.set("ping_id", ping_id)
        self.service.options["summary_min"], self.service.options["ping_id"] = minutes, ping_id
        self.ctx.settings.save()
        return True
