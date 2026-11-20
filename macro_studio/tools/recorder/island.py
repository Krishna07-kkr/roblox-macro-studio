"""The Recorder island: a small black pill that floats above the selected Roblox window.

It shows what the Recorder is doing (ready, recording, playing). Hover it, or click it, and it
grows into a panel with the saved recordings and Record, Play and Stop. The motion follows
Apple's Dynamic Island (a spring with a short response, no hard stops, interruptible), the look
is plain black with a hairline edge. Using a button here brings that Roblox window to the front
first (the tool does that), and the island never takes focus itself, so the game keeps it.

Placement and the spring are pure functions, tested without any window. Tk only on the Tk
thread; the one Win32 call made here (a window rectangle) is cheap and is not an enumeration.
"""

import contextlib
import ctypes
import tkinter as tk
from ctypes import wintypes

from PIL import Image, ImageDraw, ImageTk

from macro_studio.core import ui_theme as ui
from macro_studio.core.motion import Spring

KEY = "#010203"                  # painted transparent, so the pill's corners are see-through
ISLAND = "#000000"
EDGE = "#2c2c2e"
LIST_BG = "#1c1c1e"
TEXT = "#ffffff"
TEXT_2 = "#98989f"
DOT = {"idle": "#98989f", "ready": "#30d158", "recording": "#ff453a", "playing": "#0a84ff",
       "error": "#ff9f0a"}
PILL = (200, 38)                 # at 100% text size
PANEL = (380, 250)
GAP = 8
FRAME_MS = 16
FOLLOW_MS = 200
COLLAPSE_DELAY_MS = 450
SS = 3                           # supersampling for smooth edges

_GWL_EXSTYLE = -20
_WS_EX_NOACTIVATE, _WS_EX_TOOLWINDOW = 0x08000000, 0x00000080
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.GetSystemMetrics.argtypes = [ctypes.c_int]
_user32.GetSystemMetrics.restype = ctypes.c_int


# ------------------------------------------------------------------ pure helpers
def island_position(win, size, screen, gap=GAP, origin=(0, 0)):
    """Where the island's top-left goes next to a window.

    win = (left, top, width, height); size = (width, height) of the island; screen = (width,
    height) of the desktop and origin its top-left (negative with a monitor to the left).
    Tries above the window (centred), then its right, then its left, then inside its top edge.
    Returns (x, y, where).
    """
    left, top, width, _height = win
    w, h = size
    sw, sh = screen
    ox, oy = origin

    def clamp_x(x):
        return min(max(x, ox), ox + sw - w)

    def clamp_y(y):
        return min(max(y, oy), oy + sh - h)

    y = top - h - gap
    if y >= oy:
        return clamp_x(left + (width - w) // 2), y, "above"
    x = left + width + gap
    if x + w <= ox + sw:
        return x, clamp_y(top), "right"
    x = left - w - gap
    if x >= ox:
        return x, clamp_y(top), "left"
    return clamp_x(left + (width - w) // 2), clamp_y(top + gap), "inside"


def screen_bounds():
    """(x, y, width, height) of the whole desktop, all monitors together."""
    return (_user32.GetSystemMetrics(76), _user32.GetSystemMetrics(77),
            _user32.GetSystemMetrics(78), _user32.GetSystemMetrics(79))


def make_no_activate(widget):
    """Clicks on this window never take focus from the game (and it stays off the taskbar)."""
    widget.update_idletasks()
    hwnd = _user32.GetAncestor(widget.winfo_id(), 2) or widget.winfo_id()      # GA_ROOT
    style = _user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE)
    _user32.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE, style | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW)
    return hwnd


def window_style(widget):
    """The extended style of a Tk window (used by tests)."""
    hwnd = _user32.GetAncestor(widget.winfo_id(), 2) or widget.winfo_id()
    return _user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE)


