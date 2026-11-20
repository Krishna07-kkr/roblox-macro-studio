"""Tabs dropdown: pick which Roblox clients the macro works on, by their live screens.

A popover under the Overview "Tabs" button. Each open client is a card showing what is
on its screen right now (refreshed every second on the shared scheduler, never on the
Tk thread), its window title and PID. Click a card, or focus it and press Space, to
tick or untick it; Shift+click ticks a range, Ctrl+A ticks all. Ticked tabs have a blue
outline; unticked ones are greyed out. Right-click a card for actions (bring to front, trim
memory, close, copy PID, and selection shortcuts). With many tabs the list scrolls (mouse
wheel or the thin bar at its right edge).
Changes go straight into the shared TabSelection, so a running Goliath macro picks
them up on its next pass.
"""

import contextlib
import tkinter as tk
from tkinter import messagebox

from PIL import Image, ImageDraw, ImageEnhance, ImageTk

from macro_studio.core import ui_theme as ui
from macro_studio.core.ui_theme import (
    BODY,
    HAIRLINE,
    HAIRLINE_STRONG,
    INK,
    LINK,
    MUTE,
    SP_MD,
    SP_SM,
    SP_XS,
    SURFACE,
    SURFACE_2,
    SURFACE_3,
    Button,
)

THUMB = (192, 144)
OUTLINE = 3
MAX_VIEW_H = 520          # tallest the card list gets before it scrolls
MIN_VIEW_H = 160
SCROLL_STEP = 48          # pixels per wheel notch
SHIFT_BIT = 0x0001


