"""Calibration must reach the profile the macro actually runs from.

Three regressions this guards against:
1. The calibrators saved next to their own .py file. In the one-file exe that
   is PyInstaller's temp folder, deleted when the exe closes, so every
   recalibration was silently lost.
2. The Goliath calibrator wrote config.json, but the GUI runs from
   profiles/Goliath.json, so calibrated points never reached the macro.
3. test_ui_wiring once leaked its fake values (1024 px window etc.) into
   profiles/Goliath.json, so clicks were stretched 1.28x horizontally.
"""

import copy
import json
import os
import shutil
import sys
import tempfile

import scaling

failures = []


def check(name, cond, detail: object = ""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


class _FakeFrozen:
    """Pretend to be the one-file exe living in `exe_dir`."""

    def __init__(self, exe_dir):
        self.exe_dir = exe_dir

    def __enter__(self):
        self._had_frozen = hasattr(sys, "frozen")
        self._exe = sys.executable
        setattr(sys, "frozen", True)  # noqa: B010 - sys has no declared 'frozen'
        sys.executable = os.path.join(self.exe_dir, "RobloxMacroStudio.exe")
        return self

    def __exit__(self, *exc):
        sys.executable = self._exe
        if not self._had_frozen:
            delattr(sys, "frozen")


def check_save_paths():
    import calibrate_gui
    import calibrate_mutation

    exe_dir = tempfile.mkdtemp(prefix="calib_exe_")
    # If the save path is wrong it lands on the real config.json - keep a copy.
    real_cfg = os.path.join(scaling.get_base_dir(), "config.json")
    with open(real_cfg, "rb") as f:
        real_backup = f.read()
    try:
        with _FakeFrozen(exe_dir):
            calibrate_gui.save_config({"slots": [], "common_actions": {}})
            check("goliath calibrator saves next to the exe",
                  os.path.isfile(os.path.join(exe_dir, "config.json")),
                  os.listdir(exe_dir))
            check("mutation calibrator saves next to the exe",
                  os.path.dirname(calibrate_mutation.mutation_config_path()) == exe_dir,
                  calibrate_mutation.mutation_config_path())
    finally:
        shutil.rmtree(exe_dir, ignore_errors=True)
        with open(real_cfg, "rb") as f:
            changed = f.read() != real_backup
        # Restore only when it changed: rewriting the real file on every run raced with
        # Windows briefly holding it (intermittent OSError Errno 22 on the next open).
        if changed:
            check("real config.json left untouched", False, real_cfg)
            with open(real_cfg, "wb") as f:
                f.write(real_backup)


def check_launch_command():
    import app_gui

    with _FakeFrozen(tempfile.gettempdir()):
        cmd = app_gui.calibrator_command("mutation")
        check("frozen exe relaunches itself for the calibrator",
              cmd == [sys.executable, "--calibrate", "mutation"], cmd)
    cmd = app_gui.calibrator_command("goliath")
    check("source run starts the calibrator script with this python",
          cmd[0] == sys.executable and cmd[1].endswith("calibrate_gui.py"), cmd)

    check("argv names the goliath calibrator",
          app_gui.calibrator_from_argv(["x.exe", "--calibrate", "goliath"]) == "goliath")
    check("argv names the mutation calibrator",
          app_gui.calibrator_from_argv(["x.exe", "--calibrate", "mutation"]) == "mutation")
    check("plain start is not a calibrator run",
          app_gui.calibrator_from_argv(["x.exe"]) is None)
    check("unknown calibrator name is ignored",
          app_gui.calibrator_from_argv(["x.exe", "--calibrate", "bogus"]) is None)


def check_goliath_import():
    import tkinter as tk
    from app_gui import MacroStudioApp

    cfg_path = os.path.join(scaling.get_base_dir(), "config.json")
    with open(cfg_path, "rb") as f:  # bytes: keep the file's own line endings
        cfg_backup = f.read()

    root = tk.Tk()
    app = MacroStudioApp(root)
    root.update()
    app._select_profile("Goliath")
    root.update()
    original = copy.deepcopy(app.pm.load_profile("Goliath"))
    try:
        # Seeding: the calibrator must start from the profile's own points.
        app._seed_goliath_calibration()
        with open(cfg_path, "r", encoding="utf-8") as f:
            seeded = json.load(f)
        check("calibrator is seeded with the profile's slot points",
              seeded["slots"][0]["slot_click"] == original["slots"][0]["slot_click"],
              seeded["slots"][0].get("slot_click"))

        # The calibrator saves new points into config.json...
        seeded["slots"][0]["slot_click"] = {"x": 301, "y": 302}
        seeded["slots"][2]["claim_click"] = {"x": 501, "y": 502}
        seeded["common_actions"]["popup_close"] = {"x": 534, "y": 236}
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(seeded, f)

        # ...and closing it must pull them into the active profile.
        app._import_goliath_calibration()
        saved = app.pm.load_profile("Goliath")
        check("slot click reaches the profile",
              saved["slots"][0]["slot_click"] == {"x": 301, "y": 302},
              saved["slots"][0]["slot_click"])
        check("claim click reaches the profile",
              saved["slots"][2]["claim_click"] == {"x": 501, "y": 502},
              saved["slots"][2]["claim_click"])
        check("pop-up close point reaches the profile",
              saved["common_actions"].get("popup_close") == {"x": 534, "y": 236},
              saved["common_actions"])
        check("profile scan regions are kept",
              saved["slots"][0]["scan_region"] == original["slots"][0]["scan_region"],
              saved["slots"][0]["scan_region"])
        check("GUI shows the imported point",
              app.current_cfg["slots"][0]["slot_click"] == {"x": 301, "y": 302},
              app.current_cfg["slots"][0]["slot_click"])
    finally:
        app.pm.save_profile("Goliath", original)
        with open(cfg_path, "wb") as f:
            f.write(cfg_backup)
        root.destroy()


def check_shipped_window_size():
    from profile_manager import ProfileManager

    pm = ProfileManager()
    for name in pm.list_profiles():
        cfg = pm.load_profile(name)
        ws = cfg.get("window_settings")
        if not ws:
            continue
        ref = cfg.get("reference_client", {})
        want = (ref.get("width", scaling.REFERENCE_CLIENT[0]),
                ref.get("height", scaling.REFERENCE_CLIENT[1]))
        got = (ws.get("standard_width"), ws.get("standard_height"))
        check(f"profile '{name}' snaps windows to its calibrated size {want}",
              got == want, got)


def run():
    for section in (check_save_paths, check_launch_command,
                    check_goliath_import, check_shipped_window_size):
        try:
            section()
        except Exception as e:  # report and keep going so every section runs
            check(section.__name__, False, f"{type(e).__name__}: {e}")
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_legacy_checks(tmp_base_dir):
    """Run every check in this script in its own process (it creates a Tk root)."""
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    raise SystemExit(run())
