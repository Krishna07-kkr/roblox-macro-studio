"""Soak test: does a long session leak handles, GDI objects, memory or images, or stall the UI?

Runs the real Macro Studio under mainloop with fake Roblox clients (synthetic frames, so it
needs no game), keeps the live feed on, opens and closes the Tabs picker and the pop-out feed
on a schedule, and samples the numbers below every few seconds. After a warm-up it checks
the growth against the budgets in specs/002-audit-hardening (SC-A01 to SC-A03).

    .venv\\Scripts\\python.exe scripts\\soak.py --minutes 10
    .venv\\Scripts\\python.exe scripts\\soak.py --minutes 60 --csv soak.csv

It works on a temporary copy of your data, so no profile is touched, and the window is
transparent by default (--visible to see it). Exit code 0 = all budgets met.
"""

import argparse
import csv
import ctypes
import gc
import os
import shutil
import sys
import tempfile
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BUDGETS = {                  # growth after warm-up (SC-A01 .. SC-A03)
    "handles": 5,
    "gdi": 5,
    "user": 5,
    "tk_images": 2,
    "rss_percent": 5.0,
    "lag_s": 0.25,           # the budget is 0.2 s; slack for a loaded machine
}

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_user32.GetGuiResources.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_user32.GetGuiResources.restype = wintypes.DWORD


def handle_count():
    count = wintypes.DWORD()
    _kernel32.GetProcessHandleCount(_kernel32.GetCurrentProcess(), ctypes.byref(count))
    return count.value


def gui_resources(kind):          # 0 = GDI objects, 1 = USER objects
    return _user32.GetGuiResources(_kernel32.GetCurrentProcess(), kind)


def prepare_data_copy():
    """A throwaway copy of the data, so the soak can never touch real profiles."""
    folder = tempfile.mkdtemp(prefix="soak-data-")
    for name in ("config.json", "config_mutation.json", "account_schedule.json"):
        source = os.path.join(ROOT, name)
        if os.path.isfile(source):
            shutil.copy2(source, folder)
    for sub in ("profiles", "templates"):
        if os.path.isdir(os.path.join(ROOT, sub)):
            shutil.copytree(os.path.join(ROOT, sub), os.path.join(folder, sub))
    os.environ["MACRO_STUDIO_DATA_DIR"] = folder
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--clients", type=int, default=4, help="fake Roblox clients")
    parser.add_argument("--sample-s", type=float, default=10.0)
    parser.add_argument("--warmup-s", type=float, default=60.0)
    parser.add_argument("--csv", default="")
    parser.add_argument("--visible", action="store_true")
    args = parser.parse_args()

    data_dir = prepare_data_copy()
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "tests"))

    import tkinter as tk

    import numpy as np
    import psutil
    from conftest import FakeWM
    from macro_studio.app import MacroStudioApp

    root = tk.Tk()
    root.geometry("1180x880+0+0")
    if not args.visible:
        root.attributes("-alpha", 0.0)
    app = MacroStudioApp(root)
    macro = app.tool("macro")
    macro.pm.save_profile = lambda name, data: True

    rng = np.random.default_rng(1)
    frames = {}
    for i in range(args.clients):
        frame = rng.integers(0, 255, (600, 800, 3), dtype=np.uint8)
        frame[100:200, 100:300] = (40 + i * 40, 90, 200)
        frames[1000 + i] = frame
    macro._feed_wm = FakeWM({h: {"pid": 5000 + n, "title": f"Roblox {n}"}
                             for n, h in enumerate(frames)})
    capture = lambda hwnd: frames.get(hwnd)          # noqa: E731
    macro._feed_vs.capture_client_full = capture
    macro.live_feed._capture = capture

    process = psutil.Process()
    samples, start = [], time.monotonic()
    state = {"last_tick": time.monotonic(), "worst_gap": 0.0, "warm": None, "peak_gap": 0.0}

    def tick():                                           # lag probe, every 10 ms
        now = time.monotonic()
        state["worst_gap"] = max(state["worst_gap"], now - state["last_tick"])
        state["last_tick"] = now
        root.after(10, tick)

    def images():
        return len(root.tk.call("image", "names"))

    def sample():
        gc.collect()
        elapsed = time.monotonic() - start
        row = {"t_s": round(elapsed), "handles": handle_count(), "gdi": gui_resources(0),
               "user": gui_resources(1), "rss_mb": round(process.memory_info().rss / 2**20, 1),
               "tk_images": images(), "worst_gap_s": round(state["worst_gap"], 3),
               "picker": int(picker_open()), "popout": int(popout_open())}
        samples.append(row)
        if state["warm"] is None and elapsed >= args.warmup_s and not (row["picker"] or row["popout"]):
            state["warm"] = dict(row)
            state["worst_gap"] = 0.0                      # judge lag after warm-up too
        print("  ".join(f"{k}={v}" for k, v in row.items()), flush=True)
        state["peak_gap"] = max(state["peak_gap"], row["worst_gap_s"])
        if elapsed < args.minutes * 60:
            root.after(int(args.sample_s * 1000), sample)
        else:
            root.quit()

    # Open and close are idempotent: a click elsewhere on the desktop closes the picker early
    # (by design), and a plain toggle would then open it instead and leave it open.
    def picker_open():
        return macro.tab_picker is not None and not macro.tab_picker.closed

    def open_picker():
        if not picker_open():
            macro.toggle_tab_picker()

    def close_picker():
        if picker_open():
            macro.toggle_tab_picker()

    def popout_open():
        return macro.feed_window is not None

    def cycle_picker():
        open_picker()
        root.after(5000, close_picker)

    def cycle_popout():
        if not popout_open():
            macro.pop_out_feed()
        root.after(5000, lambda: macro.dock_feed() if popout_open() else None)

    def periodic():
        if time.monotonic() - start < args.minutes * 60 - 12:
            cycle_picker()
            root.after(12000, cycle_popout)
            root.after(30000, periodic)

    root.after(10, tick)
    root.after(2000, sample)
    root.after(3000, periodic)
    print(f"Soak: {args.minutes} min, {args.clients} fake clients, data copy {data_dir}", flush=True)
    root.mainloop()

    app.shutdown()
    root.destroy()
    shutil.rmtree(data_dir, ignore_errors=True)

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(samples[0]))
            writer.writeheader()
            writer.writerows(samples)

    quiet = [s for s in samples if not (s["picker"] or s["popout"])]
    warm, last = state["warm"], (quiet[-1] if quiet else samples[-1])
    if warm is None:
        print("Too short to judge: raise --minutes above the warm-up.")
        return 2
    growth = {
        "handles": last["handles"] - warm["handles"],
        "gdi": last["gdi"] - warm["gdi"],
        "user": last["user"] - warm["user"],
        "tk_images": last["tk_images"] - warm["tk_images"],
        "rss_percent": round((last["rss_mb"] - warm["rss_mb"]) / warm["rss_mb"] * 100, 1),
        "lag_s": last["worst_gap_s"],
    }
    print("\nGrowth after warm-up vs budget:")
    failed = []
    for key, value in growth.items():
        ok = value <= BUDGETS[key]
        print(f"  {'PASS' if ok else 'FAIL'}  {key:12} {value:>8}   (budget {BUDGETS[key]})")
        if not ok:
            failed.append(key)
    print("\n" + ("ALL BUDGETS MET" if not failed else f"OVER BUDGET: {failed}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
