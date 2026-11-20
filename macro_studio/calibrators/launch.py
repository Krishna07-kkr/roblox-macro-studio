"""How the studio opens a calibrator window in its own process.

The exe relaunches itself with --calibrate: the calibrators are bundled inside it, and a
bare "python <script>" needs Python plus every package installed, which the standalone
build promises not to need. From source, the top-level calibrator script runs with the
same interpreter.
"""

import os
import sys

from macro_studio.core.paths import get_base_dir

CALIBRATOR_SCRIPTS = {"goliath": "calibrate_gui.py", "mutation": "calibrate_mutation.py"}


def calibrator_command(kind):
    """Command line that opens a calibrator window in a separate process."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--calibrate", kind]
    return [sys.executable, os.path.join(get_base_dir(), CALIBRATOR_SCRIPTS[kind])]


def calibrator_from_argv(argv):
    """'goliath' / 'mutation' when started as `--calibrate <kind>`, else None."""
    if len(argv) >= 3 and argv[1] == "--calibrate" and argv[2] in CALIBRATOR_SCRIPTS:
        return argv[2]
    return None


def run_calibrator(kind):
    """Open the calibrator window for `kind` in this process (blocks until it closes)."""
    if kind == "goliath":
        from macro_studio.calibrators.goliath import launch_calibration_gui
        launch_calibration_gui()
    elif kind == "mutation":
        from macro_studio.calibrators.mutation import launch
        launch()
    else:
        raise ValueError(f"Unknown calibrator {kind!r}")
