"""The Tabs picker and the live feed view must not accumulate Tk images (A1, finding H12).

Measured, not assumed: 100-300 open/close cycles and 500 frames left the image count flat.
This keeps it that way. Runs in its own process because it creates a Tk root.
"""

import gc
import sys
import tkinter as tk
from types import SimpleNamespace

failures = []


def check(name, cond, detail: object = ""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


class Scheduler:
    def add(self, *args, **kwargs):
        pass

    def remove(self, *args, **kwargs):
        pass


def run():
    import numpy as np
    from PIL import Image

    from macro_studio.core.live_feed_view import LiveFeedView
    from macro_studio.core.tab_picker import TabPicker
    from macro_studio.core.tab_selection import TabSelection

    root = tk.Tk()
    root.geometry("900x700+0+0")

    def images():
        return len(root.tk.call("image", "names"))

    rng = np.random.default_rng(2)
    tabs = [SimpleNamespace(hwnd=i, pid=100 + i, title=f"Roblox {i}",
                            image=Image.fromarray(rng.integers(0, 255, (144, 192, 3), dtype=np.uint8)))
            for i in range(4)]
    anchor = tk.Button(root, text="Tabs")
    anchor.pack()
    root.update()
    selection = TabSelection()

    def cycle():
        picker = TabPicker(root, anchor, selection, snapshot=lambda: tabs, scheduler=Scheduler(),
                           ui_call=lambda fn: fn(), minimized_count=lambda: 0)
        picker.show_tabs(tabs, 0)
        root.update()
        picker.close()
        root.update()

    for _ in range(5):                      # warm-up: one-off theme caches
        cycle()
    gc.collect()
    warm = images()
    for _ in range(100):
        cycle()
    gc.collect()
    check("opening and closing the picker 100 times adds no images", images() <= warm + 2,
          (warm, images()))

    holder = tk.Frame(root)
    holder.pack()
    view = LiveFeedView(holder)
    view.pack()
    frame = Image.fromarray(rng.integers(0, 255, (336, 448, 3), dtype=np.uint8))
    view.show(frame, "first")
    root.update()
    gc.collect()
    warm = images()
    for i in range(300):
        view.show(frame, f"frame {i}")
        view.show(None, "no client")
        if i % 50 == 0:
            root.update()
    root.update()
    gc.collect()
    check("600 feed frames and placeholders add no images", images() <= warm + 2, (warm, images()))

    root.destroy()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_tk_images_stay_flat():
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
