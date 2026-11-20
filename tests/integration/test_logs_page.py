"""The Logs page: lines arrive from the event bus, filter, clear, copy, export, file (US7).

Runs in its own process (creates Tk) on a temporary copy of the data.
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


def run():
    import threading
    import tkinter as tk
    from tkinter import messagebox

    from macro_studio.app import MacroStudioApp

    messagebox.askyesnocancel = lambda *a, **k: False
    root = tk.Tk()
    root.geometry("1180x880+0+0")
    PUMP.append(root)
    app = MacroStudioApp(root)
    logs = app.tool("logs")
    app.tool("macro").pm.save_profile = lambda name, data: True
    app._switch_view(app.view_titles.index("Logs"))
    root.update()

    def shown():
        return logs.text.get("1.0", "end").strip()

    check("the sidebar offers a Logs page", "Logs" in app.view_titles)

    app.ctx.log("[CLIENTS] A Roblox client opened (Ann).")
    app.ctx.log("[JOB] 'live_feed:macro' took 1.1 s")
    check("lines from any tool appear", wait_for(lambda: "client opened (Ann)" in shown()), shown())
    check("with a time in front", wait_for(lambda: ":" in shown().split("\n")[0][:8]), shown()[:20])

    logs.var_game.set(True)
    logs._filter_changed()
    check("'game activity only' hides the studio's chatter", wait_for(
        lambda: "[JOB]" not in shown() and "client opened" in shown()), shown())
    logs.var_game.set(False)
    logs._filter_changed()
    check("and showing everything brings it back", "[JOB]" in shown(), shown())
    check("the choice is remembered", logs.settings.get("game_only") is False)

    # lines logged from other threads
    def spam():
        for i in range(50):
            app.ctx.log(f"[AFK] from a worker {i}")
    worker = threading.Thread(target=spam)
    worker.start()
    worker.join()
    check("lines from worker threads arrive", wait_for(lambda: "from a worker 49" in shown()))

    # copy and export
    logs.copy()
    check("Copy puts the visible text on the clipboard", "client opened (Ann)" in root.clipboard_get())
    path = logs.export()
    check("Export writes a file with the log", path and os.path.isfile(path)
          and "client opened (Ann)" in open(path, encoding="utf-8").read(), path)
    check("the export says where it went", path and path in logs.lbl_note.cget("text"))

    # file logging
    logs.var_file.set(True)
    logs._file_changed()
    app.ctx.log("[MEMORY] written to the file")
    check("file logging appends new lines", wait_for(lambda: os.path.isfile(logs.file_path)
          and "written to the file" in open(logs.file_path, encoding="utf-8").read()), logs.file_path)
    logs.var_file.set(False)
    logs._file_changed()
    app.ctx.log("[MEMORY] not written")
    time.sleep(0.1)
    check("and stops when switched off", "not written" not in open(logs.file_path, encoding="utf-8").read())

    # clear
    logs.clear()
    check("Clear empties the page", shown() == "", shown()[:40])

    # the page redraws only when something changed
    version = logs._shown
    logs.refresh()
    check("an idle refresh changes nothing", logs._shown == version)

    # open folder uses the OS opener on the data folder only
    opened = []
    os.startfile = lambda target, *a, **k: opened.append(target)
    logs.open_folder()
    check("Open folder opens the data folder", len(opened) == 1 and os.path.isdir(opened[0]))

    logs.stop()
    app.ctx.log("[CLIENTS] after stop")
    time.sleep(0.1)
    check("after stopping, nothing more is collected", "after stop" not in logs.buffer.text())

    app.shutdown()
    root.destroy()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_logs_page(tmp_base_dir):
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
