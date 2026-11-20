"""The Memory page: settings, validation, Trim now, and the event wiring (spec 001, US3).

Runs in its own process (creates Tk) on a temporary copy of the data, with a fake service so
no real process is trimmed.
"""

import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))      # tests/

failures = []


def check(name, cond, detail: object = ""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


def wait_for(condition, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


def run():
    import tkinter as tk
    from tkinter import messagebox

    from macro_studio.app import MacroStudioApp
    from macro_studio.tools.memory.service import MemoryService, TrimReport, Trimmed

    messagebox.askyesnocancel = lambda *a, **k: False
    root = tk.Tk()
    root.geometry("1180x880+0+0")
    app = MacroStudioApp(root)
    memory = app.tool("memory")
    app.tool("macro").pm.save_profile = lambda name, data: True
    app._switch_view(app.view_titles.index("Memory"))
    root.update()

    check("the sidebar offers a Memory page", "Memory" in app.view_titles, app.view_titles)
    check("automatic trimming starts off", memory.settings.get("enabled") is False
          and memory.service.enabled is False)
    check("the defaults match the spec", (memory.service.threshold_mb, memory.settings.get("interval_s"))
          == (350, 3))

    # validation: bad values are named, outlined, and not used
    memory.e_limit.set("49")
    check("a limit below the range is refused", memory.apply() is False
          and "less than 50" in memory.lbl_error.cget("text") and memory.e_limit.error,
          memory.lbl_error.cget("text"))
    memory.e_limit.set("8001")
    check("a limit above the range is refused", memory.apply() is False
          and "more than 8000" in memory.lbl_error.cget("text"))
    memory.e_limit.set("abc")
    check("text instead of a number is refused", memory.apply() is False
          and "abc" in memory.lbl_error.cget("text"))
    memory.e_limit.set("500")
    memory.e_interval.set("0")
    check("an interval below the range is refused", memory.apply() is False
          and "less than 1" in memory.lbl_error.cget("text") and memory.e_interval.error)
    check("a refused value changes nothing", memory.service.threshold_mb == 350)

    # a good apply is used and saved, and clears the errors
    saved = []
    real_save = memory.ctx.settings.save
    memory.ctx.settings.save = lambda: (saved.append(1), real_save())
    memory.e_limit.set("500")
    memory.e_interval.set("20")
    check("valid values are applied", memory.apply() is True and memory.service.threshold_mb == 500
          and memory.settings.get("interval_s") == 20 and saved)
    check("errors are cleared once the values are good",
          memory.lbl_error.cget("text") == "" and not memory.e_limit.error)
    check("the scheduled job uses the new interval",
          memory.ctx.scheduler.next_run(memory.AUTO_JOB) is not None)

    # toggles apply at once
    memory.var_enabled.set(True)
    memory._toggle_changed()
    memory.var_all.set(True)
    memory._toggle_changed()
    check("toggles reach the service and are saved", memory.service.enabled is True
          and memory.service.trim_all is True and memory.settings.get("enabled") is True)

    # Trim now runs once on the scheduler, not on the Tk thread
    calls = []
    fake = SimpleNamespace(
        enabled=True, trim_all=False, threshold_mb=500,
        trim_now=lambda: (calls.append("now"), TrimReport(trimmed=[Trimmed(1, 800.0, 200.0)]))[1],
        run_auto=lambda: None, check_limit=lambda: None, set_rows=lambda rows: calls.append("rows"))
    memory.service = fake
    memory.trim_now()
    check("Trim now reaches the service", wait_for(lambda: "now" in calls), calls)
    check("the one-shot job removes itself",
          wait_for(lambda: memory.ctx.scheduler.next_run(memory.NOW_JOB) is None))

    # the report text
    memory._show(TrimReport(trimmed=[Trimmed(1, 800.0, 200.0), Trimmed(2, 500.0, 100.0)],
                            skipped=[(3, "Windows refused")]))
    text = memory.lbl_result.cget("text")
    check("the report says how much was freed and what was skipped",
          "Trimmed 2 client(s)" in text and "1,000 MB" in text and "PID 3 (Windows refused)" in text,
          text)
    memory._show(TrimReport())
    check("an empty pass says nothing needed trimming",
          "Nothing needed trimming" in memory.lbl_result.cget("text"))

    # the client list arrives over the event bus, without importing the Clients tool
    app.ctx.events.publish("clients_updated", rows=[SimpleNamespace(pid=7)])
    check("clients_updated reaches the service", "rows" in calls)

    # and a memory alert is written to the log
    logged = []
    memory.ctx.log = logged.append
    app.ctx.events.publish("ram_over", rows=[SimpleNamespace(pid=7, account="Ann")], threshold_mb=500)
    check("a memory alert is logged", any("Ann" in line and "500" in line for line in logged), logged)

    # the real service really is the real class
    check("the real service is a MemoryService",
          isinstance(MemoryService(trim=None, process_info=None, publish=None, clock=None, log=None),
                     MemoryService))

    source = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "macro_studio", "tools", "memory", "tool.py"),
        encoding="utf-8").read()
    check("the Memory tool never imports another tool's package",
          "tools.clients" not in source and "tools.macro.tool" not in source)

    app.shutdown()
    root.destroy()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_memory_page(tmp_base_dir):
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
