"""find_windows(include_minimized=True) finds a really minimized window (real Win32, own process).

A minimized window is "visible" but its rectangle is about 160x28 off-screen, so the normal
size filter drops it. The program name is this very Python process, so no Roblox is needed.
"""

import sys
import tkinter as tk

failures = []


def check(name, cond, detail: object = ""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


def run():
    import psutil

    from macro_studio.core.window_manager import WindowManager

    me = psutil.Process().name().lower()
    wm = WindowManager(target_procs=[me], ignore_procs=["no-such-program.exe"])
    root = tk.Tk()
    root.title("Roblox minimized test")
    root.geometry("500x400+60+60")
    root.update()
    hwnd = int(root.frame(), 16)
    check("a normal window is found by default", hwnd in wm.find_windows(), wm.find_windows())

    root.iconify()
    root.update()
    check("really minimized", root.state() == "iconic", root.state())
    check("the default scan skips a minimized window", hwnd not in wm.find_windows())
    check("include_minimized finds it", hwnd in wm.find_windows(include_minimized=True),
          wm.find_windows(include_minimized=True))
    check("is_minimized says so", wm.is_minimized(hwnd))

    root.deiconify()
    root.update()
    check("restored: found again by the default scan", hwnd in wm.find_windows())
    check("and no longer minimized", not wm.is_minimized(hwnd))

    root.withdraw()
    root.update()
    check("a hidden window is not listed, minimized or not",
          hwnd not in wm.find_windows(include_minimized=True))

    root.destroy()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_find_windows_can_include_minimized():
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