def _pill_image(width, height, radius):
    big = Image.new("RGB", (width * SS, height * SS), KEY)
    ImageDraw.Draw(big).rounded_rectangle(
        [0, 0, width * SS - 1, height * SS - 1], radius=min(radius, height // 2, width // 2) * SS,
        fill=ISLAND, outline=EDGE, width=SS)
    return ImageTk.PhotoImage(big.resize((width, height), Image.Resampling.LANCZOS))


# ------------------------------------------------------------------ the island
class RecorderIsland:
    def __init__(self, root, rect_of, callbacks, animate=True, screen=screen_bounds,
                 scale=None):
        """rect_of(hwnd) -> (left, top, width, height) of the Roblox window, or None when it
        is gone or minimized. callbacks: on_record, on_play, on_stop, on_select(name), on_hide (optional)."""
        self._rect_of, self._cb, self._animate = rect_of, callbacks, animate
        self._screen = screen
        factor = (scale if scale is not None else ui.get_text_scale()) / 100.0
        self.pill = (round(PILL[0] * factor), round(PILL[1] * factor))
        self.panel = (round(PANEL[0] * factor), round(PANEL[1] * factor))
        self.hwnd = None
        self.mode, self.text = "idle", "Recorder"
        self.names, self.selected = [], None
        self.expanded = False
        self.shown = False
        self._anchor = (0, 0)                 # centre-x and top of the island on screen
        self._photo = None
        self._after_frame = self._after_follow = self._after_collapse = None
        self._closed = False

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=KEY)
        self.win.attributes("-transparentcolor", KEY)
        make_no_activate(self.win)
        self.canvas = tk.Canvas(self.win, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.w = Spring(self.pill[0], response=0.32, damping=0.82)
        self.h = Spring(self.pill[1], response=0.32, damping=0.82)
        self._build_panel()
        self.canvas.bind("<Button-1>", lambda e: self._clicked_pill())
        if "on_hide" in callbacks:                  # right-click: hide the island
            self.canvas.bind("<Button-3>", lambda e: self._cb["on_hide"]())
        for widget in (self.win, self.canvas):
            widget.bind("<Enter>", lambda e: self._pointer_in())
            widget.bind("<Leave>", lambda e: self._pointer_out())
        self._draw()
        self._follow()

    # ------------------------------------------------------------ content
    def _build_panel(self):
        s = self.panel[0] / PANEL[0]
        pad = round(16 * s)
        bw = (self.panel[0] - 2 * pad - 16) // 3          # three equal buttons
        self.list = tk.Listbox(self.canvas, bg=LIST_BG, fg=TEXT, selectbackground="#0a84ff",
                               selectforeground=TEXT, highlightthickness=0, bd=0,
                               activestyle="none", exportselection=False, font=ui.F_BODY(),
                               selectmode="browse")
        self.list.bind("<<ListboxSelect>>", lambda e: self._picked())
        self.btn_record = ui.Button(self.canvas, "Record", command=lambda: self._cb["on_record"](),
                                    kind="secondary", height=round(34 * s), page_bg=ISLAND, width=bw)
        self.btn_play = ui.Button(self.canvas, "Play", command=lambda: self._cb["on_play"](),
                                  kind="primary", height=round(34 * s), page_bg=ISLAND, width=bw)
        self.btn_stop = ui.Button(self.canvas, "Stop", command=lambda: self._cb["on_stop"](),
                                  kind="danger", height=round(34 * s), page_bg=ISLAND, width=bw)
        self._items = {
            "list": self.canvas.create_window(0, 0, window=self.list, anchor="nw", state="hidden"),
            "record": self.canvas.create_window(0, 0, window=self.btn_record, anchor="nw",
                                                state="hidden"),
            "play": self.canvas.create_window(0, 0, window=self.btn_play, anchor="nw",
                                              state="hidden"),
            "stop": self.canvas.create_window(0, 0, window=self.btn_stop, anchor="nw",
                                              state="hidden"),
        }
        self._pad = pad

    def _picked(self):
        chosen = self.list.curselection()
        if chosen:
            name = self.names[chosen[0]]
            self.selected = name
            self._cb["on_select"](name)
            self._draw()

    # ------------------------------------------------------------ what the tool tells it
    def set_target(self, hwnd):
        self.hwnd = hwnd
        self._follow_once()

    def set_state(self, mode, text):
        self.mode, self.text = mode, text
        self._draw()

    def set_recordings(self, names, selected=None):
        self.names = list(names)
        self.selected = selected if selected in self.names else (self.names[0] if self.names else None)
        self.list.delete(0, "end")
        for name in self.names:
            self.list.insert("end", name)
        if self.selected is not None:
            index = self.names.index(self.selected)
            self.list.selection_clear(0, "end")
            self.list.selection_set(index)
            self.list.see(index)
        self._draw()

    # ------------------------------------------------------------ expanding
    def _pointer_in(self):
        if self._after_collapse is not None:
            self.win.after_cancel(self._after_collapse)
            self._after_collapse = None
        self.expand()

    def _pointer_out(self):
        if self._after_collapse is None:
            self._after_collapse = self.win.after(COLLAPSE_DELAY_MS, self._maybe_collapse)

    def _maybe_collapse(self):
        self._after_collapse = None
        if self._closed:
            return
        px, py = self.win.winfo_pointerxy()
        x, y = self.win.winfo_rootx(), self.win.winfo_rooty()
        if x <= px < x + self.win.winfo_width() and y <= py < y + self.win.winfo_height():
            return                                      # still over it
        self.collapse()

    def _clicked_pill(self):
        self.collapse() if self.expanded else self.expand()

    def expand(self):
        self.expanded = True
        self._start()

    def collapse(self):
        self.expanded = False
        self._start()

    def _target_size(self):
        return self.panel if self.expanded else self.pill

    def _start(self):
        if not self._animate:
            self.settle()
            return
        if self._after_frame is None and not self._closed:
            self._after_frame = self.win.after(FRAME_MS, self._frame)

    def _frame(self):
        self._after_frame = None
        if self._closed:
            return
        tw, th = self._target_size()
        dt = FRAME_MS / 1000.0
        self.w.step(tw, dt)
        self.h.step(th, dt)
        self._draw()
        if not (self.w.settled(tw) and self.h.settled(th)):
            self._after_frame = self.win.after(FRAME_MS, self._frame)

    def settle(self):
        """Jump to the end of the animation (tests, and when animation is off)."""
        tw, th = self._target_size()
        self.w.snap(tw)
        self.h.snap(th)
        self._draw()

    # ------------------------------------------------------------ drawing and placing
    def _draw(self):
        if self._closed:
            return
        width, height = max(round(self.w.value), 8), max(round(self.h.value), 8)
        radius = round(height / 2) if height <= self.pill[1] * 1.4 else round(24 * self.panel[0] / PANEL[0])
        self._photo = _pill_image(width, height, radius)      # the old one is freed here
        self.canvas.delete("art")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo, tags="art")
        grown = self.h.value > self.pill[1] * 1.6 and self.w.value > self.pill[0] * 1.3
        pad = self._pad
        dot_r = max(4, round(self.pill[1] * 0.14))
        cy = round(self.pill[1] / 2)
        colour = DOT.get(self.mode, DOT["idle"])
        self.canvas.create_oval(pad, cy - dot_r, pad + 2 * dot_r, cy + dot_r, fill=colour,
                                outline="", tags="art")
        title = self.selected or "No recording"
        label = f"{title}  ·  {self.text}" if not grown else title
        self.canvas.create_text(pad + 2 * dot_r + 10, cy, text=label, anchor="w", fill=TEXT,
                                font=ui.sans(13, "bold"), tags="art", width=max(40, width - 2 * pad - 24))
        if grown:
            self.canvas.create_text(width - pad, cy, text=self.text, anchor="e", fill=TEXT_2,
                                    font=ui.sans(12), tags="art")
            top = self.pill[1] + 4
            bottom_h = self.btn_play.winfo_reqheight() or 34
            list_h = max(40, height - top - bottom_h - pad - 10)
            self.canvas.coords(self._items["list"], pad, top)
            self.canvas.itemconfigure(self._items["list"], width=width - 2 * pad, height=list_h,
                                      state="normal")
            by = height - pad - bottom_h
            gap = 8
            bw = (width - 2 * pad - 2 * gap) // 3
            for index, key in enumerate(("record", "play", "stop")):
                self.canvas.coords(self._items[key], pad + index * (bw + gap), by)
                self.canvas.itemconfigure(self._items[key], width=bw, state="normal")
            self.btn_record.set_text("Stop recording" if self.mode == "recording" else "Record")
        else:
            for item in self._items.values():
                self.canvas.itemconfigure(item, state="hidden")
        self.canvas.tag_raise("art")
        for item in self._items.values():
            self.canvas.tag_raise(item)
        self._place(width, height)

    def _place(self, width, height):
        x0, y0, sw, sh = self._screen()
        cx, top = self._anchor
        x = min(max(round(cx - width / 2), x0), x0 + sw - width)
        y = min(max(top, y0), y0 + sh - height)
        self.win.geometry(f"{width}x{height}+{x}+{y}")

    # ------------------------------------------------------------ following the window
    def _follow(self):
        self._follow_once()
        if not self._closed:
            self._after_follow = self.win.after(FOLLOW_MS, self._follow)

    def _follow_once(self):
        if self._closed:
            return
        rect = self._rect_of(self.hwnd) if self.hwnd else None
        if rect is None:
            if self.shown:
                self.win.withdraw()
                self.shown = False
            return
        x0, y0, sw, sh = self._screen()
        x, y, _where = island_position(rect, self.pill, (sw, sh), origin=(x0, y0))
        self._anchor = (x + self.pill[0] // 2, y)
        if not self.shown:
            self.win.deiconify()
            self.win.attributes("-topmost", True)
            self.shown = True
        self._place(max(round(self.w.value), 8), max(round(self.h.value), 8))

    def tick(self):
        """One follow step now (tests call this instead of waiting for the timer)."""
        self._follow_once()

    # ------------------------------------------------------------ life cycle
    def destroy(self):
        if self._closed:
            return
        self._closed = True
        for handle in (self._after_frame, self._after_follow, self._after_collapse):
            if handle is not None:
                with contextlib.suppress(tk.TclError):
                    self.win.after_cancel(handle)
        self._photo = None
        with contextlib.suppress(tk.TclError):
            self.win.destroy()
