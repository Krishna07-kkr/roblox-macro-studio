"""Replays a recording against a real throw-away window to check the real input path.

Opens a small always-on-top Tk window (a button, a text entry and a list), builds a recording by
hand (move, click the button with a picture of it, press and release the key "a", scroll the
list) and plays it through the REAL player, window geometry, capture, mouse and keyboard code.
Then it checks what the window received. Takes about two seconds and briefly moves the mouse and
takes focus, so run it when you are not using the PC. Nothing outside its own window is clicked.

    .venv\\Scripts\\python.exe scripts\\recorder_selfcheck.py
"""

import os
import sys
import threading
import tkinter as tk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for _var, _sub in (("TCL_LIBRARY", "tcl8.6"), ("TK_LIBRARY", "tk8.6")):
    os.environ.setdefault(_var, os.path.join(sys.base_prefix, "tcl", _sub))


def main():
    import psutil

    from macro_studio.core import scaling
    from macro_studio.core.window_manager import WindowManager
    from macro_studio.tools.recorder.model import Recording, Step
    from macro_studio.tools.recorder.player import Player
    from macro_studio.tools.recorder.system import RealIO, World, client_info

    scaling.set_dpi_awareness()
    me = psutil.Process().name().lower()
    wm = WindowManager(target_procs=[me], ignore_procs=["no-such.exe"])

    root = tk.Tk()
    root.title("Recorder self-check")
    root.geometry("800x600+120+80")
    root.attributes("-topmost", True)
    seen = {"clicks": 0, "keys": [], "wheel": 0}
    button = tk.Button(root, text="CLICK ME", font=("Segoe UI", 28), command=lambda: seen.__setitem__(
        "clicks", seen["clicks"] + 1))
    button.place(x=300, y=200, width=260, height=100)
    entry = tk.Entry(root, font=("Segoe UI", 20))
    entry.place(x=300, y=360, width=260, height=40)
    listbox = tk.Listbox(root, font=("Segoe UI", 12))
    for i in range(80):
        listbox.insert("end", f"item {i}")
    listbox.place(x=20, y=20, width=200, height=300)
    root.bind("<KeyPress>", lambda e: seen["keys"].append(e.keysym))
    root.bind_all("<MouseWheel>", lambda e: seen.__setitem__("wheel", seen["wheel"] + 1), add="+")
    root.update()
    hwnd = int(root.frame(), 16)
    world = World(wm=wm)
    io = RealIO(wm)
    root.update()
    left, top, width, height = client_info(hwnd)
    print(f"window client {width}x{height} at ({left}, {top})")

    frame = world.capture_now(hwnd)
    if frame is None:
        print("FAIL: the window could not be captured")
        return 1
    bx = button.winfo_x() + button.winfo_width() // 2
    by = button.winfo_y() + button.winfo_height() // 2
    x0, y0 = bx - 32, by - 32
    picture = frame[y0:y0 + 64, x0:x0 + 64].copy()
    steps = [
        Step("move", 0, x=bx - 80, y=by - 60),
        Step("move", 80, x=bx, y=by),
        Step("down", 60, x=bx, y=by, button="left", look={"file": "p.png", "box": [x0, y0, 64, 64]}),
        Step("up", 70, x=bx, y=by, button="left"),
        Step("move", 80, x=100, y=150),
        Step("wheel", 80, x=100, y=150, delta=-120),
        Step("wheel", 60, x=100, y=150, delta=-120),
        Step("down", 80, x=330, y=380, button="left", look=None),
        Step("up", 50, x=330, y=380, button="left"),
        Step("key_down", 60, vk=0x41),
        Step("key_up", 70, vk=0x41),
    ]
    rec = Recording("selfcheck", width, height, steps)
    logs = []
    player = Player(io, capture=world.capture_now, guard=None, wait=lambda s: (threading.Event().wait(s), True)[1],
                    stop=lambda: False, log=logs.append)
    outcome = {}

    def play():
        outcome["r"] = player.play(rec, hwnd, lambda name: picture, speed=1.0, step_timeout=3.0)
    worker = threading.Thread(target=play)
    worker.start()

    def poll():
        if worker.is_alive():
            root.after(30, poll)
        else:
            root.quit()
    root.after(30, poll)
    root.mainloop()
    worker.join()
    root.update()
    result = outcome["r"]
    first_visible = listbox.nearest(0)
    checks = {
        "the replay finished": result.ok,
        "the button was clicked once": seen["clicks"] == 1,
        "the list received the wheel": seen["wheel"] >= 1,
        "the key 'a' reached the window": "a" in seen["keys"],
        "the entry got focus from the click and text": entry.get() in ("a", ""),
    }
    root.destroy()
    print(result)
    for line in logs:
        print("  log:", line)
    print("seen:", seen, "first visible list item:", first_visible)
    bad = [name for name, ok in checks.items() if not ok]
    for name, ok in checks.items():
        print(("PASS  " if ok else "FAIL  ") + name)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
