"""Capture popup: the dialog, the Settings card, and the saved picture working in the guard.

Runs in its own process because it creates a Tk root (see conftest.run_in_fresh_python).
Takes the temporary data folder (MACRO_STUDIO_DATA_DIR), so no real file is written, and mouse
events are sent straight to the canvas, never as keys to "whatever has focus".
"""

import os
import sys

failures = []


def check(name, cond, detail: object = ""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


def draw_popup(frame, x, y):
    import cv2
    cv2.rectangle(frame, (x, y), (x + 159, y + 99), (40, 40, 200), -1)
    cv2.rectangle(frame, (x, y), (x + 159, y + 23), (255, 255, 255), -1)
    cv2.rectangle(frame, (x + 135, y + 2), (x + 157, y + 21), (0, 0, 255), -1)
    cv2.rectangle(frame, (x + 20, y + 60), (x + 139, y + 89), (60, 200, 60), -1)
    cv2.circle(frame, (x + 40, y + 40), 14, (0, 255, 255), -1)


def click(canvas, x, y):
    canvas.event_generate("<ButtonPress-1>", x=x, y=y)
    canvas.event_generate("<ButtonRelease-1>", x=x, y=y)


def drag(canvas, x0, y0, x1, y1):
    canvas.event_generate("<ButtonPress-1>", x=x0, y=y0)
    canvas.event_generate("<B1-Motion>", x=x1, y=y1, state=0x100)
    canvas.event_generate("<ButtonRelease-1>", x=x1, y=y1)


def run():
    import numpy as np
    import tkinter as tk
    from tkinter import messagebox

    from macro_studio.app import MacroStudioApp
    from macro_studio.core import scaling
    from macro_studio.core.popup_capture import PopupCaptureDialog
    from macro_studio.core.popup_guard import PopupGuard

    base = scaling.get_base_dir()
    check("test runs on the temporary data folder",
          os.path.abspath(base) != os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
          base)

    answers = {"yes": True}
    dialogs = []
    messagebox.showwarning = lambda t, m, **k: dialogs.append(("warning", t, m))
    messagebox.showerror = lambda t, m, **k: dialogs.append(("error", t, m))
    messagebox.askyesno = lambda *a, **k: answers["yes"]
    messagebox.askyesnocancel = lambda *a, **k: False

    root = tk.Tk()
    root.geometry("1180x880+0+0")
    app = MacroStudioApp(root)
    m = app.tool("macro")
    m.pm.save_profile = lambda name, data: True          # never write a profile
    root.update()

    rng = np.random.default_rng(3)
    frame = rng.integers(0, 256, (600, 800, 3), dtype=np.uint8)
    draw_popup(frame, 300, 200)

    # 1. The dialog: box, then close button, then save.
    saved = []
    dlg = PopupCaptureDialog(root, frame, base, "purchase_popup_Test.png", saved.append)
    root.update()
    canvas = dlg.canvas
    check("starts on step 1 with Save off", "Step 1" in dlg.lbl_step.cget("text")
          and dlg.btn_save._state == "disabled")

    click(canvas, 100, 100)                                   # a click is not a box
    root.update()
    check("a plain click does not make a box", dlg.box is None and dlg.lbl_error.cget("text"))

    drag(canvas, 305, 205, 320, 215)                          # too small to recognise
    root.update()
    check("a tiny box is refused and the dialog starts over",
          dlg.box is None and "too small" in dlg.lbl_error.cget("text"), dlg.lbl_error.cget("text"))

    drag(canvas, 460, 300, 300, 200)                          # drag backwards: corners any way round
    root.update()
    check("the box is taken from the corners, either way round", dlg.box == (300, 200, 160, 100),
          dlg.box)
    check("it moves on to the close button", "Step 2" in dlg.lbl_step.cget("text"))

    click(canvas, 10, 10)                                     # outside the box
    root.update()
    check("a close point outside the box is refused", dlg.close is None
          and "inside" in dlg.lbl_error.cget("text") and dlg.btn_save._state == "disabled")

    click(canvas, 450, 212)
    root.update()
    check("the close button is recorded", dlg.close == (450, 212), dlg.close)
    check("Save turns on", dlg.btn_save._state == "normal")

    dlg.reset()
    root.update()
    check("Start over clears everything", dlg.box is None and dlg.close is None
          and dlg.btn_save._state == "disabled")

    drag(canvas, 300, 200, 460, 300)
    click(canvas, 450, 212)
    dlg.save()
    root.update()
    check("Save hands back the settings and closes", len(saved) == 1 and not dlg.winfo_exists())
    settings = saved[0] if saved else {}
    check("close button is stored relative to the picture",
          settings.get("close_rel") == {"x": 150, "y": 12}, settings)
    check("the picture file was written in the data folder",
          os.path.isfile(os.path.join(base, "templates", "purchase_popup_Test.png")))

    # 2. The picture works: the guard finds the popup in the frame and clicks the close button.
    clicks, logs = [], []
    guard = PopupGuard({"popup_guard": settings}, os.path.join(base, "templates"),
                       capture=lambda h: frame if not clicks else rng.integers(
                           0, 256, (600, 800, 3), dtype=np.uint8),
                       click=lambda h, x, y: clicks.append((x, y)) or True, log=logs.append,
                       wait=lambda s: True, pause=lambda: None, stop=lambda: None)
    check("guard accepts the saved picture", guard.active)
    check("guard closes the popup through the captured close button",
          guard.ensure_clear(1) is True and clicks == [(450, 212)], (clicks, logs))

    # 3. The Settings card.
    check("the card starts Off", m.lbl_popup_status.cget("text").startswith("Off"),
          m.lbl_popup_status.cget("text"))
    check("Remove is disabled with nothing captured", m.btn_popup_remove._state == "disabled")
    m._popup_saved(settings)
    root.update()
    check("saving marks the profile unsaved", m.dirty)
    check("the profile now holds the guard settings", m.current_cfg.get("popup_guard") == settings)

    name = m._popup_picture_name()
    check("picture name is per profile and filesystem-safe",
          name.startswith("purchase_popup_") and name.endswith(".png")
          and all(c.isalnum() or c in "_-." for c in name), name)
    m.active_profile_name = "My Alt/..\\x"
    check("an odd profile name cannot reach outside templates/",
          os.path.basename(m._popup_picture_name()) == m._popup_picture_name()
          and ".." not in m._popup_picture_name(), m._popup_picture_name())

    m.current_cfg["popup_guard"] = dict(settings, template="purchase_popup_Test.png")
    m._refresh_popup_status()
    check("the card says On when the picture exists", m.lbl_popup_status.cget("text").startswith("On"),
          m.lbl_popup_status.cget("text"))
    m.current_cfg["popup_guard"] = dict(settings, template="purchase_popup_gone.png")
    m._refresh_popup_status()
    check("the card says Off when the picture is missing",
          "missing" in m.lbl_popup_status.cget("text"), m.lbl_popup_status.cget("text"))
    m.current_cfg["popup_guard"] = dict(settings, template=r"..\..\evil.png")
    m._refresh_popup_status()
    check("a picture name outside templates/ is not trusted",
          m.lbl_popup_status.cget("text").startswith("Off"))

    m.current_cfg["popup_guard"] = settings
    answers["yes"] = False
    m._remove_popup()
    check("Remove asks first, and No keeps it", "popup_guard" in m.current_cfg)
    answers["yes"] = True
    m._remove_popup()
    check("Remove drops the setting", "popup_guard" not in m.current_cfg
          and m.lbl_popup_status.cget("text").startswith("Off"))

    # 4. No client open: the capture reports it instead of opening an empty dialog.
    dialogs.clear()
    m._popup_frame_ready(None, "No Roblox client is open.")
    check("no frame -> a warning, not a dialog", dialogs and "No Roblox client" in dialogs[-1][2],
          dialogs)
    check("the Capture button works again", m.btn_popup_capture._state == "normal")

    app.shutdown()
    root.destroy()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_popup_ui(tmp_base_dir):
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
