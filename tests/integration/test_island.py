"""The Recorder island as a real window: placement, hover growth, no focus stealing, no leaks."""

import gc
import sys
import tkinter as tk

failures = []


def check(name, cond, detail: object = ""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


def run():
    from macro_studio.tools.recorder import island as isl

    root = tk.Tk()
    root.geometry("300x200+10+10")
    root.update()
    rects = {7: (400, 300, 800, 600)}
    calls = []
    callbacks = {"on_record": lambda: calls.append("record"), "on_play": lambda: calls.append("play"),
                 "on_stop": lambda: calls.append("stop"), "on_select": lambda n: calls.append(("select", n))}
    screen = lambda: (0, 0, 1920, 1080)  # noqa: E731

    def make():
        return isl.RecorderIsland(root, lambda h: rects.get(h), callbacks, animate=False,
                                  screen=screen, scale=100)

    island = make()
    root.update()
    check("hidden until it has a Roblox window to sit by", not island.shown
          and island.win.state() == "withdrawn")
    island.set_target(7)
    root.update()
    check("it appears once a window is chosen", island.shown and island.win.state() == "normal",
          island.win.state())
    x, y = island.win.winfo_x(), island.win.winfo_y()
    check("it sits above the window, centred on it", (x, y) == (400 + (800 - 200) // 2, 300 - 38 - 8),
          (x, y))
    check("it is the small pill size", (island.win.winfo_width(), island.win.winfo_height()) == (200, 38),
          (island.win.winfo_width(), island.win.winfo_height()))

    rects[7] = (600, 340, 800, 600)
    island.tick()
    root.update()
    check("it follows the window when it moves", (island.win.winfo_x(), island.win.winfo_y())
          == (600 + 300, 340 - 46), (island.win.winfo_x(), island.win.winfo_y()))

    style = isl.window_style(island.win)
    check("clicking it never takes focus from the game", style & isl._WS_EX_NOACTIVATE, hex(style))
    check("and it stays off the taskbar", style & isl._WS_EX_TOOLWINDOW, hex(style))

    # ---- state and recordings
    island.set_recordings(["Egg run", "Pets"], "Pets")
    island.set_state("recording", "12 steps")
    root.update()
    check("it remembers the chosen recording", island.selected == "Pets" and island.list.curselection() == (1,))

    # ---- growing
    island.expand()
    root.update()
    check("hovering grows it into the panel", (island.win.winfo_width(), island.win.winfo_height()) == (380, 250),
          (island.win.winfo_width(), island.win.winfo_height()))
    check("the list and buttons show", island.canvas.itemcget(island._items["list"], "state") == "normal"
          and island.canvas.itemcget(island._items["play"], "state") == "normal")
    check("the Record button says what it will do now", island.btn_record._text == "Stop recording",
          island.btn_record._text)
    island.list.selection_clear(0, "end")
    island.list.selection_set(0)
    island._picked()
    check("picking a recording tells the tool", ("select", "Egg run") in calls, calls)
    island.btn_play._command()
    island.btn_stop._command()
    island.btn_record._command()
    check("its buttons reach the tool", calls[-3:] == ["play", "stop", "record"], calls)
    island.collapse()
    root.update()
    check("and it shrinks back to the pill", (island.win.winfo_width(), island.win.winfo_height()) == (200, 38))
    check("the panel parts hide again", island.canvas.itemcget(island._items["list"], "state") == "hidden")

    # ---- animation
    animated = isl.RecorderIsland(root, lambda h: rects.get(h), callbacks, animate=True, screen=screen, scale=100)
    animated.set_target(7)
    animated.expand()
    root.update()
    mid = animated.w.value
    deadline_loops = 0
    while not (animated.w.settled(380) and animated.h.settled(250)) and deadline_loops < 400:
        root.after(16, root.quit)
        root.mainloop()
        deadline_loops += 1
    check("the animated growth reaches the panel size", abs(animated.w.value - 380) < 1
          and abs(animated.h.value - 250) < 1, (animated.w.value, animated.h.value))
    check("and it did not jump there", mid < 380, mid)
    animated.destroy()

    # ---- the window goes away or is minimized
    rects.pop(7)
    island.tick()
    root.update()
    check("it hides when the Roblox window is gone or minimized", island.win.state() == "withdrawn")

    # ---- other screens
    rects[7] = (100, 10, 800, 600)                         # no room above
    island.tick()
    root.update()
    check("with no room above it moves to the side", island.win.winfo_y() >= 10 and island.win.winfo_x() >= 100 + 800,
          (island.win.winfo_x(), island.win.winfo_y()))

    # ---- no leaks
    island.destroy()
    gc.collect()
    base = len(root.tk.call("image", "names"))
    for _ in range(30):
        extra = make()
        extra.set_target(7)
        extra.set_recordings(["a", "b"], "a")
        extra.expand()
        root.update()
        extra.destroy()
    gc.collect()
    check("opening and closing it 30 times leaves no images behind",
          len(root.tk.call("image", "names")) <= base + 2, (base, len(root.tk.call("image", "names"))))

    root.destroy()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_island_window():
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
