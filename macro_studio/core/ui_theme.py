"""
Vercel-inspired design system for the Macro Studio GUI.

Tokens come from the Vercel DESIGN.md (VoltAgent/awesome-design-md), flipped to
the dark polarity that spec defines for its dark bands:
    primary #171717        -> surface ladder
    on-primary #ffffff     -> ink
    hairline-strong #a1a1a1 -> deemphasised body text
Semantic and accent tokens are kept verbatim.

Font sizes use Tk's negative convention (negative == pixels), so the px values
from the design system carry over 1:1.
"""

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageTk

# ---------------------------------------------------------------
# Colors
# ---------------------------------------------------------------
CANVAS = "#000000"           # systemBackground (dark)
SURFACE = "#1c1c1e"          # secondarySystemBackground: cards and grouped lists
SURFACE_2 = "#2c2c2e"        # tertiary: inputs, secondary buttons, selected rows
SURFACE_3 = "#3a3a3c"        # hover
SURFACE_4 = "#48484a"        # pressed

HAIRLINE = "#38383a"         # separator
HAIRLINE_STRONG = "#545458"
BORDER_INPUT = "#6e6e73"     # input and switch outlines: 3:1 against SURFACE (WCAG 1.4.11)

INK = "#ffffff"              # label
BODY = "#bcbcc2"             # secondary text
MUTE = "#98989d"             # tertiary text; >= 4.5:1 on every surface
ON_ACCENT = "#ffffff"

LINK = "#2997ff"             # systemBlue as text, outlines and focus rings on dark surfaces
LINK_DEEP = "#0071e3"        # pressed / deep blue
ACCENT_FILL = "#0071e3"      # filled blue buttons and selected rows: 4.7:1 with white text
ERROR = "#ff453a"            # systemRed
ERROR_DEEP = "#d70015"
ERROR_TEXT = "#ff6961"       # red text on dark surfaces
WARNING = "#ff9f0a"          # systemOrange
CYAN = "#30d158"             # systemGreen -> success / live
VIOLET = "#bf5af2"           # systemPurple
PINK = "#ff375f"             # systemPink
FOCUS = LINK                 # keyboard focus ring

# ---------------------------------------------------------------
# Spacing (base unit 4px) and radii
# ---------------------------------------------------------------
SP_XXS, SP_XS, SP_SM, SP_MD = 4, 8, 12, 16
SP_LG, SP_XL, SP_2XL, SP_3XL = 24, 32, 40, 48

R_XS, R_SM, R_MD, R_LG, R_XL = 4, 8, 10, 14, 20
R_PILL = 999                 # capsule: the radius is clamped to half the height

# ---------------------------------------------------------------
# Typography
# ---------------------------------------------------------------
_SANS_STACK = ["SF Pro Text", "SF Pro Display", "Segoe UI Variable Text", "Segoe UI Variable Display",
               "Inter", "Segoe UI", "Helvetica"]
_MONO_STACK = ["Geist Mono", "JetBrains Mono", "Cascadia Mono", "Consolas", "Courier New"]

_resolved = {}


def _resolve(stack, fallback):
    """Pick the first installed family from a stack. Requires a live Tk root."""
    try:
        available = {f.lower() for f in tkfont.families()}
    except Exception:
        return fallback
    for fam in stack:
        if fam.lower() in available:
            return fam
    return fallback


def init_fonts():
    """Call once after the Tk root exists."""
    _resolved["sans"] = _resolve(_SANS_STACK, "Segoe UI")
    _resolved["mono"] = _resolve(_MONO_STACK, "Consolas")
    return _resolved


# Text size. Only text scales: the layout is built from fixed sizes, so very large text would
# clip, which is why the choices stop at 150%. Set it once at start-up, before any widget exists.
TEXT_SCALES = (90, 100, 110, 125, 150)
#: Used until the player picks a size on the System page.
DEFAULT_TEXT_SCALE = 125
_text_scale = 100


