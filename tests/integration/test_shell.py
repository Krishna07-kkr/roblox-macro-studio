"""The shell builds every registered tool and nothing else (T022, SC-002).

Runs in its own process because it creates a Tk root (see conftest.run_in_fresh_python).
"""

import sys

failures = []


def check(name, cond, detail: object = ""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


def run():
    import tkinter as tk
    from macro_studio.app import MacroStudioApp, StudioShell
    from macro_studio.registry import ToolSpec

    # 1. A brand-new tool needs only its own module and a registry entry.
    class DummyTool:
        page_titles = ["Dummy Page"]

        def __init__(self, shell, ctx):
            self.built, self.started, self.stopped = [], False, False
            self.ctx = ctx

        def build_pages(self, frames):
            tk.Label(frames[0], text="dummy").pack()
            self.built = frames

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

    root = tk.Tk()
    root.withdraw()
    shell = StudioShell(root, tools=[ToolSpec("dummy", "Dummy", 10, DummyTool)])
    dummy = shell.tool("dummy")
    check("dummy tool built with one page", len(dummy.built) == 1)
    check("dummy page is in the navigation", shell.view_titles == ["Dummy Page"],
          shell.view_titles)
    check("dummy tool started", dummy.started)
    check("tool context carries shared services",
          all(hasattr(dummy.ctx, n) for n in
              ("settings", "scheduler", "hotkeys", "automation", "events", "log", "ui_call")))
    check("F6 is bound to the shared emergency stop",
          "emergency_stop" in shell.ctx.hotkeys._bindings)
    shell.shutdown()
    check("shutdown stops tools", dummy.stopped)
    check("shutdown stops shared threads",
          not shell.ctx.scheduler.running and not shell.ctx.hotkeys.running)
    root.destroy()

    # 2. The default studio shows the macro tool's three pages, as before.
    root = tk.Tk()
    root.withdraw()
    app = MacroStudioApp(root)
    root.update()
    check("macro pages come first, in order, then the other tools' pages",
          app.view_titles == ["Overview", "Slots & Coordinates", "Settings", "Recorder", "Clients", "Memory", "Layout", "Anti-AFK", "Notifications", "System", "Logs"],
          app.view_titles)
    app._switch_view(1)
    check("switching sets the page title",
          app.lbl_page_title.cget("text") == "Slots & Coordinates")
    check("macro tool reachable by id", app.tool("macro") is not None)
    check("macro runners get the shared hotkeys",
          app.tool("macro").ctx.hotkeys is app.ctx.hotkeys)

    # 3. Constitution VI: every macro tool shows a live feed of the client.
    from macro_studio.core.live_feed import LiveFeed
    macro_tools = [t for t in app._tools if getattr(t, "is_macro_tool", False)]
    check("at least one macro tool is registered", bool(macro_tools))
    for tool in macro_tools:
        name = type(tool).__name__
        feed = getattr(tool, "live_feed", None)
        check(f"{name} has a live feed", isinstance(feed, LiveFeed), feed)
        if isinstance(feed, LiveFeed):
            check(f"{name} feed runs on the shared scheduler at >= 2 per second",
                  app.ctx.scheduler.next_run(feed.name) is not None
                  and feed.interval_s <= 0.5)
    feed = app.tool("macro").live_feed
    app._switch_view(2)
    check("feed pauses while its page is hidden", feed.visible is False)
    app._switch_view(0)
    check("feed resumes on the Overview page", feed.visible is True)
    macro = app.tool("macro")

    # 4. Per-slot preview cards are gone; the live feed is the only vision view.
    check("no per-slot preview cards", not hasattr(macro, "slot_cards_inner")
          and not getattr(macro, "slot_preview_widgets", {}))
    try:
        macro._update_slot_frame(1, None, "EMPTY_SLOT", "scan")  # runners still call it
        root.update()
        check("slot frame callback is harmless", True)
    except Exception as e:
        check("slot frame callback is harmless", False, e)

    # 5. Pop-out: the feed moves to its own window and the main window shrinks.
    root.deiconify()
    root.geometry("1180x880")
    root.update()
    before_h = root.winfo_height()
    macro.pop_out_feed()
    root.update()
    popup = macro.feed_window
    check("feed opens in its own window",
          popup is not None and bool(popup.winfo_exists()), popup)
    check("in-page feed section is hidden", not macro.vision_card.winfo_ismapped())
    check("main window shrinks", root.winfo_height() < before_h,
          (before_h, root.winfo_height()))
    app._switch_view(2)
    check("popped-out feed keeps running on other pages", feed.visible is True)
    app._switch_view(0)
    macro.dock_feed()
    root.update()
    check("dock closes the feed window", macro.feed_window is None)
    check("dock brings the feed section back", macro.vision_card.winfo_ismapped())
    check("main window size restored", root.winfo_height() >= before_h - 2,
          (before_h, root.winfo_height()))
    macro.pop_out_feed()
    root.update()
    macro.feed_window.destroy()   # closing the window by its X also docks
    root.update()
    check("closing the feed window docks it", macro.feed_window is None
          and macro.vision_card.winfo_ismapped())

    # 6. Tabs dropdown: every client with its live screen; ticks limit the macro.
    from PIL import Image
    from macro_studio.core.tab_selection import TabInfo
    macro._snapshot_tabs = lambda: [
        TabInfo(11, 9011, "Roblox A", Image.new("RGB", (800, 600), (200, 40, 40))),
        TabInfo(12, 9012, "Roblox B", None)]
    check("tabs button starts on all", macro._tabs_label() == "Tabs: All",
          macro._tabs_label())
    macro.toggle_tab_picker()
    picker = macro.tab_picker
    picker.show_tabs(macro._snapshot_tabs(), minimized=1)
    root.update()
    check("dropdown lists every client", len(picker._cards) == 2, picker._cards)
    check("dropdown shows each client's screen",
          all(img.image is not None for _, img, _ in picker._cards.values()))
    check("minimized clients are explained", "minimized" in picker.status.cget("text"),
          picker.status.cget("text"))
    picker.toggle(9012)
    root.update()
    check("unticking a tab limits the macro to the rest",
          macro.tab_selection.filter([11, 12], {11: 9011, 12: 9012}.get) == [11])
    check("runner gets the picker's selection",
          macro._tabs_label() == "Tabs: 1 selected", macro._tabs_label())
    picker.toggle(9011)
    check("no tabs blocks Start", "No tabs" in (macro._preflight_check() or ""))
    picker.select_all()
    check("select all goes back to every tab", macro.tab_selection.all_tabs)
    macro.toggle_tab_picker()
    root.update()
    check("pressing Tabs again closes the dropdown", picker.closed
          and app.ctx.scheduler.next_run(picker.JOB) is None)

    loaded = macro.current_cfg
    try:
        for profile in ("Goliath", "Mutation"):
            macro.current_cfg = macro.pm.load_profile(profile)  # in memory only
            overlay = macro._feed_overlay()
            check(f"{profile} feed draws calibrated regions and points",
                  bool(overlay.regions) and bool(overlay.points), overlay)
    finally:
        macro.current_cfg = loaded
    app.shutdown()
    root.destroy()

    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_shell_builds_registered_tools(tmp_base_dir):
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
