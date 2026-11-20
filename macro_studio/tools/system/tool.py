"""The System page: hotkeys, start with Windows, window options and text size (spec 001, US7).

F1-F4 run the four everyday actions from any window; each can be moved to another key, but
never to F6, F7 or Esc (the stop and pause keys) and never to a key another action uses.
"Start with Windows" is a per-user startup entry; it needs no administrator rights.
"""

import os
import sys
import tkinter as tk
from tkinter import messagebox

from macro_studio import __version__
from macro_studio.core import autostart as autostart_mod
from macro_studio.core import updater as updater_mod
from macro_studio.core import ui_theme as ui
from macro_studio.core.glass import DEFAULT_STYLE, apply_window_look, glass_supported
from macro_studio.core.tray import TrayIcon
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
    TEXT_SCALES,
    Button,
    Card,
    Field,
    Toggle,
)
from macro_studio.services.hotkeys import HotkeyConflict
from macro_studio.tools.system.service import (
    ACTIONS,
    DEFAULT_KEYS,
    LABELS,
    HotkeyBindings,
    normalize_text_scale,
)


UPDATE_JOB = "system:update"


class SystemTool:
    page_titles = ["System"]
    DEFAULTS = {"hotkeys": {}, "always_on_top": False, "start_minimised": False, "text_scale": 125,
                "text_scale_chosen": False, "glass": True, "glass_style": DEFAULT_STYLE,
                "close_to_tray": True, "check_updates": False}

    def __init__(self, shell, ctx, autostart=autostart_mod, updater=updater_mod):
        self.shell, self.ctx, self.root = shell, ctx, shell.root
        self.settings = ctx.settings.view("system", self.DEFAULTS)
        self.autostart = autostart
        self.updater = updater
        self.release = None
        self.bindings = HotkeyBindings(ctx.hotkeys, lambda: dict(ctx.actions))
        self.fields = {}
        self.tray = None
        self._unsubscribe = None

    # ------------------------------------------------------------ lifecycle
    def start(self):
        self.ctx.actions["toggle_panel"] = lambda: self.ctx.ui_call(self.toggle_panel)
        problems = self.bindings.bind_all(dict(self.settings.get("hotkeys") or {}))
        for action, why in problems.items():
            self.ctx.log(f"[SYSTEM] The saved key for '{LABELS[action]}' could not be used: {why}")
        self.root.attributes("-topmost", bool(self.settings.get("always_on_top")))
        self._start_tray()
        if getattr(sys, "frozen", False):            # only the exe can be updated in place
            self.updater.remove_leftover(sys.executable)
            if self.settings.get("check_updates"):
                self.check_updates(ask=True)
        if self.settings.get("start_minimised"):
            # With a tray icon the window can go out of sight; without one it only minimises,
            # so the window is never lost.
            self.root.after(0, self.root.withdraw if self.tray else self.root.iconify)

    def stop(self):
        self.ctx.actions.pop("toggle_panel", None)
        for action in ACTIONS:
            self.ctx.hotkeys.unbind(action)
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        if self.tray is not None:
            self.tray.stop()
            self.tray = None

    # ------------------------------------------------------------ tray
    def _start_tray(self, tray=None):
        """Put the icon in the tray. Its menu reuses the actions the tools registered."""
        def action(name):
            return lambda: self.ctx.actions.get(name, lambda: None)()
        items = [("Show or hide", action("toggle_panel")),
                 ("Turn anti-AFK on or off", action("toggle_afk")),
                 ("Trim memory now", action("optimize")),
                 ("Arrange windows in a grid", action("arrange_grid")),
                 ("Exit", lambda: self.ctx.ui_call(self.shell.quit_now))]
        icon = tray or TrayIcon("Roblox Macro Studio", on_show=action("toggle_panel"),
                                menu_items=items,
                                on_error=lambda err: self.ctx.log(f"[SYSTEM] Tray: {err}"))
        if icon.start():
            self.tray = icon
            self._unsubscribe = self.ctx.events.subscribe("clients_updated", self._on_clients)
        else:
            self.ctx.log("[SYSTEM] The tray icon could not be created; the window stays on the "
                         "taskbar.")

    def _on_clients(self, rows, **_extra):
        if self.tray is not None:
            total = sum(r.ram_mb for r in rows)
            noun = "client" if len(rows) == 1 else "clients"
            self.tray.set_tooltip(f"Roblox Macro Studio - {len(rows)} {noun}, {total:,.0f} MB")

    def intercept_close(self):
        """The close button hides the window to the tray instead of quitting, if that is on."""
        if self.tray is not None and self.settings.get("close_to_tray"):
            self.root.withdraw()
            return True
        return False

    def toggle_panel(self):
        """The F4 action: minimise the window, or bring it back."""
        if self.root.state() in ("iconic", "withdrawn"):
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        else:
            self.root.iconify()

    # ------------------------------------------------------------ page
    def build_pages(self, frames):
        (page,) = frames
        keys = Card(page, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        keys.pack(fill="x", padx=SP_XL, pady=(SP_XL, SP_SM))
        ui.heading(keys.body, "Hotkeys", size=15).pack(anchor="w")
        ui.caption(keys.body, "Work from any window. F6, F7 and Esc are the stop and pause keys "
                              "and cannot be used. Examples: F9, Ctrl+G.").pack(
            anchor="w", pady=(2, SP_SM))
        for action in ACTIONS:
            self.fields[action] = self._row(
                keys.body, LABELS[action], lambda row, a=action: Field(
                    row, value=self.bindings.keys.get(a) or self._saved_key(a), width=12))
        self.lbl_keys = tk.Label(keys.body, text="", font=ui.F_CAPTION(), bg=SURFACE, fg=BODY,
                                 anchor="w", justify="left", wraplength=640)
        self.lbl_keys.pack(fill="x", pady=(SP_SM, 0))
        bar = tk.Frame(keys.body, bg=SURFACE)
        bar.pack(fill="x", pady=(SP_SM, 0))
        Button(bar, "Apply keys", command=self.apply_keys, kind="primary", height=32,
               page_bg=SURFACE).pack(side="left")
        Button(bar, "Reset to defaults", command=self.reset_keys, kind="ghost", height=32,
               page_bg=SURFACE).pack(side="left", padx=(SP_SM, 0))

        window = Card(page, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        window.pack(fill="x", padx=SP_XL, pady=SP_SM)
        ui.heading(window.body, "Startup and window", size=15).pack(anchor="w", pady=(0, SP_SM))
        self.var_autostart = self._toggle(window.body, "Start with Windows",
                                          self.autostart.is_enabled(), self._autostart_changed)
        self.var_top = self._toggle(window.body, "Keep this window on top",
                                    bool(self.settings.get("always_on_top")), self._top_changed)
        self.var_min = self._toggle(window.body, "Start minimised",
                                    bool(self.settings.get("start_minimised")), self._min_changed)
        self.var_tray = self._toggle(window.body, "Closing the window hides it to the tray",
                                     bool(self.settings.get("close_to_tray")), self._tray_changed)
        self.var_glass = self._toggle(window.body, "Liquid Glass",
                                      bool(self.settings.get("glass")) and glass_supported(),
                                      self._glass_changed)
        self.btn_glass = self._row(window.body, "Glass style", lambda row: Button(
            row, "", command=self._pick_glass, kind="secondary", height=28, page_bg=SURFACE))
        self._glass_text()
        self.btn_scale = self._row(window.body, "Text size", lambda row: Button(
            row, "", command=self._pick_scale, kind="secondary", height=28, page_bg=SURFACE))
        self._scale_text()
        self.lbl_window = tk.Label(window.body, text="", font=ui.F_CAPTION(), bg=SURFACE,
                                   fg=ERROR_TEXT, anchor="w", justify="left", wraplength=640)
        self.lbl_window.pack(fill="x", pady=(SP_SM, 0))

        updates = Card(page, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        updates.pack(fill="x", padx=SP_XL, pady=SP_SM)
        ui.heading(updates.body, "Updates", size=15).pack(anchor="w", pady=(0, SP_SM))
        self.var_updates = self._toggle(updates.body, "Check for updates when the studio starts",
                                        bool(self.settings.get("check_updates")),
                                        self._updates_changed)
        self.btn_update = self._row(updates.body, f"Version {__version__}", lambda row: Button(
            row, "Check now", command=self._update_clicked, kind="secondary", height=28,
            page_bg=SURFACE))
        self.lbl_update = ui.caption(updates.body, "New versions come from the project's GitHub "
                                                   "Releases and are checked before they are used.")
        self.lbl_update.pack(fill="x", pady=(SP_SM, 0))

    GLASS_CHOICES = (("Soft", "soft"), ("Frosted (see-through)", "frosted"))

    def _glass_label(self):
        if not glass_supported():
            return "Needs Windows 11 22H2"
        return "Frosted" if self.settings.get("glass_style") == "frosted" else "Soft"

    def _glass_text(self):
        self.btn_glass.set_text(self._glass_label())

    def _glass_changed(self):
        self.set_glass(bool(self.var_glass.get()), self.settings.get("glass_style"))

    def _pick_glass(self):
        """Choosing a style also turns the glass on."""
        menu = tk.Menu(self.root, tearoff=0)
        for label, style in self.GLASS_CHOICES:
            menu.add_command(label=label, command=lambda s=style: self.set_glass(True, s))
        menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())

    def set_glass(self, on, style):
        """Switch the translucent window look right away (Off, Soft or Frosted)."""
        if on and not glass_supported():
            self.var_glass.set(False)
            self.lbl_window.config(text="Liquid Glass needs Windows 11 version 22H2 or newer.",
                                   fg=ERROR_TEXT)
            return
        self.settings.set("glass", bool(on))
        self.settings.set("glass_style", style)
        self.ctx.settings.save()
        apply_window_look(self.root, bool(on), style)
        self.var_glass.set(bool(on))
        self.lbl_window.config(text="", fg=BODY)
        self._glass_text()

    def _row(self, parent, text, build):
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=text, font=ui.F_BODY(), bg=SURFACE, fg=INK, anchor="w").pack(side="left")
        widget = build(row)
        widget.pack(side="right")
        return widget

    def _toggle(self, parent, text, value, command):
        var = tk.BooleanVar(value=value)
        self._row(parent, text, lambda row: Toggle(row, var, page_bg=SURFACE, command=command))
        return var

    def _saved_key(self, action):
        return (self.settings.get("hotkeys") or {}).get(action) or DEFAULT_KEYS[action]

    # ------------------------------------------------------------ hotkeys
    def apply_keys(self):
        """Try every field; keep the ones that work, outline and name the ones that do not."""
        problems, chosen = [], dict(self.settings.get("hotkeys") or {})
        for field in self.fields.values():
            field.set_error(None)
        for action, field in self.fields.items():
            wanted = field.get().strip()
            if wanted == self.bindings.keys.get(action):
                chosen[action] = wanted
                continue
            try:
                self.bindings.rebind(action, wanted)
                chosen[action] = wanted
            except HotkeyConflict as err:
                field.set_error(str(err))
                problems.append(f"{LABELS[action]}: {err}")
        self.settings.set("hotkeys", chosen)
        self.ctx.settings.save()
        self.lbl_keys.config(text="\n".join(problems) or "Saved.",
                             fg=ERROR_TEXT if problems else BODY)
        return not problems

    def reset_keys(self):
        for action, field in self.fields.items():
            field.set(DEFAULT_KEYS[action])
        self.apply_keys()

    # ------------------------------------------------------------ switches
    def _autostart_changed(self):
        wanted = bool(self.var_autostart.get())
        done = self.autostart.enable() if wanted else self.autostart.disable()
        if not done:
            self.var_autostart.set(not wanted)
            self.lbl_window.config(text="Windows would not change the startup entry.")
            return
        self.lbl_window.config(text="")

    def _top_changed(self):
        on = bool(self.var_top.get())
        self.root.attributes("-topmost", on)
        self.settings.set("always_on_top", on)
        self.ctx.settings.save()

    def _min_changed(self):
        self.settings.set("start_minimised", bool(self.var_min.get()))
        self.ctx.settings.save()

    def _tray_changed(self):
        self.settings.set("close_to_tray", bool(self.var_tray.get()))
        self.ctx.settings.save()

    # ------------------------------------------------------------ updates
    def _updates_changed(self):
        self.settings.set("check_updates", bool(self.var_updates.get()))
        self.ctx.settings.save()

    def _update_status(self, text, error=False):
        if hasattr(self, "lbl_update"):
            self.lbl_update.config(text=text, fg=ERROR_TEXT if error else BODY)

    def _update_clicked(self):
        if self.release is not None:
            self.start_update()
        else:
            self.check_updates(ask=False)

    def check_updates(self, ask):
        """Look for a newer release on a worker; `ask` pops a question when one is found."""
        self._update_status("Checking for updates...")
        self.ctx.scheduler.add(UPDATE_JOB, lambda: self._check_job(ask), 0, run_now=True,
                               isolated=True, once=True)

    def _check_job(self, ask):
        try:
            release = self.updater.latest_release(self.updater.http_client())
        except (OSError, ValueError) as err:
            text = f"Could not check for updates: {err}"
            self.ctx.ui_call(lambda: self._update_status(text, error=True))
            return
        if not self.updater.is_newer(release):
            self.ctx.ui_call(lambda: self._update_status(
                f"You have the latest version ({__version__})."))
            return
        self.ctx.ui_call(lambda: self._offer_update(release, ask))

    def _offer_update(self, release, ask):
        self.release = release
        self.btn_update.set_text(f"Update to {release.tag}")
        self._update_status(f"Version {release.tag} is available (you have {__version__}).")
        self.ctx.log(f"[UPDATE] Version {release.tag} is available. System > Updates installs it.")
        if ask and messagebox.askyesno(
                "Update available", f"Macro Studio {release.tag} is available (you have "
                                    f"{__version__}).\n\nUpdate now? The studio restarts."):
            self.start_update()

    def start_update(self):
        release = self.release
        if release is None:
            return
        if not getattr(sys, "frozen", False):
            self._update_status("Running from source: get the new version with git pull.")
            return
        if self.ctx.automation.owner is not None:
            self._update_status("Stop the running macro first, then update.", error=True)
            return
        self._update_status(f"Downloading {release.tag}...")
        folder = os.path.dirname(sys.executable)

        def job():
            try:
                path = self.updater.download(self.updater.http_client(), release, folder)
            except (OSError, ValueError) as err:
                text = f"Download failed: {err}"
                self.ctx.ui_call(lambda: self._update_status(text, error=True))
                return
            self.ctx.ui_call(lambda: self._install(path, release))
        self.ctx.scheduler.add(UPDATE_JOB, job, 0, run_now=True, isolated=True, once=True)

    def _install(self, path, release):
        if self.ctx.automation.owner is not None:      # a macro started during the download
            self._update_status("Stop the running macro first, then update.", error=True)
            return
        try:
            self.updater.install(path, sys.executable)
            self.updater.restart(sys.executable)
        except OSError as err:
            self._update_status(f"Could not install the update: {err}", error=True)
            return
        self.ctx.log(f"[UPDATE] Installed {release.tag}; restarting.")
        self.shell.quit_now()

    def _pick_scale(self):
        menu = tk.Menu(self.root, tearoff=0)
        for percent in TEXT_SCALES:
            menu.add_command(label=f"{percent}%", command=lambda p=percent: self.set_text_scale(p))
        menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())

    def set_text_scale(self, percent):
        self.settings.set("text_scale", normalize_text_scale(percent))
        self.settings.set("text_scale_chosen", True)
        self.ctx.settings.save()
        self._scale_text()
        self.lbl_window.config(text="Text size changes the next time you start the studio.",
                               fg=BODY)

    def _scale_text(self):
        shown = (self.settings.get("text_scale") if self.settings.get("text_scale_chosen")
                 else ui.get_text_scale())
        self.btn_scale.set_text(f"{normalize_text_scale(shown)}%")
