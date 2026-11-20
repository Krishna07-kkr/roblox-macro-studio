"""The System page: hotkeys, startup, window options, text size (spec 001, US7).

Runs in its own process (creates Tk) on a temporary copy of the data. Autostart is a fake, so
the real startup entry is never touched.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))      # tests/

failures = []
PUMP = []


def check(name, cond, detail: object = ""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


def wait_for(condition, timeout=5.0):
    if condition():
        return True
    root = PUMP[0]
    deadline = time.monotonic() + timeout

    def poll():
        if condition() or time.monotonic() > deadline:
            root.quit()
        else:
            root.after(20, poll)
    root.after(20, poll)
    root.mainloop()
    return bool(condition())


class FakeAutostart:
    def __init__(self):
        self.on, self.allow = False, True

    def is_enabled(self):
        return self.on

    def enable(self):
        if self.allow:
            self.on = True
        return self.allow

    def disable(self):
        if self.allow:
            self.on = False
        return self.allow


def run():
    import tkinter as tk
    from tkinter import messagebox

    from macro_studio.app import MacroStudioApp
    from macro_studio.core import ui_theme as ui

    messagebox.askyesnocancel = lambda *a, **k: False
    root = tk.Tk()
    root.geometry("1180x880+0+0")
    PUMP.append(root)
    app = MacroStudioApp(root)
    system = app.tool("system")
    app.tool("macro").pm.save_profile = lambda name, data: True
    fake = FakeAutostart()
    system.autostart = fake
    app._switch_view(app.view_titles.index("System"))
    root.update()

    check("the sidebar offers a System page", "System" in app.view_titles)
    check("the four actions are registered by their tools", set(app.ctx.actions) >= {
        "toggle_afk", "optimize", "arrange_grid", "toggle_panel"}, sorted(app.ctx.actions))
    check("F1 to F4 are bound by default", system.bindings.keys == {
        "toggle_afk": "F1", "optimize": "F2", "arrange_grid": "F3", "toggle_panel": "F4"},
          system.bindings.keys)

    # rebinding
    system.fields["optimize"].set("F3")
    check("a key another action uses is refused and that action is named",
          system.apply_keys() is False and "arrange_grid" in system.lbl_keys.cget("text")
          and system.fields["optimize"].error, system.lbl_keys.cget("text"))
    check("and the old key stays", system.bindings.keys["optimize"] == "F2")
    system.fields["optimize"].set("F6")
    check("F6 is refused", system.apply_keys() is False and "reserved" in system.lbl_keys.cget("text"),
          system.lbl_keys.cget("text"))
    system.fields["optimize"].set("Esc")
    check("Esc is refused", system.apply_keys() is False)
    system.fields["optimize"].set("banana")
    check("an unknown key is named", system.apply_keys() is False
          and "banana" in system.lbl_keys.cget("text"))
    system.fields["optimize"].set("F9")
    check("a free key is accepted and remembered", system.apply_keys() is True
          and system.bindings.keys["optimize"] == "F9"
          and system.settings.get("hotkeys")["optimize"] == "F9")
    system.reset_keys()
    check("reset puts the defaults back", system.bindings.keys["optimize"] == "F2"
          and system.fields["optimize"].get() == "F2")

    # the F-keys really run the tools' actions
    mem = app.tool("memory")
    calls = []
    from macro_studio.tools.memory.service import TrimReport
    mem.service = type("S", (), {"trim_now": lambda self: (calls.append(1), TrimReport())[1],
                                 "enabled": False, "check_limit": lambda s: None,
                                 "run_auto": lambda s: None, "set_rows": lambda s, r: None})()
    app.ctx.hotkeys._bindings["optimize"].callback()
    check("pressing the Trim key runs a memory trim", wait_for(lambda: bool(calls)), calls)

    # F4 minimises and restores
    system.toggle_panel()
    root.update()
    check("the panel key minimises the window", root.state() == "iconic", root.state())
    system.toggle_panel()
    root.update()
    check("and brings it back", root.state() == "normal", root.state())

    # switches
    system.var_autostart.set(True)
    system._autostart_changed()
    check("Start with Windows is turned on", fake.on is True and system.lbl_window.cget("text") == "")
    fake.allow = False
    system.var_autostart.set(False)
    system._autostart_changed()
    check("a refusal switches it back and says so", system.var_autostart.get() is True
          and "would not change" in system.lbl_window.cget("text"))
    fake.allow = True
    system.var_top.set(True)
    system._top_changed()
    check("always on top applies to the window", bool(root.attributes("-topmost"))
          and system.settings.get("always_on_top") is True)
    system.var_min.set(True)
    system._min_changed()
    check("start minimised is remembered", system.settings.get("start_minimised") is True)

    # Liquid Glass
    from macro_studio.core.glass import glass_supported
    check("Liquid Glass starts on, as Frosted", system.settings.get("glass") is True
          and system.settings.get("glass_style") == "frosted" and system.btn_glass._text
          == ("Frosted" if glass_supported() else "Needs Windows 11 22H2"), system.btn_glass._text)
    system.set_glass(True, "frosted")
    check("Frosted can be chosen and is remembered", system.settings.get("glass_style") == "frosted"
          and (system.btn_glass._text == "Frosted" or not glass_supported()), system.btn_glass._text)
    system.set_glass(False, "soft")
    check("and Off turns it off", system.settings.get("glass") is False
          and (system.btn_glass._text == "Off" or not glass_supported()), system.btn_glass._text)
    system.set_glass(True, "soft")

    # text size
    size_in_use = ui.sans(14)[1]
    check("the studio starts at the bigger default text size", ui.get_text_scale() == ui.DEFAULT_TEXT_SCALE,
          ui.get_text_scale())
    system.set_text_scale(125)
    check("the text size is saved and explained", system.settings.get("text_scale") == 125
          and "next time" in system.lbl_window.cget("text") and system.btn_scale._text == "125%")
    system.set_text_scale(80)
    check("a size that is not offered becomes 100", system.settings.get("text_scale") == 100)
    check("the running window keeps its size until restart", ui.sans(14)[1] == size_in_use)

    # the tray: close hides to it, its menu reuses the actions, Exit really quits
    check("the tray icon is up", system.tray is not None and system.tray.hwnd)
    check("its menu has the everyday actions and Exit", system.tray.menu_labels() == [
        "Show or hide", "Turn anti-AFK on or off", "Trim memory now",
        "Arrange windows in a grid", "Exit"], system.tray.menu_labels())
    check("closing the window hides it to the tray by default", system.intercept_close() is True
          and root.state() == "withdrawn", root.state())
    system.toggle_panel()
    root.update()
    check("the panel key brings it back from the tray", root.state() == "normal", root.state())
    system.var_tray.set(False)
    system._tray_changed()
    check("with that switched off the close button really closes", system.intercept_close() is False
          and root.state() == "normal")
    system.var_tray.set(True)
    system._tray_changed()
    quit_calls = []
    app.quit_now = lambda: quit_calls.append(1)
    system.tray.run_command(5)
    check("the tray's Exit asks the shell to quit for real", wait_for(lambda: bool(quit_calls)),
          quit_calls)
    app.ctx.events.publish("clients_updated", rows=[type("R", (), {"ram_mb": 400.0})(),
                                                     type("R", (), {"ram_mb": 600.0})()])
    check("the tooltip shows the client count and memory", wait_for(
        lambda: "2 clients, 1,000 MB" in system.tray.tooltip), system.tray.tooltip)

    class NoTray:
        hwnd = 0

        def start(self):
            return False
    logged = []
    app.ctx.events.subscribe("log", lambda message, **k: logged.append(message))
    spare = system.tray
    system.tray = None
    system._start_tray(tray=NoTray())
    check("if the tray cannot be created the window stays on the taskbar and says so",
          system.tray is None and any("tray icon could not be created" in m for m in logged)
          and system.intercept_close() is False)
    system.tray = spare

    # stopping releases everything
    system.stop()
    check("stopping unbinds the keys and removes its action", "toggle_panel" not in app.ctx.actions
          and "optimize" not in app.ctx.hotkeys._bindings)
    check("and takes the tray icon away", system.tray is None)

    app.shutdown()
    root.destroy()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_system_page(tmp_base_dir):
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
