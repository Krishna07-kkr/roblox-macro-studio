"""The Apple-style motion: the switch knob glides, pages slide in, both can be interrupted."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))      # tests/

failures = []


def check(name, cond, detail: object = ""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


def run():
    import tkinter as tk

    from macro_studio.app import MacroStudioApp
    from macro_studio.core import ui_theme as ui

    root = tk.Tk()
    root.geometry("1180x880+0+0")

    def pump(ms):
        root.after(ms, root.quit)
        root.mainloop()

    # ---- the switch
    var = tk.BooleanVar(value=False)
    toggle = ui.Toggle(root, var, page_bg=ui.SURFACE)
    toggle.pack()
    root.update()
    seen = []
    toggle._toggle()
    check("the switch is on straight away (the value never waits for the animation)", var.get() is True)
    for _ in range(40):
        pump(16)
        seen.append(round(toggle._t, 2))
        if toggle._t == 1.0:
            break
    check("the knob glides there in several steps, not one jump", toggle._t == 1.0 and len(set(seen)) >= 4, seen)
    check("and the steps only go forward (an ease-out)", seen == sorted(seen), seen)

    toggle._toggle()
    pump(40)
    middle = toggle._t
    toggle._toggle()                                  # changes its mind half way
    pump(30)
    check("turning it round mid-slide continues from where it was, with no jump",
          0.0 < middle < 1.0 and abs(toggle._t - middle) < 0.6, (middle, toggle._t))
    pump(500)
    check("and it ends on the new value", toggle._t == (1.0 if var.get() else 0.0), (toggle._t, var.get()))
    var.set(False)
    toggle.redraw()
    check("a change made from outside shows at once", toggle._t == 0.0)

    # ---- pages
    app = MacroStudioApp(root)
    app.animate_pages = True
    for job in ("macro:claim_timers", "live_feed:macro", "memory:auto", "clients:scan"):
        app.ctx.scheduler.remove(job)
    app._switch_view(2)
    pump(30)
    page = app.views[2]
    x_now = int(page.place_info()["x"])
    check("a page starts a few pixels over and slides in", 0 < x_now <= 22, x_now)
    app._switch_view(4)                                # a second click before it arrived
    waited = 0
    while app._slide_job is not None and waited < 60:   # until the slide has finished
        pump(50)
        waited += 1
    placed = [i for i, v in enumerate(app.views) if v.place_info()]
    check("clicking again mid-slide shows only the newest page", placed == [4], placed)
    check("and it has settled exactly in place", int(app.views[4].place_info()["x"]) == 0, app.views[4].place_info())
    check("the title and sidebar follow", app.lbl_page_title.cget("text") == app.view_titles[4])

    app.animate_pages = False
    app._switch_view(1)
    root.update()
    check("with animation off a switch is instant", int(app.views[1].place_info()["x"]) == 0)

    app.shutdown()
    root.destroy()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_motion(tmp_base_dir):
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
