"""Compatibility alias: moved to macro_studio/calibrators/mutation.py. Removed after one release.

Imported, this name becomes the same module object as the new one (research R14).
Run as a script (`python calibrate_mutation.py`, which the studio does from source), it opens the
calibrator window as before.
"""
import sys

if __name__ == "__main__":
    from macro_studio.calibrators.mutation import launch

    launch()
else:
    from macro_studio.calibrators import mutation as _module
    from macro_studio.calibrators.mutation import *  # noqa: F401,F403

    sys.modules[__name__] = _module
