"""The window never freezes while background work runs (spec 002, A2, finding H3).

Runs the real app with a window manager whose enumerations are slow (0.4 s, like a busy PC),
no client open, and measures the longest gap in the Tk event loop. Enumeration belongs on the
scheduler thread; if any of it runs on the Tk thread the gap is at least 0.4 s.
Runs in its own process because it creates a Tk root.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/

failures = []
LIMIT = 0.25            # the budget is 0.2 s (SC-A03); a little slack for a loaded test machine
SLOW = 0.4


def check(name, cond, detail: object = ""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


def worst_gap(root, seconds):
    """Longest gap between 10 ms ticks of a real mainloop.

    Worker threads can only reach Tk while the main thread is inside mainloop(), so the
    probe runs there. Anything slow on the Tk thread shows up as a long gap between ticks.
    """
    state = {"last": time.monotonic(), "worst": 0.0}

    def tick():
        now = time.monotonic()
        state["worst"] = max(state["worst"], now - state["last"])
        state["last"] = now
        root.after(10, tick)

    root.after(10, tick)
    root.after(int(seconds * 1000), root.quit)
    root.mainloop()
    return state["worst"]


def run():
    import tkinter as tk

    from conftest import FakeWM
    from macro_studio.app import MacroStudioApp

    root = tk.Tk()
    root.geometry("1180x880+0+0")
    app = MacroStudioApp(root)
    m = app.tool("macro")
    m.pm.save_profile = lambda name, data: True
    root.update()

    # No client open, one minimized: the feed has to say so, and finding that out is slow.
    wm = FakeWM(windows={5: {"pid": 55, "title": "Roblox", "minimized": True, "hidden": True}},
                enum_delay=SLOW)
    m._feed_wm = wm
    m._feed_windows_at = 0.0
    worst = worst_gap(root, 5.0)
    check("slow window enumeration never freezes the window", worst < LIMIT, round(worst, 3))
    check("the slow enumeration really ran (the test is not vacuous)",
          any(name in ("find_windows", "count_minimized_clients") for name, _a in wm.calls), wm.calls)

    shown = m.live_view.caption.cget("text")
    check("the feed still explains that a client is minimized", "minimized" in shown, shown)

    app.shutdown()
    root.destroy()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_window_does_not_freeze():
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
