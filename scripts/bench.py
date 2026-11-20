"""Micro-benchmarks for the hot paths (spec 002, A5): measure before optimizing.

    .venv/Scripts/python.exe scripts/bench.py

Times, with 1, 4 and 8 fake clients where it matters: finding windows, drawing a live-feed
frame (overlay + resize), the Tabs picker snapshot, making a Tk image, and the popup guard's
template match. Results print as a table for specs/002-audit-hardening/audit-results.md.
Nothing here touches your data or a real game window.
"""

import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))


def timed(fn, repeat=30, warm=3):
    for _ in range(warm):
        fn()
    samples = []
    for _ in range(repeat):
        began = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - began) * 1000)
    return statistics.median(samples), max(samples)


def main():
    import numpy as np
    from PIL import Image

    from conftest import FakeWM
    from macro_studio.core.live_feed import LiveFeed, Overlay
    from macro_studio.core.popup_guard import PopupGuard
    from macro_studio.core.tab_selection import snapshot_tabs
    from macro_studio.core.window_manager import WindowManager

    rng = np.random.default_rng(1)
    frame = rng.integers(0, 255, (600, 800, 3), dtype=np.uint8)
    rows = []

    wm = WindowManager()
    rows.append(("find_windows (real desktop)", *timed(wm.find_windows)))
    rows.append(("count_minimized_clients (real desktop)", *timed(wm.count_minimized_clients)))

    overlay = Overlay(regions=[(196, 178, 142, 246, (0, 255, 255), "Slot 1")],
                      points=[(273, 288, (0, 112, 243), "")])
    feed = LiveFeed(capture=lambda hwnd: frame, pick_window=lambda: 1, overlay=lambda: overlay,
                    scheduler=None, ui_call=lambda fn: fn(), on_frame=lambda *a: None,
                    max_size=(448, 336))
    rows.append(("LiveFeed.render 800x600 -> 448x336", *timed(feed.render)))

    for count in (1, 4, 8):
        fake = FakeWM({1000 + i: {"pid": 5000 + i, "title": f"Roblox {i}"} for i in range(count)})
        rows.append((f"snapshot_tabs, {count} clients",
                     *timed(lambda: snapshot_tabs(fake.find_windows, fake.get_pid,
                                                  fake.get_window_title, lambda h: frame), repeat=15)))

    import tkinter as tk

    from PIL import ImageTk
    root = tk.Tk()
    root.withdraw()
    image = Image.fromarray(frame).resize((448, 336))
    rows.append(("ImageTk.PhotoImage 448x336", *timed(lambda: ImageTk.PhotoImage(image))))
    root.destroy()

    import cv2
    import tempfile
    folder = tempfile.mkdtemp()
    os.makedirs(os.path.join(folder, "templates"))
    patch = frame[200:300, 300:460].copy()
    cv2.imwrite(os.path.join(folder, "templates", "purchase_popup.png"), patch)
    guard = PopupGuard({"popup_guard": {"enabled": True, "template": "purchase_popup.png",
                                        "origin": {"x": 300, "y": 200},
                                        "close_rel": {"x": 150, "y": 12}}},
                       os.path.join(folder, "templates"), capture=lambda h: frame,
                       click=lambda h, x, y: True, log=lambda m: None, wait=lambda s: True,
                       pause=lambda: None, stop=lambda: None)
    rows.append(("popup guard check (frame, no popup)", *timed(lambda: guard._find(frame))))

    print(f"{'what':46} {'median ms':>10} {'max ms':>9}")
    for name, median, worst in rows:
        print(f"{name:46} {median:>10.2f} {worst:>9.2f}")


if __name__ == "__main__":
    main()
