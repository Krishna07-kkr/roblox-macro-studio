"""A page that scrolls when it is taller than the window.

Tools build into `.inner` exactly as they would into a plain frame. When the content fits, the
inner frame is as tall as the window, so children that expand still expand and nothing changes.
When it does not fit, the page scrolls with the mouse wheel and a thin bar at the right edge,
instead of cutting the bottom off. Tables and text boxes keep their own wheel.
"""

import tkinter as tk

from macro_studio.core.ui_theme import HAIRLINE_STRONG, SP_XS

STEP_PX = 48
BAR_W = 6
WATCH_MS = 250
OWN_WHEEL = ("Treeview", "Text", "Listbox", "Scrollbar", "TCombobox")


class ScrollPage(tk.Frame):
    def __init__(self, parent, bg):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0,
                                yscrollincrement=STEP_PX, yscrollcommand=self._draw_thumb)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar = tk.Canvas(self, width=BAR_W, bg=bg, highlightthickness=0, bd=0,
                             cursor="hand2")
        self.bar.bind("<Button-1>", lambda e: self._drag(e.y))
        self.bar.bind("<B1-Motion>", lambda e: self._drag(e.y))
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self._fit())
        self.inner.bind("<Configure>", lambda e: self._fit())
        self._fitting = False
        self._need = 0
        self._watching = False
        self.bind("<Map>", lambda e: self._watch())

    def _watch(self):
        """While shown, notice content that grew or shrank (text re-wrapping, rows added).

        The inner frame is held at a fixed height by the canvas, so Tk sends it no resize event
        when what it needs changes; this looks every quarter second instead.
        """
        if self._watching:
            return
        self._watching = True
        self._tick()

    def _tick(self):
        try:
            if not self.winfo_ismapped():
                self._watching = False
                return
            if self.inner.winfo_reqheight() != self._need:
                self._fit()
            self.after(WATCH_MS, self._tick)
        except tk.TclError:                 # the window was closed while a tick was waiting
            self._watching = False

    def _fit(self):
        """Inner frame: as wide as the window, at least as tall; scroll only if taller."""
        if self._fitting:
            return
        self._fitting = True
        try:
            view_w, view_h = self.canvas.winfo_width(), self.canvas.winfo_height()
            if view_w <= 1 or view_h <= 1:
                return
            need = self._need = self.inner.winfo_reqheight()
            height = max(need, view_h)
            self.canvas.itemconfigure(self._window, width=view_w, height=height)
            self.canvas.configure(scrollregion=(0, 0, view_w, height))
            if need > view_h:
                if not self.bar.winfo_ismapped():
                    self.bar.pack(side="right", fill="y", padx=(0, SP_XS), before=self.canvas)
            else:
                self.bar.pack_forget()
                self.canvas.yview_moveto(0.0)
            self._draw_thumb(*self.canvas.yview())
        finally:
            self._fitting = False

    def _scrollable(self):
        return self.inner.winfo_reqheight() > self.canvas.winfo_height()

    def on_wheel(self, event):
        """Mouse wheel over the page. Widgets that scroll themselves keep their own wheel."""
        widget = getattr(event, "widget", None)
        try:
            own = widget is not None and widget.winfo_class() in OWN_WHEEL
        except tk.TclError:
            own = False
        if own or not self._scrollable():
            return None
        notches = -int(event.delta / 120) or (-1 if event.delta > 0 else 1)
        self.canvas.yview_scroll(notches, "units")
        return "break"

    def _draw_thumb(self, first, last):
        self.bar.delete("all")
        if not self._scrollable():
            return
        height = self.bar.winfo_height()
        self.bar.create_rectangle(0, float(first) * height, BAR_W, float(last) * height,
                                  fill=HAIRLINE_STRONG, outline="")

    def _drag(self, y):
        first, last = self.canvas.yview()
        self.canvas.yview_moveto(y / max(1, self.bar.winfo_height()) - (last - first) / 2)