def card_image(image, selected, size=THUMB, minimized=False):
    """The thumbnail as shown: blue outline when ticked, greyed out when not.

    A minimized client shows its last picture, dimmed, with a "Minimized" band: it cannot be
    captured or clicked until its window is restored.
    """
    w, h = size
    if image is None:
        base = Image.new("RGB", size, SURFACE_2)
        d = ImageDraw.Draw(base)
        text = "No picture"
        box = d.textbbox((0, 0), text)
        d.text(((w - box[2]) // 2, (h - box[3]) // 2), text, fill=MUTE)
    else:
        base = image.convert("RGB").resize(size)
        if not selected:
            base = ImageEnhance.Brightness(ImageEnhance.Color(base).enhance(0.0)).enhance(0.55)
    d = ImageDraw.Draw(base)
    if minimized:
        base = ImageEnhance.Brightness(base).enhance(0.6)
        d = ImageDraw.Draw(base)
        d.rectangle([0, h - 22, w, h], fill=SURFACE_2)
        d.text((8, h - 18), "Minimized", fill=INK)
    if selected:
        d.rectangle([0, 0, w - 1, h - 1], outline=LINK, width=OUTLINE)
    else:
        d.rectangle([0, 0, w - 1, h - 1], outline=HAIRLINE, width=1)
    return base


class TabPicker(tk.Toplevel):
    REFRESH_S = 1.0
    JOB = "tab_picker"

    def __init__(self, root, anchor, selection, snapshot, scheduler, ui_call,
                 on_change=None, minimized_count=None, actions=None, confirm=None):
        """`actions` has run(name, tabs) for "focus", "trim" and "close" (off the Tk thread);
        without it those menu items are disabled. `confirm(title, text)` asks before a close."""
        super().__init__(root, bg=HAIRLINE)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self._anchor_widget = anchor
        self._actions = actions
        self._confirm = confirm or (lambda title, text: messagebox.askyesno(title, text,
                                                                           parent=self))
        self._menu = None
        self.anchor_pid = None      # where Shift+click ranges start
        self._view_h = self._content_h = 0
        self.selection = selection
        self._snapshot = snapshot
        self._scheduler = scheduler
        self._ui_call = ui_call
        self._on_change = on_change or (lambda: None)
        self._minimized_count = minimized_count or (lambda: 0)
        self.tabs = []
        self._minimized = 0
        self._cards = {}          # hwnd -> (frame, image label, photo holder)
        self.closed = False

        panel = tk.Frame(self, bg=SURFACE, padx=SP_MD, pady=SP_MD)
        panel.pack(padx=1, pady=1)
        head = tk.Frame(panel, bg=SURFACE)
        head.pack(fill="x", pady=(0, SP_SM))
        tk.Label(head, text="Work on these tabs", font=ui.F_BODY_STRONG(), bg=SURFACE,
                 fg=INK).pack(side="left")
        Button(head, "Select all", command=self.select_all, kind="ghost", height=26,
               page_bg=SURFACE, font=ui.F_CAPTION()).pack(side="right")
        body = tk.Frame(panel, bg=SURFACE)
        body.pack(fill="x")
        self.canvas = tk.Canvas(body, bg=SURFACE, highlightthickness=0, bd=0, width=1,
                                height=1, yscrollincrement=SCROLL_STEP,
                                yscrollcommand=self._draw_thumb)
        self.canvas.pack(side="left")
        self.track = tk.Canvas(body, width=6, bg=SURFACE, highlightthickness=0, bd=0,
                               cursor="hand2")
        self.track.bind("<Button-1>", lambda e: self._drag_thumb(e.y))
        self.track.bind("<B1-Motion>", lambda e: self._drag_thumb(e.y))
        self.grid_frame = tk.Frame(self.canvas, bg=SURFACE)
        self.canvas.create_window(0, 0, window=self.grid_frame, anchor="nw")
        tk.Label(panel, text="Click to tick. Shift+click ticks a range. Right-click for actions.",
                 font=ui.F_CAPTION(), bg=SURFACE, fg=MUTE, anchor="w").pack(
            fill="x", pady=(SP_XS, 0))
        self.status = tk.Label(panel, text="Looking for Roblox clients...",
                               font=ui.F_CAPTION(), bg=SURFACE, fg=MUTE, anchor="w",
                               justify="left", wraplength=2 * THUMB[0] + SP_MD)
        self.status.pack(fill="x", pady=(SP_SM, 0))

        self._place_under(anchor)
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<MouseWheel>", self.on_wheel)
        self.bind("<Control-a>", lambda e: self.on_select_all_key())
        # Clicks outside the popover close it, like any dropdown.
        self.bind("<ButtonPress-1>", self._maybe_close, add="+")
        self.wait_visibility()      # a grab needs a mapped window
        self.grab_set()
        self.focus_set()
        self._scheduler.add(self.JOB, self._tick, interval_s=self.REFRESH_S, run_now=True,
                            isolated=True)

    # ------------------------------------------------------------ placement / closing
    def _place_under(self, anchor):
        anchor.update_idletasks()
        self.geometry(f"+{anchor.winfo_rootx()}+{anchor.winfo_rooty() + anchor.winfo_height() + 4}")

    def _maybe_close(self, event):
        x, y = event.x_root, event.y_root
        inside = (self.winfo_rootx() <= x < self.winfo_rootx() + self.winfo_width()
                  and self.winfo_rooty() <= y < self.winfo_rooty() + self.winfo_height())
        if not inside:
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._scheduler.remove(self.JOB)
        self._drop_menu()
        try:
            self.grab_release()
        finally:
            self.destroy()

    # ------------------------------------------------------------ refresh
    def _tick(self):
        """Scheduler thread: capture every client, hand the result to Tk."""
        if self.closed:
            return
        tabs = self._snapshot()
        minimized = self._minimized_count()
        self._ui_call(lambda: self.show_tabs(tabs, minimized))

    def show_tabs(self, tabs, minimized=0):
        """Tk thread: rebuild the cards if the client list changed, else repaint them."""
        if self.closed or not self.winfo_exists():
            return
        self.tabs = list(tabs)
        rebuilt = [t.hwnd for t in self.tabs] != list(self._cards)
        if rebuilt:
            self._rebuild()
        listed = sum(1 for t in self.tabs if getattr(t, "minimized", False))
        self._minimized = listed or minimized
        self._repaint()
        if rebuilt:
            self._fit()           # after the pictures are set, so the sizes are real
        self._update_status()

    def _rebuild(self):
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self._cards = {}
        if self.anchor_pid not in [t.pid for t in self.tabs]:
            self.anchor_pid = None
        cols = 3 if len(self.tabs) > 4 else 2
        for i, tab in enumerate(self.tabs):
            card = tk.Frame(self.grid_frame, bg=SURFACE, takefocus=1,
                            highlightthickness=2, highlightbackground=SURFACE,
                            highlightcolor=INK, cursor="hand2")   # blue = ticked
            card.grid(row=i // cols, column=i % cols, padx=(0, SP_SM), pady=(0, SP_SM),
                      sticky="nw")
            img = tk.Label(card, bg=SURFACE, bd=0)
            img.pack()
            title = tk.Label(card, text=tab.title[:28], font=ui.F_BODY(), bg=SURFACE,
                             fg=INK, anchor="w")
            title.pack(fill="x", pady=(SP_XS, 0))
            meta = tk.Label(card, text=f"PID {tab.pid}   window {tab.hwnd}",
                            font=ui.F_CAPTION(), bg=SURFACE, fg=MUTE, anchor="w")
            meta.pack(fill="x")
            for w in (card, img, title, meta):
                w.bind("<Button-1>",
                       lambda e, p=tab.pid: self.click_tab(p, shift=bool(int(e.state) & SHIFT_BIT)))
                w.bind("<Button-3>", lambda e, p=tab.pid: self._popup_menu(e.x_root, e.y_root, p))
            card.bind("<space>", lambda e, p=tab.pid: self.click_tab(p))
            card.bind("<Return>", lambda e, p=tab.pid: self.click_tab(p))
            card.bind("<Shift-F10>", lambda e, c=card, p=tab.pid: self._popup_at(c, p))
            self._cards[tab.hwnd] = (card, img, title)

    def _repaint(self):
        for tab in self.tabs:
            _card, img, title = self._cards[tab.hwnd]
            selected = self.selection.includes(tab.pid)
            photo = ImageTk.PhotoImage(card_image(tab.image, selected,
                                                  minimized=getattr(tab, "minimized", False)))
            img.configure(image=photo)
            img.image = photo
            title.configure(fg=INK if selected else BODY)

    def _update_status(self):
        minimized = self._minimized
        open_count = len(self.tabs)
        if not open_count:
            text = "No Roblox client is open. Start the game, and its tab shows up here."
        else:
            text = (f"{self.selection.describe(open_count)}. Goliath switches on its next "
                    f"pass; Mutation reads tabs when it starts.")
        if minimized:
            noun = "client is" if minimized == 1 else "clients are"
            text += (f"\n{minimized} minimized {noun} listed too, but the macro cannot click a "
                     f"minimized window. Right-click > Restore.")
        self.status.configure(text=text)

    # ------------------------------------------------------------ scrolling
    def _screen_room(self):
        """How tall the card list may be so the popover stays on screen."""
        below = self.winfo_screenheight() - (self._anchor_widget.winfo_rooty()
                                             + self._anchor_widget.winfo_height())
        return max(MIN_VIEW_H, min(MAX_VIEW_H, below - 170))

    def _fit(self):
        """Size the visible window to the cards (up to the cap) and show the bar if needed."""
        self.update_idletasks()
        width, content = self.grid_frame.winfo_reqwidth(), self.grid_frame.winfo_reqheight()
        view = min(content, self._screen_room())
        keep = self.canvas.yview()[0]
        self.canvas.configure(width=width, height=view, scrollregion=(0, 0, width, content))
        self._view_h, self._content_h = view, content
        if content > view:
            self.track.configure(height=view)
            self.track.pack(side="left", padx=(SP_XS, 0), fill="y")
        else:
            self.track.pack_forget()
        self.canvas.yview_moveto(keep if content > view else 0.0)
        self._draw_thumb(*self.canvas.yview())

    def view_height(self):
        return self._view_h

    def content_height(self):
        return self._content_h

    def on_wheel(self, event):
        """Mouse wheel: one notch moves one step; nothing happens when all cards are visible."""
        if self._content_h <= self._view_h:
            return "break"
        notches = -int(event.delta / 120) or (-1 if event.delta > 0 else 1)
        self.canvas.yview_scroll(notches, "units")
        return "break"

    def _draw_thumb(self, first, last):
        self.track.delete("all")
        if self._content_h <= self._view_h:
            return
        first, last = float(first), float(last)
        self.track.create_rectangle(0, first * self._view_h, 6, last * self._view_h,
                                    fill=HAIRLINE_STRONG, outline="")

    def _drag_thumb(self, y):
        first, last = self.canvas.yview()
        self.canvas.yview_moveto(y / max(1, self._view_h) - (last - first) / 2)

    # ------------------------------------------------------------ choosing tabs
    def toggle(self, pid):
        self.selection.toggle(pid, [t.pid for t in self.tabs])
        self._after_change()

    def click_tab(self, pid, shift=False):
        """Click: tick or untick one tab. Shift+click: tick the range from the last click."""
        open_pids = [t.pid for t in self.tabs]
        if shift and self.anchor_pid in open_pids:
            self.selection.select_only(
                self.selection.range_pids(open_pids, pid, self.anchor_pid), open_pids)
            self._after_change()
        else:
            self.anchor_pid = pid
            self.toggle(pid)

    def select_all(self):
        self.selection.set_all()
        self._after_change()

    def on_select_all_key(self):
        self.select_all()
        return "break"

    def select_none(self):
        self.selection.select_only([], [t.pid for t in self.tabs])
        self._after_change()

    def invert_selection(self):
        self.selection.invert([t.pid for t in self.tabs])
        self._after_change()

    def select_only(self, tabs):
        self.selection.select_only([t.pid for t in tabs], [t.pid for t in self.tabs])
        self._after_change()

    # ------------------------------------------------------------ right-click menu
    def targets_for(self, pid):
        """The tabs a right-click on `pid` acts on.

        If the user chose a group of tabs and right-clicks one of them, the whole group;
        otherwise (including the default "all tabs") just the clicked one, so a stray
        right-click never closes every client.
        """
        if not self.selection.all_tabs and self.selection.includes(pid):
            return [t for t in self.tabs if self.selection.includes(t.pid)]
        return [t for t in self.tabs if t.pid == pid]

    def _drop_menu(self):
        if self._menu is not None:
            with contextlib.suppress(tk.TclError):
                self._menu.destroy()
            self._menu = None

    def build_menu(self, pid):
        self._drop_menu()
        targets = self.targets_for(pid)
        count = len(targets)
        one = count == 1
        menu = tk.Menu(self, tearoff=0, bg=SURFACE_2, fg=INK, activebackground=SURFACE_3,
                       activeforeground=INK, disabledforeground=MUTE, bd=0, relief="flat",
                       font=ui.F_BODY())
        self._menu = menu
        head = f"{targets[0].title[:28]}  (PID {targets[0].pid})" if one else f"{count} tabs"
        menu.add_command(label=head, state="disabled")
        menu.add_separator()
        wired = "normal" if self._actions is not None and targets else "disabled"
        menu.add_command(label="Bring to front", state=wired if one else "disabled",
                         command=lambda: self._act("focus", targets))
        menu.add_command(label="Restore window" if one else "Restore windows", state=wired,
                         command=lambda: self._act("restore", targets))
        menu.add_command(label="Minimize", state=wired,
                         command=lambda: self._act("minimize", targets))
        menu.add_command(label="Trim memory", state=wired,
                         command=lambda: self._act("trim", targets))
        menu.add_command(label="Close client..." if one else f"Close {count} clients...",
                         state=wired, command=lambda: self._act("close", targets))
        menu.add_command(label="Copy PID" if one else "Copy PIDs",
                         command=lambda: self._copy_pids(targets))
        menu.add_separator()
        menu.add_command(label="Only this tab" if one else f"Only these {count} tabs",
                         command=lambda: self.select_only(targets))
        menu.add_command(label="Select all", command=self.select_all)
        menu.add_command(label="Select none", command=self.select_none)
        menu.add_command(label="Invert selection", command=self.invert_selection)
        return menu

    def _popup_menu(self, x, y, pid):
        menu = self.build_menu(pid)
        menu.tk_popup(x, y)

    def _popup_at(self, widget, pid):
        self._popup_menu(widget.winfo_rootx() + 20, widget.winfo_rooty() + 20, pid)

    def _act(self, name, targets):
        if not targets or self._actions is None:
            return
        if name == "close":
            shown = "\n".join(f"{t.title[:40]} (PID {t.pid})" for t in targets[:8])
            if len(targets) > 8:
                shown += f"\n...and {len(targets) - 8} more"
            noun = "this Roblox client" if len(targets) == 1 else f"these {len(targets)} Roblox clients"
            if not self._confirm("Close clients",
                                 f"Close {noun}?\n\n{shown}\n\nThe game closes without saving."):
                return
        self._actions.run(name, targets)

    def _copy_pids(self, targets):
        self.clipboard_clear()
        self.clipboard_append(" ".join(str(t.pid) for t in targets))

    def _after_change(self):
        self._repaint()
        self._update_status()
        self._on_change()
