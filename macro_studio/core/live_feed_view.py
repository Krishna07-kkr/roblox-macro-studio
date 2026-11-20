"""Tk widget that shows LiveFeed frames (Constitution VI). Tk thread only."""

import tkinter as tk

from PIL import Image, ImageDraw, ImageFont, ImageTk

from macro_studio.core import ui_theme as ui
from macro_studio.core.ui_theme import HAIRLINE, INK, MUTE, SURFACE, SURFACE_2


class LiveFeedView(tk.Frame):
    """A live image of the game client with a one-line caption under it."""

    def __init__(self, parent, size=(640, 480), bg=SURFACE):
        super().__init__(parent, bg=bg)
        self.size = size
        self._photo = self._placeholder("Looking for a Roblox client...")
        self.image_label = tk.Label(self, image=self._photo, bg=SURFACE_2, bd=0)
        self.image_label.pack(anchor="w")
        self.caption = tk.Label(self, text="", font=ui.F_CAPTION(), bg=bg, fg=MUTE,
                                anchor="w", justify="left")
        self.caption.pack(fill="x", pady=(4, 0))

    def _placeholder(self, text):
        w, h = self.size
        img = Image.new("RGB", (w, h), SURFACE_2)
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, w - 1, h - 1], outline=HAIRLINE, width=1)
        font = ImageFont.load_default(size=max(12, round(16 * ui.get_text_scale() / 100)))
        box = draw.textbbox((0, 0), text, font=font)
        draw.text(((w - (box[2] - box[0])) // 2, (h - (box[3] - box[1])) // 2), text,
                  fill=MUTE, font=font)
        return ImageTk.PhotoImage(img)

    def show(self, image, caption):
        """Display one frame; `image` None shows the 'no client' placeholder."""
        if image is None:
            self._photo = self._placeholder("Nothing to show")
            self.caption.config(text=caption, fg=MUTE)
        else:
            self._photo = ImageTk.PhotoImage(image)
            self.caption.config(text=caption, fg=INK)
        self.image_label.config(image=self._photo)