def normalize_text_scale(value):
    """One of TEXT_SCALES, or 100 for anything else (a bad saved value must not break the UI)."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 100
    if isinstance(value, bool) or number != value and not isinstance(value, str):
        return 100
    return number if number in TEXT_SCALES else 100


def set_text_scale(percent):
    global _text_scale
    _text_scale = normalize_text_scale(percent)
    return _text_scale


def get_text_scale():
    """The text size in use (percent)."""
    return _text_scale


def _px(px):
    return -max(8, round(px * _text_scale / 100))


def sans(px=14, weight="normal"):
    return (_resolved.get("sans", "Segoe UI"), _px(px), weight)


def mono(px=12, weight="normal"):
    return (_resolved.get("mono", "Consolas"), _px(px), weight)


# Design-system type tokens (weights 600/500 collapse to Tk "bold")
def F_DISPLAY_MD():
    return sans(24, "bold")     # display-md


def F_DISPLAY_SM():
    return sans(20, "bold")     # display-sm


def F_METRIC():
    return sans(30, "bold")     # stat numerals


def F_BODY():
    return sans(14)             # body-sm (default UI text)


def F_BODY_STRONG():
    return sans(14, "bold")     # body-sm-strong


def F_BUTTON():
    return sans(14, "bold")     # button-md


def F_CAPTION():
    return sans(12)             # caption


def F_EYEBROW():
    return sans(11, "bold")      # footnote-style section labels


def F_CODE():
    return mono(13)             # code


# ---------------------------------------------------------------
# Rounded-rectangle rendering
# Tk has no border-radius, so shapes are drawn with PIL at 4x and
# downsampled for antialiasing, then used as widget backgrounds.
# ---------------------------------------------------------------
_img_cache = {}
_img_cache_owner = None  # Tk interpreter the cached images belong to
SS = 4  # supersample factor
GLASS_SS = 3  # supersampling of the glass edge masks


def rounded_image(w, h, radius, fill, border=None, border_width=1, page_bg=CANVAS):
    """Return a cached antialiased rounded-rect PhotoImage."""
    global _img_cache_owner
    # A PhotoImage dies with the Tk interpreter that made it. When a new root
    # replaces a destroyed one, reusing the cache would hand out dead images.
    owner = getattr(getattr(tk, "_default_root", None), "tk", None)
    if owner is not _img_cache_owner:
        _img_cache.clear()
        _img_cache_owner = owner

    w, h = max(int(w), 1), max(int(h), 1)
    key = (w, h, radius, fill, border, border_width, page_bg)
    if key in _img_cache:
        return _img_cache[key]
    _cap_cache()

    base = Image.new("RGB", (w * SS, h * SS), page_bg)
    draw = ImageDraw.Draw(base)
    r = min(radius * SS, (min(w, h) * SS) // 2)
    draw.rounded_rectangle(
        [0, 0, w * SS - 1, h * SS - 1],
        radius=r,
        fill=fill,
        outline=border,
        width=border_width * SS if border else 0,
    )
    photo = ImageTk.PhotoImage(base.resize((w, h), Image.Resampling.LANCZOS))
    _img_cache[key] = photo
    return photo


def glass_image(w, h, radius, fill, page_bg=CANVAS, rim=0.34, sheen=0.0):
    """A "Liquid Glass" panel: a bright rim light that is strongest at the top left and fades
    towards the bottom right, like light catching the edge of glass. `sheen` (0 to 1) also
    lightens the top of the fill; use it only where nothing else is drawn on top (cards hold
    child widgets with a flat colour, so they use none).

    Cached like `rounded_image`. Over the translucent window (see core/glass.py) the fill
    reads as frosted glass; on a solid window it still looks like a lit, rounded panel.
    Built from image masks at the final size (only the two edge masks are drawn larger, for
    smooth corners), so a big card costs a few milliseconds, not a per-pixel pass.
    """
    owner = getattr(getattr(tk, "_default_root", None), "tk", None)
    global _img_cache_owner
    if owner is not _img_cache_owner:
        _img_cache.clear()
        _img_cache_owner = owner
    w, h = max(int(w), 1), max(int(h), 1)
    key = ("glass", w, h, radius, fill, page_bg, round(rim, 3), round(sheen, 3))
    if key in _img_cache:
        return _img_cache[key]
    _cap_cache()

    width, height = w * GLASS_SS, h * GLASS_SS
    r = min(radius * GLASS_SS, min(width, height) // 2)
    outer = Image.new("L", (width, height), 0)
    ImageDraw.Draw(outer).rounded_rectangle([0, 0, width - 1, height - 1], radius=r, fill=255)
    inset = max(1, round(1.3 * GLASS_SS))
    inner = Image.new("L", (width, height), 0)
    ImageDraw.Draw(inner).rounded_rectangle([inset, inset, width - 1 - inset, height - 1 - inset],
                                            radius=max(r - inset, 0), fill=255)
    shape = outer.resize((w, h), Image.Resampling.LANCZOS)
    edge = ImageChops.subtract(outer, inner).resize((w, h), Image.Resampling.LANCZOS)

    picture = Image.new("RGB", (w, h), page_bg)
    body = Image.new("RGB", (w, h), _rgb(fill))
    if sheen:
        lit = Image.new("RGB", (w, h), _lift(fill, sheen))
        body = Image.composite(lit, body, Image.linear_gradient("L").transpose(
            Image.Transpose.FLIP_TOP_BOTTOM).resize((w, h), Image.Resampling.BILINEAR))
    picture.paste(body, mask=shape)

    # The rim light fades diagonally from `rim` (top left) to a faint trace (bottom right).
    grid = 24
    gy, gx = np.mgrid[0:grid, 0:grid].astype(np.float32) / (grid - 1)
    fade = (gx + gy) / 2.0
    strength = (rim * (1 - fade) + 0.06 * fade) * 255.0
    light = Image.fromarray(strength.astype(np.uint8), "L").resize((w, h), Image.Resampling.BILINEAR)
    picture.paste((255, 255, 255), mask=ImageChops.multiply(edge, light))
    photo = ImageTk.PhotoImage(picture)
    _img_cache[key] = photo
    return photo


def _rgb(colour):
    colour = colour.lstrip("#")
    return tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))


def _lift(colour, amount):
    """The colour moved towards white by `amount` (0 to 1)."""
    return tuple(round(c + (255 - c) * amount) for c in _rgb(colour))


def _cap_cache(limit=400):
    """The image cache grows with every window size seen; start over when it gets large."""
    if len(_img_cache) > limit:
        _img_cache.clear()


def mix(fg, bg, alpha):
    """Blend fg over bg at `alpha` - used for translucent badge tints."""
    def parts(c):
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    f, b = parts(fg), parts(bg)
    return "#%02x%02x%02x" % tuple(int(f[i] * alpha + b[i] * (1 - alpha)) for i in range(3))


class Card(tk.Frame):
    """
    A rounded surface panel. Add children to `.body`.

    The rounded shape is painted on a Canvas lowered behind the content, so the
    frame still sizes itself to its children through normal geometry management.
    """

    def __init__(self, parent, radius=R_LG, fill=SURFACE, border=None,
                 padding: int | tuple[int, int] = SP_MD, page_bg=CANVAS, glass=True, **kw):
        super().__init__(parent, bg=page_bg, **kw)
        self._radius, self._fill, self._border, self._page_bg = radius, fill, border, page_bg
        self._glass = glass
        self._bg_canvas = tk.Canvas(self, bg=page_bg, highlightthickness=0, bd=0)
        self._bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        tk.Misc.lower(self._bg_canvas)
        self._img = None

        pad = padding if isinstance(padding, (tuple, list)) else (padding, padding)
        self.body = tk.Frame(self, bg=fill)
        self.body.pack(fill="both", expand=True, padx=pad[0], pady=pad[1])
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _event=None):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            return
        if self._glass and self._border is None:
            self._img = glass_image(w, h, self._radius, self._fill, page_bg=self._page_bg)
        else:
            self._img = rounded_image(w, h, self._radius, self._fill,
                                      self._border, page_bg=self._page_bg)
        self._bg_canvas.delete("bg")
        self._bg_canvas.create_image(0, 0, anchor="nw", image=self._img, tags="bg")
        tk.Misc.lower(self._bg_canvas)

    def restyle(self, fill=None, border=None):
        """Recolour the card in place (e.g. to show a selected state)."""
        if fill is not None:
            self._fill = fill
            self.body.configure(bg=fill)
        if border is not None:
            self._border = border
        self._redraw()


class Button(tk.Canvas):
    """
    Canvas-drawn button with rounded corners and hover / press / disabled states.

    The design system uses a 6px square shape for in-app buttons (the 100px pill
    is reserved for marketing CTAs), and a single inverted primary CTA.
    """

    KINDS = {
        # Filled blue: the one main action. Gray "tinted": everything else. Plain blue text for
        # quiet actions. Red text for destructive ones. Capsule shape like iOS.
        "primary":   dict(fill=ACCENT_FILL, fg=ON_ACCENT, border=None, hover="#0a84ff",
                          press="#005bb8"),
        "secondary": dict(fill=SURFACE_2, fg=INK, border=None, hover=SURFACE_3, press=SURFACE_4),
        "ghost":     dict(fill=None, fg=LINK, border=None, hover=None, press=None),
        "danger":    dict(fill=SURFACE_2, fg=ERROR_TEXT, border=None, hover=SURFACE_3,
                          press=SURFACE_4),
    }

    @classmethod
    def spec_for(cls, kind, page_bg):
        """The colours of a kind; a plain (ghost) button borrows the page colour."""
        spec = dict(cls.KINDS.get(kind, cls.KINDS["secondary"]))
        if spec["fill"] is None:
            spec["fill"] = page_bg
            spec["hover"] = mix(INK, page_bg, 0.10)
            spec["press"] = mix(INK, page_bg, 0.18)
        return spec

    def __init__(self, parent, text, command=None, kind="secondary",
                 width=None, height=32, radius=R_PILL, page_bg=CANVAS,
                 font=None, state="normal"):
        self._spec = self.spec_for(kind, page_bg)
        self._kind = kind
        self._text = text
        self._command = command
        self._radius = radius
        self._page_bg = page_bg
        self._font = font or F_BUTTON()
        self._state = state
        self._img = None

        if width is None:
            width = tkfont.Font(font=self._font).measure(text) + 2 * SP_MD

        super().__init__(parent, width=width, height=height, bg=page_bg,
                         highlightthickness=0, bd=0, cursor="hand2",
                         takefocus=1 if state == "normal" else 0)
        self._cw, self._ch = width, height
        self._pressed = False   # mouse went down on this button and is still held
        self._inside = False
        self._focused = False
        self._paint(self._spec["fill"])

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        for key in ("<Return>", "<KP_Enter>", "<space>"):
            self.bind(key, self._on_key)
        self.bind("<FocusIn>", lambda e: self._set_focused(True))
        self.bind("<FocusOut>", lambda e: self._set_focused(False))

    # Feedback on press, action on release, and only if the pointer is still on the
    # button: pressing and dragging away cancels, like every native button.
    def _over(self, event):
        return 0 <= event.x < self._cw and 0 <= event.y < self._ch

    def _current_fill(self):
        if self._pressed and self._inside:
            return self._spec["press"]
        return self._spec["hover"] if self._inside else self._spec["fill"]

    def _on_enter(self, _e):
        self._inside = True
        self._refresh()

    def _on_leave(self, _e):
        self._inside = False
        self._refresh()

    def _on_press(self, event):
        self._pressed, self._inside = True, self._over(event)
        self._refresh()

    def _on_drag(self, event):
        inside = self._over(event)
        if inside != self._inside:
            self._inside = inside
            self._refresh()

    def _on_release(self, event):
        fire = self._pressed and self._over(event) and self._state == "normal"
        self._pressed, self._inside = False, self._over(event)
        self._refresh()
        if fire:
            self.invoke()

    def _on_key(self, _event):
        if self._state == "normal":
            self._paint(self._spec["press"])
            self.after(90, self._refresh)
            self.invoke()
        return "break"

    def invoke(self):
        if self._state == "normal" and self._command:
            self._command()

    def _set_focused(self, focused):
        self._focused = focused
        self._refresh()

    def _refresh(self):
        if self.winfo_exists():
            self._paint(self._current_fill())

    def _hover(self, fill):
        if self._state == "normal":
            self._paint(fill)

    def _paint(self, fill):
        self.delete("all")
        if self._state == "disabled":
            fill, fg, border = SURFACE_2, MUTE, None
        else:
            fg, border = self._spec["fg"], self._spec["border"]
        width = 1
        if self._focused and self._state == "normal":
            border, width = (INK if self._kind == "primary" else FOCUS), 2
        if self._kind != "ghost" and self._state == "normal" and border is None:
            self._img = glass_image(self._cw, self._ch, self._radius, fill,
                                    page_bg=self._page_bg, rim=0.26, sheen=0.08)
        else:
            self._img = rounded_image(self._cw, self._ch, self._radius, fill,
                                      border, border_width=width, page_bg=self._page_bg)
        self.create_image(0, 0, anchor="nw", image=self._img)
        self.create_text(self._cw // 2, self._ch // 2, text=self._text,
                         fill=fg, font=self._font)

    def set_state(self, state):
        self._state = state
        normal = state == "normal"
        self.configure(cursor="hand2" if normal else "arrow", takefocus=1 if normal else 0)
        if not normal and self._focused:
            nxt = self.tk_focusNext()
            (nxt or self.winfo_toplevel()).focus_set()
        self._paint(self._spec["fill"])

    def set_text(self, text):
        self._text = text
        self._paint(self._current_fill() if self._state == "normal" else self._spec["fill"])

    def set_kind(self, kind):
        self._kind = kind
        self._spec = self.spec_for(kind, self._page_bg)
        self._paint(self._spec["fill"])


class Badge(tk.Canvas):
    """Pill status badge: a dot plus an uppercase mono label."""

    def __init__(self, parent, text="IDLE", color=MUTE, page_bg=SURFACE, height=22,
                 font=None):
        self._font = font or sans(11, "bold")
        self._page_bg = page_bg
        self._ch = height
        self._pad = max(30, height + 8)       # dot inset grows with the pill
        self._cw = tkfont.Font(font=self._font).measure(text.upper()) + self._pad
        super().__init__(parent, width=self._cw, height=height, bg=page_bg,
                         highlightthickness=0, bd=0)
        self._img = None
        self.set(text, color)

    def set(self, text, color):
        text = text.upper()
        self.text = text
        needed = tkfont.Font(font=self._font).measure(text) + self._pad
        if needed != self._cw:
            self._cw = needed
            self.configure(width=needed)
        self.delete("all")
        tint = mix(color, self._page_bg, 0.16)
        self._img = rounded_image(self._cw, self._ch, self._ch // 2, tint,
                                  None, page_bg=self._page_bg)
        self.create_image(0, 0, anchor="nw", image=self._img)
        cy = self._ch // 2
        r = max(3, self._ch // 8)
        x0 = self._ch // 2 - 1
        self.create_oval(x0, cy - r, x0 + 2 * r, cy + r, fill=color, outline="")
        self.create_text(x0 + 2 * r + 6, cy, anchor="w", text=text, fill=color,
                         font=self._font)


class Toggle(tk.Canvas):
    """Custom-drawn switch bound to a BooleanVar."""

    W, H = 51, 31                  # the iOS switch

    def __init__(self, parent, variable, page_bg=SURFACE, command=None):
        super().__init__(parent, width=self.W, height=self.H, bg=page_bg,
                         highlightthickness=0, bd=0, cursor="hand2", takefocus=1)
        self.var = variable
        self._command = command
        self._page_bg = page_bg
        self._img = None
        self._focused = False
        self._pressed = False
        self._t = 1.0 if self.var.get() else 0.0      # 0 = off, 1 = on, between while sliding
        self._slide = None
        # Flip on release over the switch, so pressing and dragging off cancels.
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        for key in ("<space>", "<Return>", "<KP_Enter>"):
            self.bind(key, self._on_key)
        self.bind("<FocusIn>", lambda e: self._set_focused(True))
        self.bind("<FocusOut>", lambda e: self._set_focused(False))
        self.redraw()

    def _on_press(self, _event):
        self._pressed = True

    def _on_release(self, event):
        pressed, self._pressed = self._pressed, False
        if pressed and 0 <= event.x < self.W and 0 <= event.y < self.H:
            self._toggle()

    def _on_key(self, _event):
        self._toggle()
        return "break"

    def _set_focused(self, focused):
        self._focused = focused
        self.redraw()

    def _toggle(self, _event=None):
        self.var.set(not self.var.get())
        self._glide()
        if self._command:
            self._command()

    def _glide(self):
        """Slide the knob to where the switch now is. Pressing again mid-slide turns it round
        from wherever it has got to (no jump)."""
        if self._slide is not None:
            self.after_cancel(self._slide)
            self._slide = None
        target = 1.0 if self.var.get() else 0.0
        gap = target - self._t
        if abs(gap) < 0.03:
            self._t = target
        else:
            self._t += gap * 0.4
            self._slide = self.after(16, self._glide)
        self.redraw(snap=False)

    def redraw(self, snap=True):
        on = bool(self.var.get())
        if snap:                                # a change made from outside just shows at once
            self._t = 1.0 if on else 0.0
        t = self._t
        self.delete("all")
        if self._focused:
            border = INK if on else FOCUS     # a blue ring would vanish on the blue track
        else:
            border = None if on else BORDER_INPUT
        self._img = rounded_image(self.W, self.H, self.H // 2,
                                  mix(CYAN, SURFACE_3, t),
                                  border, border_width=2 if self._focused else 1,
                                  page_bg=self._page_bg)
        self.create_image(0, 0, anchor="nw", image=self._img)
        radius = self.H // 2 - 3
        half = self.H // 2
        kx = round(half + (self.W - self.H) * t)
        self.create_oval(kx - radius, 3, kx + radius, self.H - 3, fill=ON_ACCENT, outline="")


class Field(tk.Frame):
    """Rounded text input. The underlying tk.Entry is exposed as `.entry`."""

    def __init__(self, parent, width=8, value="", page_bg=SURFACE):
        super().__init__(parent, bg=page_bg)
        self._page_bg = page_bg
        self._bg = tk.Canvas(self, bg=page_bg, highlightthickness=0, bd=0)
        self._bg.place(x=0, y=0, relwidth=1, relheight=1)
        tk.Misc.lower(self._bg)
        self._img = None
        self._focused = False
        self.error = None          # message while the value is rejected

        self.entry = tk.Entry(self, width=width, bg=SURFACE_2, fg=INK,
                              insertbackground=INK, relief="flat", bd=0,
                              font=F_BODY(), highlightthickness=0)
        self.entry.insert(0, str(value))
        self.entry.pack(fill="both", expand=True, padx=SP_SM, pady=6)
        self.bind("<Configure>", self._redraw)
        self.entry.bind("<FocusIn>", lambda e: self._set_focused(True), add="+")
        self.entry.bind("<FocusOut>", lambda e: self._set_focused(False), add="+")
        # Typing is how a rejected value gets fixed, so it clears the red outline.
        self.entry.bind("<Key>", lambda e: self.set_error(None), add="+")

    def _set_focused(self, focused):
        self._focused = focused
        self._redraw()

    def set_error(self, message):
        """Outline the field in red while `message` is set; None clears it."""
        if message == self.error:
            return
        self.error = message
        self._redraw()

    def _redraw(self, _event=None):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            return
        border, width = BORDER_INPUT, 1
        if self.error:
            border, width = ERROR_TEXT, 2
        elif self._focused:
            border, width = FOCUS, 2
        self._img = rounded_image(w, h, R_SM, SURFACE_2, border, border_width=width,
                                  page_bg=self._page_bg)
        self._bg.delete("bg")
        self._bg.create_image(0, 0, anchor="nw", image=self._img, tags="bg")
        tk.Misc.lower(self._bg)

    def get(self):
        return self.entry.get()

    def set(self, value):
        self.entry.delete(0, tk.END)
        self.entry.insert(0, str(value))


class NavItem(tk.Canvas):
    """Sidebar row: a rounded highlight behind the title, like the macOS sidebar."""

    def __init__(self, parent, text, command, page_bg=CANVAS):
        line = tkfont.Font(font=F_BODY_STRONG()).metrics("linespace")
        super().__init__(parent, bg=page_bg, height=line + 16, highlightthickness=0, bd=0,
                         takefocus=1, cursor="hand2")
        self._command, self._text, self._page_bg = command, text, page_bg
        self._active = self._hover = self._focused = False
        self._img = None
        for key in ("<Return>", "<KP_Enter>", "<space>"):
            self.bind(key, self._on_key)
        self.bind("<Button-1>", lambda e: self._command())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.bind("<FocusOut>", lambda e: self._set_focus(False))
        self.bind("<Configure>", lambda e: self._paint())

    def _on_key(self, _event):
        self._command()
        return "break"

    def _set_hover(self, hover):
        self._hover = hover
        self._paint()

    def _set_focus(self, focused):
        self._focused = focused
        self._paint()

    def set_active(self, active):
        self._active = active
        self._paint()

    def _paint(self):
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return
        fill = SURFACE_2 if self._active else (SURFACE if self._hover else self._page_bg)
        border, border_width = (FOCUS, 2) if self._focused else (None, 1)
        self.delete("all")
        if self._active and border is None:
            self._img = glass_image(width, height, R_MD, fill, page_bg=self._page_bg, rim=0.3,
                                    sheen=0.06)
        else:
            self._img = rounded_image(width, height, R_MD, fill, border,
                                      border_width=border_width, page_bg=self._page_bg)
        self.create_image(0, 0, anchor="nw", image=self._img)
        strong = self._active
        self.create_text(SP_MD, height // 2, anchor="w", text=self._text,
                         fill=INK if (strong or self._hover) else BODY,
                         font=F_BODY_STRONG() if strong else F_BODY())


def eyebrow(parent, text, bg=SURFACE, fg=MUTE):
    """Mono uppercase section label - the design system's technical voice."""
    return tk.Label(parent, text=text.upper(), font=F_EYEBROW(), bg=bg, fg=fg, anchor="w")


