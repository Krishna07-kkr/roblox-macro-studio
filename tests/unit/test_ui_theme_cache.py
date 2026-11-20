"""Cached rounded images must not outlive the Tk interpreter that owns them."""

import sys
import tkinter as tk

import ui_theme


def _make_root():
    root = tk.Tk()
    root.withdraw()
    return root


def check_rounded_image_survives_a_new_tk_root():
    first = _make_root()
    tk.Label(first, image=ui_theme.rounded_image(20, 10, 4, "#111111")).pack()
    first.destroy()

    second = _make_root()
    try:
        # Same cache key as above: must be a fresh image owned by the new root.
        tk.Label(second, image=ui_theme.rounded_image(20, 10, 4, "#111111")).pack()
    finally:
        second.destroy()


def test_rounded_image_survives_a_new_tk_root():
    # Two roots in one process is exactly the case under test, and Tk itself is
    # flaky there, so the check runs in its own process.
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__, "--check")
    assert code == 0, output


if __name__ == "__main__" and "--check" in sys.argv:
    check_rounded_image_survives_a_new_tk_root()
    print("OK")
