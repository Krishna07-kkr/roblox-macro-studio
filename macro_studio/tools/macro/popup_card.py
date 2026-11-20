"""The "Purchase pop-up guard" card on the Settings page (spec 002, story A7).

A mixin of the macro tool: it uses the tool's `current_cfg`, `ctx`, `tab_selection` and
`_mark_dirty`, and only adds the card, the capture flow and Remove. The guard itself is in
macro_studio/core/popup_guard.py and the capture window in core/popup_capture.py.
"""

import copy
import os
import re
import threading
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox
from typing import Any

from macro_studio.core import scaling
from macro_studio.core import ui_theme as ui
from macro_studio.core.ui_theme import (
    BODY,
    CANVAS,
    CYAN,
    R_LG,
    SP_MD,
    SP_SM,
    SP_XL,
    SURFACE,
    WARNING,
    Button,
    Card,
)
from macro_studio.core.vision_scanner import VisionScanner
from macro_studio.core.window_manager import WindowManager


class PopupCardMixin:
    # What the macro tool provides to this mixin.
    ctx: Any
    root: Any
    tab_selection: Any
    current_cfg: dict
    active_profile_name: str
    _mark_dirty: Callable[[], None]
    _append_log: Callable[[str], None]

    def _build_popup_card(self, page):
        card = Card(page, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        card.pack(fill="x", padx=SP_XL, pady=(0, SP_SM))
        ui.heading(card.body, "Purchase pop-up guard", size=15).pack(anchor="w")
        ui.caption(card.body, "Before every click the macro looks for a Robux purchase pop-up and "
                              "closes it with the close button you pick. It never presses Buy.").pack(
            anchor="w", pady=(2, SP_SM))
        self.lbl_popup_status = tk.Label(card.body, text="", font=ui.F_BODY(), bg=SURFACE,
                                         fg=BODY, anchor="w", justify="left", wraplength=640)
        self.lbl_popup_status.pack(fill="x", pady=(0, SP_SM))
        row = tk.Frame(card.body, bg=SURFACE)
        row.pack(fill="x")
        self.btn_popup_capture = Button(row, "Capture popup", command=self._capture_popup,
                                        kind="secondary", height=30, page_bg=SURFACE)
        self.btn_popup_capture.pack(side="left")
        self.btn_popup_remove = Button(row, "Remove", command=self._remove_popup, kind="ghost",
                                       height=30, page_bg=SURFACE)
        self.btn_popup_remove.pack(side="left", padx=(SP_SM, 0))
        self._refresh_popup_status()

    def _popup_picture_name(self):
        """One picture per profile: the close button is stored relative to it and its client size."""
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", self.active_profile_name or "").strip("_") or "default"
        return f"purchase_popup_{slug}.png"

    def _refresh_popup_status(self):
        cfg = self.current_cfg.get("popup_guard") or {}
        name = str(cfg.get("template") or "")
        picture = bool(name) and os.path.basename(name) == name and os.path.isfile(
            os.path.join(scaling.get_resource_path("templates"), name))
        if cfg and cfg.get("enabled", True) and picture:
            text, color = "On: the macro checks for the pop-up before every click.", CYAN
        elif cfg and not picture:
            text, color = ("Off: the pop-up picture for this profile is missing. Open the purchase "
                           "pop-up in the game, then press Capture popup."), WARNING
        else:
            text, color = ("Off: nothing has been captured yet, so the macro cannot see a purchase "
                           "pop-up. Open it in the game, then press Capture popup."), WARNING
        self.lbl_popup_status.configure(text=text, fg=color)
        self.btn_popup_remove.set_state("normal" if cfg else "disabled")

    def _capture_popup(self):
        if self._popup_capturing:
            return
        self._popup_capturing = True
        self.btn_popup_capture.set_state("disabled")
        cfg = copy.deepcopy(self.current_cfg)
        # Finding and capturing the client takes a moment: never on the Tk thread.
        threading.Thread(target=self._grab_popup_frame, args=(cfg,), daemon=True).start()

    def _grab_popup_frame(self, cfg):
        frame, problem = None, None
        try:
            wm = WindowManager()
            windows = self.tab_selection.filter(wm.find_windows(), wm.get_pid)
            if not windows:
                problem = ("No Roblox client is open. Start the game, open the purchase pop-up, "
                           "and check the client is ticked in Tabs.")
            else:
                frame = VisionScanner(cfg).capture_client_full(windows[0])
                if frame is None:
                    problem = "Could not capture the game client. Is the window minimized?"
        except Exception as err:  # noqa: BLE001 - top of a worker thread: report to the UI
            problem = f"Capture failed: {err}"
        self.ctx.ui_call(lambda: self._popup_frame_ready(frame, problem))

    def _popup_frame_ready(self, frame, problem):
        self._popup_capturing = False
        self.btn_popup_capture.set_state("normal")
        if frame is None:
            messagebox.showwarning("Capture popup", problem or "Nothing was captured.")
            return
        from macro_studio.core.popup_capture import PopupCaptureDialog
        PopupCaptureDialog(self.root, frame, scaling.get_base_dir(), self._popup_picture_name(),
                           self._popup_saved)

    def _popup_saved(self, settings):
        self.current_cfg["popup_guard"] = settings
        self._mark_dirty()
        self._refresh_popup_status()
        self._append_log("[POPUP] Pop-up picture and close button saved. Save the profile to keep it.")

    def _remove_popup(self):
        if not self.current_cfg.get("popup_guard"):
            return
        if not messagebox.askyesno(
                "Remove pop-up guard",
                "Stop checking for the purchase pop-up?\n\nThe macro will then click without "
                "looking for it."):
            return
        self.current_cfg.pop("popup_guard", None)
        self._mark_dirty()
        self._refresh_popup_status()
