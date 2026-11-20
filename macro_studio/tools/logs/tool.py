"""The Logs page: what the studio has been doing, with filter, copy, export (spec 001, US7).

Every `ctx.log` line from every tool arrives over the event bus into a capped buffer (the newest
2,000 lines). The page redraws twice a second, and only when something new arrived, so a burst
of messages never floods the interface. The macro's own console on the Overview page is
separate and keeps its own detail.
"""

import os
import time
import tkinter as tk
from tkinter import messagebox

from macro_studio.core import ui_theme as ui
from macro_studio.core.fileio import rotate_log, write_text_atomic
from macro_studio.core.paths import get_base_dir
from macro_studio.core.ui_theme import (
    CANVAS,
    INK,
    SP_MD,
    SP_SM,
    SP_XL,
    SURFACE_2,
    Button,
    Toggle,
)
from macro_studio.tools.logs.buffer import LogBuffer

REFRESH_MS = 500
FILE_NAME = "studio_log.txt"
FILE_MAX_BYTES = 2 * 1024 * 1024
FILE_CHECK_EVERY = 100


class LogsTool:
    page_titles = ["Logs"]
    DEFAULTS = {"game_only": False, "to_file": False}

    def __init__(self, shell, ctx):
        self.shell, self.ctx, self.root = shell, ctx, shell.root
        self.settings = ctx.settings.view("logs", self.DEFAULTS)
        self.buffer = LogBuffer()
        self._shown = (-1, None)
        self._after = None
        self._unsubscribe = None
        self._written = 0
        self.file_path = os.path.join(get_base_dir(), FILE_NAME)

    # ------------------------------------------------------------ lifecycle
    def start(self):
        self._unsubscribe = self.ctx.events.subscribe("log", self._on_log)
        self._tick()

    def stop(self):
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        if self._after is not None:
            self.root.after_cancel(self._after)
            self._after = None

    def _on_log(self, message, **_extra):
        """Runs on whichever thread logged: only touches the thread-safe buffer and the file."""
        self.buffer.add(message)
        if self.settings.get("to_file"):
            self._write_line(self.buffer.lines()[-1])

    def _write_line(self, line):
        self._written += 1
        try:
            if self._written % FILE_CHECK_EVERY == 1:
                rotate_log(self.file_path, FILE_MAX_BYTES)
            with open(self.file_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            self.settings.set("to_file", False)           # stop trying; the page says so
            self._note = "The log file could not be written, so file logging was turned off."

    # ------------------------------------------------------------ page
    def build_pages(self, frames):
        (page,) = frames
        bar = tk.Frame(page, bg=CANVAS)
        bar.pack(fill="x", padx=SP_XL, pady=(SP_XL, SP_SM))
        self.var_game = tk.BooleanVar(value=bool(self.settings.get("game_only")))
        self.var_file = tk.BooleanVar(value=bool(self.settings.get("to_file")))
        for text, var, command in (("Game activity only", self.var_game, self._filter_changed),
                                   ("Also write to a file", self.var_file, self._file_changed)):
            tk.Label(bar, text=text, font=ui.F_BODY(), bg=CANVAS, fg=INK).pack(side="left")
            Toggle(bar, var, page_bg=CANVAS, command=command).pack(side="left", padx=(SP_SM, SP_MD))
        Button(bar, "Open folder", command=self.open_folder, kind="ghost", height=30,
               page_bg=CANVAS).pack(side="right")
        Button(bar, "Export", command=self.export, kind="secondary", height=30,
               page_bg=CANVAS).pack(side="right", padx=(0, SP_SM))
        Button(bar, "Copy", command=self.copy, kind="secondary", height=30,
               page_bg=CANVAS).pack(side="right", padx=(0, SP_SM))
        Button(bar, "Clear", command=self.clear, kind="secondary", height=30,
               page_bg=CANVAS).pack(side="right", padx=(0, SP_SM))
        self.lbl_note = tk.Label(page, text="", font=ui.F_CAPTION(), bg=CANVAS, fg=INK, anchor="w")
        self.lbl_note.pack(fill="x", padx=SP_XL)
        holder = tk.Frame(page, bg=SURFACE_2)
        holder.pack(fill="both", expand=True, padx=SP_XL, pady=(SP_SM, SP_XL))
        self.text = tk.Text(holder, bg=SURFACE_2, fg=INK, font=ui.F_CODE(), relief="flat", bd=0,
                            wrap="word", state="disabled", padx=SP_MD, pady=SP_SM,
                            highlightthickness=0)
        scroll = tk.Scrollbar(holder, command=self.text.yview)
        self.text.config(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self._note = ""
        self.refresh()

    # ------------------------------------------------------------ drawing
    def _tick(self):
        self.refresh()
        self._after = self.root.after(REFRESH_MS, self._tick)

    def refresh(self):
        """Redraw only if something was added or the filter changed."""
        if not hasattr(self, "text"):
            return
        key = (self.buffer.version, bool(self.var_game.get()))
        if getattr(self, "_note", ""):
            self.lbl_note.config(text=self._note)
            self._note = ""
        if key == self._shown:
            return
        self._shown = key
        at_bottom = self.text.yview()[1] >= 0.999
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", self.buffer.text(game_only=key[1]))
        self.text.config(state="disabled")
        if at_bottom:
            self.text.see("end")

    def _filter_changed(self):
        self.settings.set("game_only", bool(self.var_game.get()))
        self.ctx.settings.save()
        self.refresh()

    def _file_changed(self):
        self.settings.set("to_file", bool(self.var_file.get()))
        self.ctx.settings.save()
        self.lbl_note.config(text=f"Writing to {self.file_path}" if self.var_file.get() else "")

    # ------------------------------------------------------------ actions
    def _visible_text(self):
        return self.buffer.text(game_only=bool(self.var_game.get()))

    def clear(self):
        self.buffer.clear()
        self.refresh()

    def copy(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self._visible_text())
        self.lbl_note.config(text="Copied.")

    def export(self):
        path = os.path.join(get_base_dir(), "exports",
                            f"studio_log_{time.strftime('%Y%m%d_%H%M%S')}.txt")
        try:
            write_text_atomic(path, self._visible_text() + "\n")
        except OSError as err:
            messagebox.showerror("Export", f"Could not save the log: {err}")
            return None
        self.lbl_note.config(text=f"Saved to {path}")
        return path

    def open_folder(self):
        os.startfile(get_base_dir())                       # noqa: S606 - opens a folder, no command
