"""Compatibility alias: moved to macro_studio/calibrators/goliath.py. Removed after one release.

Imported, this name becomes the same module object as the new one (research R14).
Run as a script (`python calibrate_gui.py`, which the studio does from source), it opens the
calibrator window as before.
"""
import sys

if __name__ == "__main__":
    from macro_studio.calibrators.goliath import launch_calibration_gui

    launch_calibration_gui()
else:
    from macro_studio.calibrators import goliath as _module
    from macro_studio.calibrators.goliath import *  # noqa: F401,F403

    sys.modules[__name__] = _module
