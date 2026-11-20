"""The glass really is on when the app starts the way main() starts it (own process).

An earlier version applied the look before the window was on screen; Windows ignored it, and
every test passed because they all called update() first. This one does not.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))      # tests/

failures = []


def check(name, cond, detail: object = ""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


def run():
    import tkinter as tk

    from macro_studio.app import MacroStudioApp
    from macro_studio.core import glass

    root = tk.Tk()                                 # exactly as main(): no update() before the loop
    app = MacroStudioApp(root)
    for job in ("macro:claim_timers", "live_feed:macro", "memory:auto", "clients:scan"):
        app.ctx.scheduler.remove(job)
    deadline = time.monotonic() + 6

    def poll():
        if glass.current_backdrop(root) in (2, 3) or time.monotonic() > deadline:
            root.quit()
        else:
            root.after(100, poll)
    root.after(100, poll)
    root.mainloop()
    got = glass.current_backdrop(root)
    if glass.glass_supported():
        check("the glass backdrop is on after a normal start-up", got == 3, got)
    else:
        check("an old Windows keeps the solid look", got in (None, 1), got)
    app.shutdown()
    root.destroy()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_glass_on_at_startup(tmp_base_dir):
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
