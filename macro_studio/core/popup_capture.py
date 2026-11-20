"""The "Capture popup" window: teach the guard what the Robux purchase popup looks like.

The player opens the popup in the game, then in two steps on a still picture of the client:
1. drags a box around the whole popup,
2. clicks its close button (the X or Cancel, never Buy).
Saving writes the popup picture and hands back the `popup_guard` settings for the profile
(see core/popup_guard.py). Nothing here touches the game: it only looks at a captured frame.
"""

import tkinter as tk

import cv2
from PIL import Image, ImageTk

from macro_studio.core import ui_theme as ui
from macro_studio.core.popup_guard import MIN_TEMPLATE_SIDE, save_template
from macro_studio.core.ui_theme import (
    CANVAS,
    ERROR_TEXT,
    INK,
    LINK,
    MUTE,
    SP_MD,
    SP_SM,
    SURFACE,
    Button,
)

MAX_VIEW = (900, 640)
MIN_DRAG = 8          # a click shorter than this (screen pixels) is not a box

STEP_BOX = "Step 1 of 2: drag a box around the whole pop-up."
STEP_CLOSE = ("Step 2 of 2: click its close button (the X or Cancel). "
              "Never the Buy button.")
STEP_DONE = "Check the red box and the cross, then press Save."


class PopupCaptureDialog(tk.Toplevel):
    def __init__(self, root, frame, base_dir, name, on_saved):
        """
        frame    BGR picture of the client, with the popup open
        base_dir the data folder; the picture goes in `templates/` inside it
        name     picture file name for this profile
        on_saved called with the `popup_guard` settings after a successful save
        """
        super().__init__(root, bg=CANVAS)
        self.title("Capture purchase pop-up")
        self.transient(root)
        self.resizable(False, False)
        self._frame, self._base_dir, self._name, self._on_saved = frame, base_dir, name, on_saved

        height, width = frame.shape[:2]
        self._ds = min(1.0, MAX_VIEW[0] / width, MAX_VIEW[1] / height)   # display scale
        view = (max(1, round(width * self._ds)), max(1, round(height * self._ds)))

        self.box = None          # (x, y, w, h) in frame pixels
        self.close = None        # (x, y) in frame pixels
        self._drag_start = None
        self._rect_id = None
        self._mark_ids = []

        body = tk.Frame(self, bg=SURFACE, padx=SP_MD, pady=SP_MD)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        self.lbl_step = tk.Label(body, text=STEP_BOX, font=ui.F_BODY_STRONG(), bg=SURFACE, fg=INK,
                                 anchor="w")
        self.lbl_step.pack(fill="x")
        tk.Label(body, text="Open the purchase pop-up in the game first. This is only a picture; "
                            "nothing is clicked in the game.", font=ui.F_CAPTION(), bg=SURFACE,
                 fg=MUTE, anchor="w", justify="left").pack(fill="x", pady=(0, SP_SM))

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        if self._ds != 1.0:
            image = image.resize(view, Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(image)
        self.canvas = tk.Canvas(body, width=view[0], height=view[1], bg=CANVAS,
                                highlightthickness=1, highlightbackground=MUTE,
                                cursor="crosshair")
        self.canvas.pack()
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw")
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)

        self.lbl_error = tk.Label(body, text="", font=ui.F_CAPTION(), bg=SURFACE, fg=ERROR_TEXT,
                                  anchor="w", justify="left", wraplength=view[0])
        self.lbl_error.pack(fill="x", pady=(SP_SM, 0))

        row = tk.Frame(body, bg=SURFACE)
        row.pack(fill="x", pady=(SP_SM, 0))
        self.btn_save = Button(row, "Save", command=self.save, kind="primary", height=32,
                               page_bg=SURFACE)
        self.btn_save.pack(side="right")
        self.btn_save.set_state("disabled")
        Button(row, "Cancel", command=self.destroy, kind="ghost", height=32,
               page_bg=SURFACE).pack(side="right", padx=(0, SP_SM))
        Button(row, "Start over", command=self.reset, kind="secondary", height=32,
               page_bg=SURFACE).pack(side="left")
        self.bind("<Escape>", lambda _event: self.destroy())

    # ------------------------------------------------------------ helpers
    def _to_frame(self, x, y):
        height, width = self._frame.shape[:2]
        return (min(width - 1, max(0, round(x / self._ds))),
                min(height - 1, max(0, round(y / self._ds))))

    def _error(self, text=""):
        self.lbl_error.configure(text=text)

    def _step(self, text):
        self.lbl_step.configure(text=text)

    def reset(self):
        self.box = self.close = self._drag_start = None
        for item in [self._rect_id, *self._mark_ids]:
            if item is not None:
                self.canvas.delete(item)
        self._rect_id, self._mark_ids = None, []
        self.btn_save.set_state("disabled")
        self._error()
        self._step(STEP_BOX)

    # ------------------------------------------------------------ mouse
    def _press(self, event):
        if self.box is None:
            self._drag_start = (event.x, event.y)
            self._error()

    def _drag(self, event):
        if self.box is not None or self._drag_start is None:
            return
        x0, y0 = self._drag_start
        if self._rect_id is None:
            self._rect_id = self.canvas.create_rectangle(x0, y0, event.x, event.y, outline=ERROR_TEXT,
                                                         width=2)
        else:
            self.canvas.coords(self._rect_id, x0, y0, event.x, event.y)

    def _release(self, event):
        if self.box is None:
            self._finish_box(event)
        else:
            self._pick_close(event)

    def _finish_box(self, event):
        if self._drag_start is None:
            return
        x0, y0 = self._drag_start
        self._drag_start = None
        if abs(event.x - x0) < MIN_DRAG or abs(event.y - y0) < MIN_DRAG:
            if self._rect_id is not None:
                self.canvas.delete(self._rect_id)
                self._rect_id = None
            self._error("Drag from one corner of the pop-up to the opposite corner.")
            return
        fx0, fy0 = self._to_frame(min(x0, event.x), min(y0, event.y))
        fx1, fy1 = self._to_frame(max(x0, event.x), max(y0, event.y))
        if (fx1 - fx0) < MIN_TEMPLATE_SIDE or (fy1 - fy0) < MIN_TEMPLATE_SIDE:
            self.reset()
            self._error("That box is too small to recognise the pop-up. Draw it around the "
                        "whole pop-up.")
            return
        self.box = (fx0, fy0, fx1 - fx0, fy1 - fy0)
        shape = (fx0 * self._ds, fy0 * self._ds, fx1 * self._ds, fy1 * self._ds)
        if self._rect_id is None:
            self._rect_id = self.canvas.create_rectangle(*shape, outline=ERROR_TEXT, width=2)
        else:
            self.canvas.coords(self._rect_id, *shape)
        self._step(STEP_CLOSE)

    def _pick_close(self, event):
        x, y = self._to_frame(event.x, event.y)
        if self.box is None:
            return
        bx, by, bw, bh = self.box
        if not (bx <= x < bx + bw and by <= y < by + bh):
            self._error("The close button must be inside the box. Click it on the pop-up.")
            return
        for item in self._mark_ids:
            self.canvas.delete(item)
        cx, cy, arm = x * self._ds, y * self._ds, 8
        self._mark_ids = [
            self.canvas.create_line(cx - arm, cy - arm, cx + arm, cy + arm, fill=LINK, width=3),
            self.canvas.create_line(cx - arm, cy + arm, cx + arm, cy - arm, fill=LINK, width=3),
        ]
        self.close = (x, y)
        self._error()
        self._step(STEP_DONE)
        self.btn_save.set_state("normal")

    # ------------------------------------------------------------ save
    def save(self):
        if self.box is None or self.close is None:
            return
        try:
            settings = save_template(self._base_dir, self._frame, self.box, self.close,
                                     name=self._name)
        except (ValueError, OSError) as err:
            self._error(str(err))
            return
        self.destroy()
        self._on_saved(settings)

