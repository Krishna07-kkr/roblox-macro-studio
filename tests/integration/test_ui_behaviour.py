"""Macro Studio UI behaviour from the design review (2026-11-20).

Runs in its own process because it creates a Tk root (see conftest.run_in_fresh_python).
Profiles are never written: save_profile is replaced by a recorder before anything runs.
"""

import sys
import threading

failures = []


def check(name, cond, detail: object = ""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


def run():
    import tkinter as tk
    from tkinter import messagebox

    from macro_studio.app import MacroStudioApp
    from macro_studio.core.ui_theme import Button, Toggle

    dialogs = []
    messagebox.showerror = lambda t, m, **k: dialogs.append(("error", t, m))
    messagebox.showwarning = lambda t, m, **k: dialogs.append(("warning", t, m))
    messagebox.showinfo = lambda t, m, **k: dialogs.append(("info", t, m))

    root = tk.Tk()
    root.geometry("1180x880+0+0")
    app = MacroStudioApp(root)
    m = app.tool("macro")
    saved = []
    m.pm.save_profile = lambda name, data: saved.append(name) or True
    root.update()

    # 1. Buttons: press, drag off, release cancels; keyboard works; focusable.
    fired = []
    b = Button(root, "probe", command=lambda: fired.append(1))
    b.place(x=600, y=4)
    root.update()
    b.event_generate("<ButtonPress-1>", x=5, y=5)
    b.event_generate("<ButtonRelease-1>", x=400, y=400)
    check("drag off a button and release does nothing", fired == [])
    b.event_generate("<ButtonPress-1>", x=5, y=5)
    b.event_generate("<ButtonRelease-1>", x=6, y=6)
    check("a normal click fires", fired == [1], fired)
    b.focus_force()
    root.update()
    b.event_generate("<Return>")
    b.event_generate("<space>")
    check("Enter and Space press a focused button", fired == [1, 1, 1], fired)
    b.set_state("disabled")
    # Call the key handler directly: event_generate sends the key to whichever widget has
    # focus, and once the probe is disabled that is a real control (the profile menu
    # button, whose popup menu blocks the test forever when the window is in front).
    b._on_key(None)
    b.invoke()
    check("a disabled button ignores keys and leaves the Tab order",
          fired == [1, 1, 1] and str(b.cget("takefocus")) == "0")
    b.destroy()

    var = tk.BooleanVar(value=False)
    t = Toggle(root, var)
    t.place(x=600, y=40)
    root.update()
    t.focus_force()
    root.update()
    t.event_generate("<space>")
    check("Space flips a focused switch", var.get() is True)
    t.event_generate("<ButtonPress-1>", x=3, y=3)
    t.event_generate("<ButtonRelease-1>", x=300, y=300)
    check("dragging off a switch cancels", var.get() is True)
    t.destroy()

    # 2. Tab reaches the controls and the console no longer traps focus.
    app._switch_view(0)
    root.update()
    m.btn_start.focus_force()
    root.update()
    order = []
    w = m.btn_start
    for _ in range(12):
        w = w.tk_focusNext()
        order.append(w)
    check("Tab moves from Start through the other controls",
          m.btn_tabs in order and m.btn_test in order, [str(x) for x in order])
    check("console is read-only", str(m.log_text.cget("state")) == "disabled")
    m.log_text.focus_force()
    root.update()
    m.log_text.event_generate("<Tab>")
    root.update()
    check("Tab leaves the console", root.focus_get() is not m.log_text, root.focus_get())
    m._append_log("still logs while read-only")
    root.update()
    check("macro can still write to the console",
          "still logs while read-only" in m.log_text.get("1.0", "end"))

    # 2b. A long run must not grow the console without limit (spec 002 A1, finding H13).
    m.LOG_MAX_LINES = 50
    for i in range(300):
        m._append_log(f"line {i}")
    root.update()
    shown = int(m.log_text.index("end-1c").split(".")[0])
    check("the console keeps at most its line limit", shown <= 51, shown)
    text = m.log_text.get("1.0", "end")
    check("the newest lines are kept, the oldest dropped",
          "line 299" in text and "line 0\n" not in text)

    # 3. The console is on screen at the default window size.
    root.update()
    bottom = root.winfo_rooty() + root.winfo_height()
    check("console starts inside the default window",
          m.console_card.winfo_rooty() + 120 < bottom,
          (m.console_card.winfo_rooty(), bottom))
    check("run status sits with the Start button",
          m.status_pill.master is m.btn_start.master)

    # 4. Slots: ids never repeat; typed edits survive add/delete.
    if m._cfg_mode() == "goliath" and len(m.current_cfg.get("slots", [])) >= 2:
        m.slot_ui_rows[1]["e_sc_x"].set(431)
        m._delete_slot_entry(0)
        m._add_slot_entry()
        ids = [s["id"] for s in m.current_cfg["slots"]]
        check("slot ids stay unique after delete + add", len(ids) == len(set(ids)), ids)
        check("an edit in another card survives delete + add",
              any(s["slot_click"]["x"] == 431 for s in m.current_cfg["slots"]))
        check("slot edits mark the profile unsaved", m.dirty
              and m.btn_save._kind == "primary")

        # 5. Bad values are refused with the field named and outlined.
        dialogs.clear()
        m.e_d_cycle.set("-5")
        ok = m._sync_ui_to_config()
        check("a negative round delay is refused", ok is False)
        check("the message names the field", dialogs and "Round delay" in dialogs[-1][2],
              dialogs)
        check("the bad field is outlined", bool(m.e_d_cycle.error))
        check("the page with the bad field is shown",
              app.view_titles[app.current_view_idx] == "Settings")
        m.e_d_cycle.set("6.0")
        m.e_d_cycle.set_error(None)
        dialogs.clear()
        m.slot_ui_rows[0]["e_sr_w"].set("wide")
        check("text in a coordinate is refused", m._sync_ui_to_config() is False)
        check("coordinate message names the slot and field",
              dialogs and "scan region width" in dialogs[-1][2], dialogs)
        m.slot_ui_rows[0]["e_sr_w"].set(142)

        # 6. Saving: clean afterwards, and a failed save is reported, not hidden.
        saved.clear()
        dialogs.clear()
        m._save_active_profile()
        check("save writes once, with no success dialog",
              saved == [m.active_profile_name] and not dialogs, (saved, dialogs))
        check("saved profile is clean again", not m.dirty and m.btn_save._kind == "secondary")

        def broken(name, data):
            raise OSError("disk full")
        m.pm.save_profile = broken
        dialogs.clear()
        logged = []
        m._append_log = logged.append
        check("a failed save reports failure", m._sync_ui_to_config() is False)
        check("the failure is shown and logged",
              dialogs and "disk full" in dialogs[-1][2] and any("disk full" in x for x in logged),
              (dialogs, logged))
        del m._append_log
        m.pm.save_profile = lambda name, data: saved.append(name) or True

    # 7. Stop says "Stopping" until the worker thread has really ended.
    release = threading.Event()
    worker = threading.Thread(target=release.wait, daemon=True)
    worker.start()
    m.runner_thread, m.is_running = worker, True
    m.btn_stop.set_state("normal")
    m.stop_macro()
    check("Stop shows Stopping while the run winds down",
          m.status_pill.text.upper() == "STOPPING" and m.is_running, m.status_pill.text)
    release.set()
    worker.join()
    m._on_runner_stopped()
    check("then Idle", m.status_pill.text.upper() == "IDLE")
    m.runner_thread = None

    # 7b. Stop pressed while the runner is still being built: the run must never start
    # (spec 002 A2, finding H10).
    import macro_studio.tools.macro.tool as tool_mod
    gate, built, ran = threading.Event(), threading.Event(), []

    class SlowRunner:
        stats = {}
        running = True

        def __init__(self, **kwargs):
            built.set()
            gate.wait(5)               # "building" takes a while

        def stop(self):
            self.running = False

        def run(self):
            ran.append(1)

    real_runner_cls = tool_mod.MacroRunner
    tool_mod.MacroRunner = SlowRunner
    saved_sync, saved_pre = m._sync_ui_to_config, m._preflight_check
    m._sync_ui_to_config = lambda: True
    m._preflight_check = lambda: None
    try:
        m.start_macro()
        built.wait(5)
        m.stop_macro()                 # Stop arrives mid-construction
        gate.set()
        m.runner_thread.join(5)
        check("Stop during construction means run() never starts", ran == [] and
              not m.runner_thread.is_alive(), ran)
    finally:
        gate.set()
        tool_mod.MacroRunner = real_runner_cls
        m._sync_ui_to_config, m._preflight_check = saved_sync, saved_pre
        m._on_runner_stopped()
        m.runner = None
        m.runner_thread = None

    # 8. Pause keeps its hotkey hint.
    class _R:
        paused = False

        def toggle_pause(self):
            self.paused = not self.paused
    m.runner = _R()
    m.toggle_pause()
    check("Resume keeps the F7 hint", "F7" in m.btn_pause._text, m.btn_pause._text)
    m.runner = None

    # 9. Menus look like menus; Mode is part of the profile switcher.
    check("Tabs and profile buttons show a menu arrow",
          m.btn_tabs._text.endswith("▾") and m.btn_profile._text.endswith("▾"))
    check("no separate Mode button", not hasattr(m, "btn_mode"))

    # 10. Pop-out opens beside the main window and leaves it where it was.
    root.update()
    x_before = root.winfo_x()
    m.pop_out_feed()
    root.update()
    win = m.feed_window
    beside = (win.winfo_rootx() >= root.winfo_rootx() + root.winfo_width()
              or win.winfo_rootx() + win.winfo_width() <= root.winfo_rootx())
    check("feed window does not cover the main window", beside or
          root.winfo_rootx() + root.winfo_width() + 900 > root.winfo_screenwidth(),
          (win.winfo_rootx(), root.winfo_rootx(), root.winfo_width()))
    check("main window stays put when it shrinks", abs(root.winfo_x() - x_before) <= 2,
          (x_before, root.winfo_x()))
    m.dock_feed()

    # 11. Closing with unsaved changes asks first; Cancel keeps the app open.
    m._mark_dirty()
    messagebox.askyesnocancel = lambda *a, **k: None
    check("cancel on close keeps the app open", m.confirm_close() is False)
    messagebox.askyesnocancel = lambda *a, **k: False
    check("don't save lets it close", m.confirm_close() is True and not m.dirty)

    app.shutdown()
    root.destroy()

    # 12. A worker thread that reports after the window is gone must not raise (spec 002 H9).
    try:
        m._append_log("late line from a worker thread")
        m._update_stats({"accounts": 1, "cycles": 1, "claims": 0, "adds": 0, "active_hwnd": 0})
        late_ok = True
    except Exception as err:        # noqa: BLE001 - the check wants any failure
        late_ok, detail = False, err
    check("logging and stats after the window is destroyed raise nothing", late_ok,
          None if late_ok else detail)
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_ui_behaviour(tmp_base_dir):
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
