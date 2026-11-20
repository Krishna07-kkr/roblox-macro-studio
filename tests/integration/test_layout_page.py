"""The Layout page: validation, the arrange actions, and the multi-instance switch (US4).

Runs in its own process (creates Tk) on a temporary copy of the data, with fakes, so no real
window is moved and no real lock is taken.
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


PUMP = []          # the Tk root; worker threads can only reach Tk while it is being serviced


def wait_for(condition, timeout=5.0):
    """Wait for `condition`, running a real mainloop meanwhile.

    A worker thread can only reach Tk (ui_call) while the main thread is inside mainloop();
    during update() the call waits a second and fails, which would hide label updates.
    """
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
    import tkinter as tk
    from tkinter import messagebox

    from macro_studio.app import MacroStudioApp
    from macro_studio.tools.layout.service import Result

    notices = []
    answer = {"ok": False}
    messagebox.askokcancel = lambda title, text, **k: (notices.append(text), answer["ok"])[1]
    messagebox.askyesnocancel = lambda *a, **k: False

    class FakeService:
        def __init__(self):
            self.calls, self.busy = [], False

        def arrange(self, style, offset, size, monitor):
            self.calls.append(("arrange", style, offset, size, monitor))
            return Result(False, "A macro is running.") if self.busy else Result(True)

        def minimise_all(self):
            self.calls.append(("minimise",))
            return Result(True)

        def restore_all(self):
            self.calls.append(("restore",))
            return Result(True)

    class FakeMulti:
        def __init__(self):
            self.active, self.allow, self.log = False, True, []

        def enable(self):
            self.log.append("enable")
            if not self.allow:
                return Result(False, "Close every Roblox client first.")
            self.active = True
            return Result(True)

        def disable(self):
            self.log.append("disable")
            self.active = False

    root = tk.Tk()
    root.geometry("1180x880+0+0")
    PUMP.append(root)
    app = MacroStudioApp(root)
    layout = app.tool("layout")
    app.tool("macro").pm.save_profile = lambda name, data: True
    # Without a real mainloop each periodic job of the other tools waits ~1 s in ui_call and
    # would delay the one-shot jobs under test; this test is only about the Layout page.
    for other in ("macro:claim_timers", "live_feed:macro", "memory:auto", "clients:scan"):
        app.ctx.scheduler.remove(other)
    app._switch_view(app.view_titles.index("Layout"))
    root.update()
    service, multi = FakeService(), FakeMulti()
    layout.service, layout.multi = service, multi

    check("the sidebar offers a Layout page", "Layout" in app.view_titles, app.view_titles)
    check("defaults match the spec", (layout.settings.get("offset_x"), layout.settings.get("width"),
                                      layout.settings.get("height")) == (30, 800, 600))

    # validation
    layout.e_w.set("100")
    layout.arrange("grid")
    check("a width below the range is refused and named",
          "Width" in layout.lbl_result.cget("text") and layout.e_w.error and service.calls == [],
          layout.lbl_result.cget("text"))
    layout.e_w.set("800")
    layout.e_ox.set("abc")
    layout.arrange("grid")
    check("text instead of a number is refused", layout.e_ox.error and service.calls == [])
    layout.e_ox.set("30")

    # arrange goes to the service with the typed values, on the scheduler
    layout.e_w.set("900")
    layout.e_h.set("700")
    layout.arrange("cascade")
    check("arrange reaches the service with the typed values",
          wait_for(lambda: ("arrange", "cascade", (30, 30), (900, 700), 0) in service.calls),
          service.calls)
    check("the values are saved", layout.settings.get("width") == 900)
    layout.minimise_all()
    layout.restore_all()
    check("minimise and restore reach the service",
          wait_for(lambda: ("minimise",) in service.calls) and wait_for(
              lambda: ("restore",) in service.calls), service.calls)

    # multi-instance: the notice comes first, once
    layout.var_multi.set(True)
    layout.toggle_multi()
    check("the risk notice is shown the first time", len(notices) == 1 and "Terms of Use" in notices[0])
    check("declining the notice leaves it off", multi.log == [] and not layout.var_multi.get())
    answer["ok"] = True
    layout.var_multi.set(True)
    layout.toggle_multi()
    check("accepting turns it on and remembers the notice", multi.active
          and layout.settings.get("notice_shown") is True and layout.settings.get("multi_instance"))
    layout.var_multi.set(False)
    layout.toggle_multi()
    check("turning it off releases the lock", not multi.active and multi.log[-1] == "disable")
    layout.var_multi.set(True)
    layout.toggle_multi()
    check("the notice is not repeated", len(notices) == 2 or len(notices) == 1)
    seen = len(notices)
    layout.var_multi.set(False)
    layout.toggle_multi()
    layout.var_multi.set(True)
    layout.toggle_multi()
    check("no second notice after it was accepted", len(notices) == seen)

    multi.allow = False
    layout.var_multi.set(False)
    layout.toggle_multi()
    layout.var_multi.set(True)
    layout.toggle_multi()
    check("a refusal switches it back off and says why", not layout.var_multi.get()
          and "Close every Roblox client" in layout.lbl_multi.cget("text"), layout.lbl_multi.cget("text"))

    # the lock never outlives the app
    multi.allow = True
    layout.var_multi.set(True)
    layout.toggle_multi()
    layout.stop()
    check("stopping the tool releases the lock", not multi.active)

    app.shutdown()
    root.destroy()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_layout_page(tmp_base_dir):
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
