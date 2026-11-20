"""The studio window: a shell that composes registered tools (Constitution I).

The shell owns the window, the brand, the navigation built from every tool's pages, the
page title and the shared services. It holds no feature logic: every feature is a tool
listed in macro_studio/registry.py. Visual primitives live in core/ui_theme.py.
"""

import sys
import time
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import font as tkfont
from typing import Callable

from macro_studio import registry
from macro_studio.calibrators.launch import (  # noqa: F401 - re-exported for app_gui users
    CALIBRATOR_SCRIPTS, calibrator_command, calibrator_from_argv, run_calibrator,
)
from macro_studio.core import scaling
from macro_studio.core import ui_theme as ui
from macro_studio.core.glass import DEFAULT_STYLE, apply_window_look
from macro_studio.core.motion import Spring
from macro_studio.core.scroll_page import ScrollPage
from macro_studio.core.ui_theme import CANVAS, ERROR, HAIRLINE, INK, MUTE, SP_MD, SP_SM, SP_XL, SP_XS
from macro_studio.core.ui_theme import NavItem
from macro_studio.services.automation import STOP_BINDINGS, AutomationLock
from macro_studio.services.events import EventBus
from macro_studio.services.hotkeys import HotkeyService
from macro_studio.services.scheduler import Scheduler
from macro_studio.settings import SettingsStore

SIDEBAR_W = 232            # at 100% text; grows with the text size (see `sidebar_width`)


PAGE_SLIDE_PX = 22


def sidebar_width(scale):
    return SIDEBAR_W + 2 * max(0, scale - 100)


def window_size(scale, screen_w, screen_h):
    """The first window size: bigger for bigger text, never larger than the screen."""
    width = min(1180 + 3 * max(0, scale - 100), screen_w - 60)
    height = min(880 + 2 * max(0, scale - 100), screen_h - 90)
    return width, height


@dataclass
class ToolContext:
    """What every tool receives from the shell (contracts/tool-contract.md)."""

    settings: SettingsStore
    scheduler: Scheduler
    hotkeys: HotkeyService
    automation: AutomationLock
    events: EventBus
    log: Callable[[str], None]
    ui_call: Callable[[Callable[[], None]], None]
    # Named things tools can do, for hotkeys and other tools to call without importing each other.
    # Every entry must be safe to call from any thread.
    actions: dict = field(default_factory=dict)


