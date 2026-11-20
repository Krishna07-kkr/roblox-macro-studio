"""A stray pop-up must never be clicked at a guessed pixel.

An unexpected pop-up in this game can be a Robux purchase prompt. Clicking a
pixel we picked ourselves to "get rid of it" could confirm a purchase with real
money, so the rule is absolute: the macro only clicks a pop-up close button the
user calibrated, and otherwise stops and says so.
"""

import os
import tempfile

import main

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


class FakeWM:
    def is_foreground(self, hwnd):
        return True

    def force_activate_window(self, hwnd, *a, **k):
        return True

    def get_client_offset(self, hwnd):
        return (0, 0)


class FakeInput:
    def __init__(self):
        self.clicks = []

    def click(self, x, y, client_offset=None, relative=True, hwnd=None):
        self.clicks.append((x, y))
        return True


class FakeVision:
    def __init__(self, frame="FRAME"):
        self.frame = frame
        self.saved = []

    def capture_client_full(self, hwnd):
        return self.frame

    def save_full_capture(self, img, tag):
        self.saved.append(tag)
        return os.path.join(tempfile.gettempdir(), f"{tag}.png")


def build(dismiss=True, capture=True):
    r = object.__new__(main.MacroRunner)
    r.wm = FakeWM()
    r.input = FakeInput()
    r.vs = FakeVision()
    r.use_relative = True
    r.running = True
    r.dismiss_popups = dismiss
    r.capture_failures = capture
    r.d_after_confirm = 0.0
    r.sleep_interruptible = lambda s: True
    r.logs = []
    r.log = lambda m: r.logs.append(m)
    return r


def run():
    # --- No calibrated close point: nothing may be clicked. ---
    r = build()
    closed = r._dismiss_popup(1, {"pet_select": {"x": 9, "y": 9},
                                  "confirm_yes": {"x": 8, "y": 8}}, "Account #1")
    check("uncalibrated pop-up is not clicked", r.input.clicks == [], r.input.clicks)
    check("uncalibrated pop-up reports failure", closed is False)
    check("it says why nothing was clicked",
          any("popup_close" in m for m in r.logs), r.logs)

    # The two nearby daycare points must never be reused as a close button:
    # on a purchase prompt, a 'Yes' is exactly the wrong thing to press.
    check("confirm_yes is not used to close pop-ups",
          (8, 8) not in r.input.clicks)
    check("pet_select is not used to close pop-ups",
          (9, 9) not in r.input.clicks)

    # --- Calibrated close point: that exact pixel, and only it. ---
    r2 = build()
    ok = r2._dismiss_popup(1, {"popup_close": {"x": 534, "y": 236},
                               "confirm_yes": {"x": 8, "y": 8}}, "Account #1")
    check("calibrated pop-up close is clicked", ok is True)
    check("only the calibrated pixel is clicked", r2.input.clicks == [(534, 236)],
          r2.input.clicks)

    # --- 0,0 is the UI's "not calibrated" value: never click the corner. ---
    r_zero = build()
    zero_ok = r_zero._dismiss_popup(1, {"popup_close": {"x": 0, "y": 0}}, "Account #1")
    check("0,0 pop-up close is treated as not calibrated", zero_ok is False)
    check("0,0 clicks nothing", r_zero.input.clicks == [], r_zero.input.clicks)

    # --- Switched off: still no clicks. ---
    r3 = build(dismiss=False)
    r3._dismiss_popup(1, {"popup_close": {"x": 534, "y": 236}}, "Account #1")
    check("disabled setting clicks nothing", r3.input.clicks == [], r3.input.clicks)

    # --- Evidence capture, so the pop-up can be identified afterwards. ---
    r4 = build()
    path = r4._capture_evidence(1, "add_failed_Slot 2", "Account #1")
    check("a failed action saves a full screenshot", path is not None)
    check("the runner asks for a tagged capture", r4.vs.saved == ["add_failed_Slot 2"],
          r4.vs.saved)
    check("the log points at the file", any("Saved a screenshot" in m for m in r4.logs),
          r4.logs)

    r5 = build(capture=False)
    check("evidence capture can be switched off",
          r5._capture_evidence(1, "x", "Account #1") is None)

    # --- The real saver writes a usable, filesystem-safe file. ---
    import numpy as np
    from vision_scanner import VisionScanner
    vs = VisionScanner({})
    img = np.zeros((20, 30, 3), dtype=np.uint8)
    real_path = vs.save_full_capture(img, "add_failed_Slot 2")
    check("a real screenshot file is written",
          real_path is not None and os.path.isfile(real_path), real_path)
    check("the file name has no spaces or odd characters",
          all(c.isalnum() or c in "-_." for c in os.path.basename(real_path)),
          os.path.basename(real_path))
    check("it lands in debug_captures",
          os.path.basename(os.path.dirname(real_path)) == "debug_captures", real_path)
    check("a missing frame saves nothing", vs.save_full_capture(None, "x") is None)
    if real_path and os.path.isfile(real_path):
        os.remove(real_path)

    # --- The source must not contain a hardcoded pop-up coordinate. ---
    import importlib
    # The real module's source: "main" becomes an alias after the package move.
    src = open(importlib.import_module("main").__file__, encoding="utf-8").read()
    dismiss_src = src[src.index("def _dismiss_popup"):src.index("def _read_slot_states")]
    check("no guessed coordinate in the dismiss path",
          "popup_close" in dismiss_src and not any(
              tok in dismiss_src for tok in ('"x": ', "{'x':")), dismiss_src[-200:])

    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())


def test_legacy_checks():
    """Run every check in this script under pytest; a recorded failure fails the test."""
    assert run() == 0, failures
