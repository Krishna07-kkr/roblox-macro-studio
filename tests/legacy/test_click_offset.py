"""Clicks must be measured from the CLIENT area, never the window frame.

Every calibrated point in this project is picked on a capture of the game's
render area, so a click has to be placed at (client origin + point). The window
frame starts a title bar and a border earlier - measured at (8, 31) on this
machine - so using the frame origin silently shifts every click 8px left and
31px up. In mutation that put the Mutate press on the pet row above the button,
which looks exactly like the macro clicking a pet slot instead of Mutate.
"""

import mutation_runner as mr
import main

failures = []

# Measured on a real Roblox client: client area sits this far inside the frame.
FRAME_TO_CLIENT = (8, 31)
WIN_ORIGIN = (100, 200)
CLIENT_ORIGIN = (WIN_ORIGIN[0] + FRAME_TO_CLIENT[0], WIN_ORIGIN[1] + FRAME_TO_CLIENT[1])


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


class FakeWM:
    """Reports both origins, so a runner that picks the wrong one is caught."""

    def __init__(self):
        self.window_offset_calls = 0
        self.client_offset_calls = 0

    def get_window_offset(self, hwnd):
        self.window_offset_calls += 1
        return WIN_ORIGIN

    def get_client_offset(self, hwnd):
        self.client_offset_calls += 1
        return CLIENT_ORIGIN

    def force_activate_window(self, hwnd, *a, **k):
        return True

    def is_foreground(self, hwnd):
        return True


class FakeMapper:
    is_identity = True

    def point(self, x, y):
        return x, y

    def describe(self):
        return "identity"


class RecordingInput:
    def __init__(self):
        self.calls = []

    def click(self, x, y, client_offset=(0, 0), relative=True, hwnd=None):
        self.calls.append({"x": x, "y": y, "offset": client_offset})
        return True

    def screen_point(self, i=0):
        c = self.calls[i]
        return (c["x"] + c["offset"][0], c["y"] + c["offset"][1])


def test_mutation():
    r = mr.MutationRunner.__new__(mr.MutationRunner)
    r.wm = FakeWM()
    r.input = RecordingInput()
    r.log = lambda m: None
    r.mutation_cfg = {"targets": {"pet_select": {"x": 182, "y": 220},
                                  "mutate": {"x": 535, "y": 384}}}

    ok = r._click_target(1, FakeMapper(), "mutate")
    check("mutation click is sent", ok is True)
    check("mutation uses the client offset", r.wm.client_offset_calls == 1,
          f"client={r.wm.client_offset_calls} window={r.wm.window_offset_calls}")
    check("mutation never uses the window frame offset",
          r.wm.window_offset_calls == 0, r.wm.window_offset_calls)

    want = (CLIENT_ORIGIN[0] + 535, CLIENT_ORIGIN[1] + 384)
    got = r.input.screen_point()
    check("Mutate lands on the calibrated pixel", got == want, f"{got} != {want}")

    # The exact failure Krishna saw: the frame origin would place the press a
    # title bar higher, i.e. on the pet row instead of the Mutate button.
    frame_based = (WIN_ORIGIN[0] + 535, WIN_ORIGIN[1] + 384)
    check("frame origin would have missed by (8, 31)",
          (want[0] - frame_based[0], want[1] - frame_based[1]) == FRAME_TO_CLIENT)

    # Pet select must land correctly too, or pets stack.
    r.input.calls.clear()
    r._click_target(1, FakeMapper(), "pet_select")
    want_pet = (CLIENT_ORIGIN[0] + 182, CLIENT_ORIGIN[1] + 220)
    check("Pet select lands on the calibrated pixel",
          r.input.screen_point() == want_pet, r.input.screen_point())


def test_goliath():
    r = main.MacroRunner.__new__(main.MacroRunner)
    r.wm = FakeWM()
    r.input = RecordingInput()
    r.log = lambda m: None
    r.use_relative = True

    ok = r._click_point(1, {"x": 280, "y": 345}, "Slot 1", "the Claim button")
    check("goliath click is sent", ok is True)
    check("goliath uses the client offset", r.wm.client_offset_calls == 1,
          f"client={r.wm.client_offset_calls} window={r.wm.window_offset_calls}")
    check("goliath never uses the window frame offset",
          r.wm.window_offset_calls == 0, r.wm.window_offset_calls)

    want = (CLIENT_ORIGIN[0] + 280, CLIENT_ORIGIN[1] + 345)
    check("Claim lands on the calibrated pixel", r.input.screen_point() == want,
          r.input.screen_point())


def test_no_frame_offset_in_click_paths():
    """No click path may reach for the frame origin again."""
    import importlib
    for name in ("mutation_runner", "main", "calibrate_mutation", "calibrate_gui"):
        # Read the module that actually runs, not a file name: after the package
        # move the top-level names are aliases of macro_studio modules.
        mod = name + ".py"
        src = open(importlib.import_module(name).__file__, encoding="utf-8").read()
        check(f"{mod} does not use get_window_offset",
              "get_window_offset" not in src)


def run():
    test_mutation()
    test_goliath()
    test_no_frame_offset_in_click_paths()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())


def test_legacy_checks():
    """Run every check in this script under pytest; a recorded failure fails the test."""
    assert run() == 0, failures