def caption(parent, text, bg=SURFACE, fg=MUTE):
    """A sentence of help text. Eyebrows are for short labels; sentences stay readable.

    It wraps to the width of `parent`, so a long sentence is never cut off at the card edge,
    at any window width or text size.
    """
    label = tk.Label(parent, text=text, font=F_CAPTION(), bg=bg, fg=fg, anchor="w",
                     justify="left")

    def fit(event, label=label):
        label.configure(wraplength=max(120, event.width - 4))
    parent.bind("<Configure>", fit, add="+")
    return label


def heading(parent, text, bg=SURFACE, fg=INK, size=16):
    return tk.Label(parent, text=text, font=sans(size, "bold"), bg=bg, fg=fg, anchor="w")


TABLE_STYLE = "Studio.Treeview"


def table_style(root):
    """Style a ttk.Treeview with the dark tokens and return its style name.

    The default Windows ttk theme ignores row and heading colours, so the table needs the
    "clam" theme. Only tables use ttk here (every other widget is drawn by this module), so
    switching the theme is safe. Safe to call more than once.
    """
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(TABLE_STYLE, background=SURFACE, fieldbackground=SURFACE, foreground=INK,
                    bordercolor=HAIRLINE, lightcolor=SURFACE, darkcolor=SURFACE, rowheight=30,
                    borderwidth=0, font=F_BODY())
    style.configure(f"{TABLE_STYLE}.Heading", background=SURFACE_2, foreground=BODY, relief="flat",
                    font=F_BODY_STRONG(), padding=(8, 6))
    style.map(TABLE_STYLE, background=[("selected", ACCENT_FILL)],
              foreground=[("selected", ON_ACCENT)])
    style.map(f"{TABLE_STYLE}.Heading", background=[("active", SURFACE_2)])
    return TABLE_STYLE