class StudioShell:
    def __init__(self, root, tools=None, settings_path=None):
        self.root = root
        self.root.title("Macro Studio")
        self.root.minsize(1040, 760)
        self.root.configure(bg=CANVAS)
        ui.init_fonts()

        events = EventBus(on_error=lambda event, exc: print(f"[EVENTS] {event}: {exc}"))
        self.ctx = ToolContext(
            settings=SettingsStore(settings_path, log=self.log),
            scheduler=Scheduler(
                on_error=lambda name, exc: self.log(f"[JOB] {name}: {exc}"),
                on_slow=lambda name, seconds: self.log(
                    f"[JOB] '{name}' took {seconds:.1f} s; a client may be frozen or the PC busy.")),
            hotkeys=HotkeyService(on_error=lambda a, exc: self.log(f"[HOTKEY] {a}: {exc}")),
            automation=AutomationLock(on_error=lambda o, exc: self.log(f"[STOP] {o}: {exc}")),
            events=events,
            log=self.log,
            ui_call=self.ui_call,
        )
        # Text size is read before any widget exists, so every font picks it up.
        system = self.ctx.settings.view("system", {"text_scale": ui.DEFAULT_TEXT_SCALE,
                                                   "text_scale_chosen": False, "glass": True,
                                                   "glass_style": DEFAULT_STYLE})
        glass = bool(system.get("glass"))
        style = system.get("glass_style")
        self._look = (glass, style)
        # Windows only accepts the look once the window really exists on screen, so apply it
        # when the window is first shown (and again if it is ever shown anew).
        self.root.bind("<Map>", self._on_map, add="+")
        chosen = system.get("text_scale_chosen")
        scale = system.get("text_scale") if chosen else ui.DEFAULT_TEXT_SCALE
        ui.set_text_scale(scale)
        width, height = window_size(ui.get_text_scale(), self.root.winfo_screenwidth(),
                                    self.root.winfo_screenheight())
        self.root.geometry(f"{width}x{height}")

        self.views, self.view_titles, self.nav_items = [], [], []
        self.animate_pages = False          # the running app turns this on (see main)
        self._slide_job = None
        self.current_view_idx = 0
        self._tools = []
        self._tools_by_id = {}
        self._closed = False

        specs = registry.ordered(registry.TOOLS if tools is None else tools)
        for spec in specs:
            tool = spec.factory(self, self.ctx)
            self._tools.append(tool)
            self._tools_by_id[spec.id] = tool

        self._build_frame()
        self._build_sidebar()
        self._build_topbar()
        self._build_views()

        for action, key in STOP_BINDINGS:
            self.ctx.hotkeys.bind_system(action, key, self.ctx.automation.emergency_stop)
        for tool in self._tools:
            if hasattr(tool, "start"):
                tool.start()
        self.ctx.scheduler.start()
        self.ctx.hotkeys.start()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if self.views:
            self._switch_view(0)

    # -----------------------------------------------------------
    # Shared helpers for tools
    # -----------------------------------------------------------
    def tool(self, tool_id):
        return self._tools_by_id.get(tool_id)

    def log(self, message):
        """Shared log line; tools listen on the 'log' event to display it."""
        print(message)
        ctx = self.__dict__.get("ctx")  # settings may log while the context is built
        if ctx is not None:
            ctx.events.publish("log", message=message)

    def ui_call(self, fn):
        """Run `fn` on the Tk thread (worker threads must never touch widgets).

        Safe to call from any thread, including while the window is closing: once the window
        is gone there is nothing left to update, so the call is dropped instead of raising.
        """
        try:
            self.root.after(0, fn)
        except (tk.TclError, RuntimeError):
            pass

    @staticmethod
    def scrollable(parent):
        """Return an inner frame that scrolls vertically inside `parent`."""
        canvas = tk.Canvas(parent, bg=CANVAS, highlightthickness=0, bd=0)
        inner = tk.Frame(canvas, bg=CANVAS)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner(_e=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas(event):
            canvas.itemconfigure(window, width=event.width)

        def _on_wheel(event):
            canvas.yview_scroll(int(-event.delta / 120), "units")

        # Claim the wheel only while the pointer is over this pane, otherwise the
        # last pane constructed would capture it for the whole app.
        def _claim(_e=None):
            canvas.bind_all("<MouseWheel>", _on_wheel)

        def _release(_e=None):
            canvas.unbind_all("<MouseWheel>")

        # Keyboard focus moving to a widget out of view scrolls it into view.
        def _reveal(event):
            widget = event.widget
            if not isinstance(widget, tk.Misc) or not str(widget).startswith(str(inner) + "."):
                return
            if not canvas.winfo_ismapped():
                return
            top = widget.winfo_rooty() - inner.winfo_rooty()
            bottom = top + widget.winfo_height()
            total = max(inner.winfo_height(), 1)
            view_top, view_bottom = (f * total for f in canvas.yview())
            margin = 16
            if top < view_top:
                canvas.yview_moveto(max(0, top - margin) / total)
            elif bottom > view_bottom:
                canvas.yview_moveto((bottom + margin - canvas.winfo_height()) / total)

        canvas.bind_all("<FocusIn>", _reveal, add="+")
        inner.bind("<Configure>", _on_inner)
        canvas.bind("<Configure>", _on_canvas)
        canvas.bind("<Enter>", _claim)
        canvas.bind("<Leave>", _release)
        canvas.pack(fill="both", expand=True)
        return inner

    # -----------------------------------------------------------
    # Layout
    # -----------------------------------------------------------
    def _build_frame(self):
        self.sidebar = tk.Frame(self.root, bg=CANVAS, width=sidebar_width(ui.get_text_scale()))
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        # 1px vertical hairline separating sidebar from content
        tk.Frame(self.root, bg=HAIRLINE, width=1).pack(side="left", fill="y")

        self.main = tk.Frame(self.root, bg=CANVAS)
        self.main.pack(side="left", fill="both", expand=True)

    def _build_sidebar(self):
        brand = tk.Frame(self.sidebar, bg=CANVAS, height=64)
        brand.pack(fill="x", padx=SP_MD, pady=(SP_MD, SP_XS))
        brand.pack_propagate(False)
        tk.Label(brand, text="▲", font=ui.sans(15, "bold"),
                 bg=CANVAS, fg=INK).pack(side="left", padx=(SP_XS, SP_XS))
        tk.Label(brand, text="Macro Studio", font=ui.sans(15, "bold"),
                 bg=CANVAS, fg=INK).pack(side="left")

        for tool in self._tools:
            if hasattr(tool, "build_sidebar"):
                tool.build_sidebar(self.sidebar)

        ui.eyebrow(self.sidebar, "Navigation", bg=CANVAS).pack(
            fill="x", padx=SP_MD + SP_XS, pady=(SP_MD, SP_XS))
        index = 0
        for tool in self._tools:
            for title in getattr(tool, "page_titles", []):
                item = NavItem(self.sidebar, title, lambda idx=index: self._switch_view(idx))
                item.pack(fill="x", padx=SP_SM, pady=1)
                self.nav_items.append(item)
                index += 1

        foot = tk.Frame(self.sidebar, bg=CANVAS)
        foot.pack(side="bottom", fill="x", padx=SP_MD + SP_XS, pady=SP_MD)
        tk.Label(foot, text="Killswitch  F6 / ESC", font=ui.F_EYEBROW(),
                 bg=CANVAS, fg=ERROR, anchor="w").pack(fill="x")
        tk.Label(foot, text="v2.5  Vision Engine", font=ui.F_EYEBROW(),
                 bg=CANVAS, fg=MUTE, anchor="w").pack(fill="x", pady=(2, 0))

    def _build_topbar(self):
        # Tall enough for the page title at any text size (a fixed 64 px cut it off).
        title_line = tkfont.Font(font=ui.sans(24, "bold")).metrics("linespace")
        bar = tk.Frame(self.main, bg=CANVAS, height=max(64, title_line + 2 * SP_MD + SP_XS))
        bar.pack(fill="x")
        bar.pack_propagate(False)

        inner = tk.Frame(bar, bg=CANVAS)
        inner.pack(fill="both", expand=True, padx=SP_XL, pady=SP_MD)

        self.lbl_page_title = tk.Label(inner, text="", font=ui.sans(24, "bold"),
                                       bg=CANVAS, fg=INK, anchor="w")
        self.lbl_page_title.pack(side="left")

        for tool in self._tools:
            if hasattr(tool, "build_topbar"):
                tool.build_topbar(inner)

        tk.Frame(self.main, bg=HAIRLINE, height=1).pack(fill="x")

    def _build_views(self):
        self.view_container = tk.Frame(self.main, bg=CANVAS)
        self.view_container.pack(fill="both", expand=True)

        for tool in self._tools:
            titles = list(getattr(tool, "page_titles", []))
            pages = [ScrollPage(self.view_container, bg=CANVAS) for _ in titles]
            self.views.extend(pages)
            self.view_titles.extend(titles)
            tool.build_pages([page.inner for page in pages])
        # One wheel binding for the whole window: the page in front scrolls if it is taller
        # than the window (tables and text boxes keep their own wheel).
        self.root.bind("<MouseWheel>", self._on_wheel, add="+")

    def _on_wheel(self, event):
        page = self.views[self.current_view_idx] if self.views else None
        return page.on_wheel(event) if page is not None else None

    def _on_map(self, event):
        if event.widget is self.root:
            self.root.after(30, lambda: apply_window_look(self.root, *self._look))

    def _show_page(self, page):
        """Put a page in front. In the running app it slides in a few pixels on a spring, like a
        native push; a second click mid-slide simply retargets. Tests switch instantly."""
        for v in self.views:
            v.place_forget()
        if self._slide_job is not None:
            self.root.after_cancel(self._slide_job)
            self._slide_job = None
        if not self.animate_pages:
            page.place(x=0, y=0, relwidth=1, relheight=1)
            return
        spring = Spring(PAGE_SLIDE_PX, response=0.22, damping=1.0)
        page.place(x=PAGE_SLIDE_PX, y=0, relwidth=1, relheight=1)

        last = [time.monotonic()]

        def frame():
            self._slide_job = None
            now = time.monotonic()
            spring.step(0.0, min(0.05, now - last[0]))     # real elapsed time: a slow frame
            last[0] = now                                   # never slows the whole animation
            if abs(spring.value) < 0.5:
                page.place(x=0, y=0, relwidth=1, relheight=1)
                return
            page.place(x=round(spring.value), y=0, relwidth=1, relheight=1)
            self._slide_job = self.root.after(16, frame)
        self._slide_job = self.root.after(16, frame)

    def _switch_view(self, idx):
        self._show_page(self.views[idx])
        self.current_view_idx = idx
        self.lbl_page_title.config(text=self.view_titles[idx])
        for i, item in enumerate(self.nav_items):
            item.set_active(i == idx)
        # Let tools pause work tied to a hidden page (e.g. live feeds).
        for tool in self._tools:
            if hasattr(tool, "on_view_changed"):
                tool.on_view_changed(self.view_titles[idx])

    # -----------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------
    def shutdown(self):
        """Stop every tool, then the shared threads. Safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        for tool in reversed(self._tools):
            if hasattr(tool, "stop"):
                try:
                    tool.stop()
                except Exception as e:
                    print(f"[SHUTDOWN] {type(tool).__name__}: {e}")
        self.ctx.hotkeys.stop()
        self.ctx.scheduler.stop()

    def _on_close(self):
        # A tool may turn the close into something else, e.g. hiding to the tray.
        for tool in self._tools:
            if hasattr(tool, "intercept_close") and tool.intercept_close():
                return
        self.quit_now()

    def quit_now(self):
        """Really close the studio (the tray's Exit and the normal close both end up here)."""
        # A tool with unsaved work may ask to save first, or cancel the close.
        for tool in self._tools:
            if hasattr(tool, "confirm_close") and not tool.confirm_close():
                return
        self.shutdown()
        self.root.destroy()


class MacroStudioApp(StudioShell):
    """The studio with every registered tool.

    Legacy: attributes the shell does not have fall through to the macro tool, so code
    written against the old single-class app (the UI wiring tests, scripts) keeps
    working. Removed together with the other compatibility shims (T065).
    """

    def __getattr__(self, name):
        tools = self.__dict__.get("_tools_by_id") or {}
        macro = tools.get("macro")
        if macro is None or name.startswith("__"):
            raise AttributeError(name)
        return getattr(macro, name)


def main():
    # Must run before Tk creates any window, otherwise Windows hands this
    # process virtualised (lying) pixel coordinates on scaled displays.
    scaling.set_dpi_awareness()

    kind = calibrator_from_argv(sys.argv)
    if kind:
        run_calibrator(kind)
        return

    root = tk.Tk()
    app = MacroStudioApp(root)
    app.animate_pages = True
    root.mainloop()
