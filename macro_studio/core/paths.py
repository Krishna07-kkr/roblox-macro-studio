"""Where user data and bundled resources live.

User data (profiles, configs, schedules, logs, captures) sits next to the exe, or in
the project root when run from source. It is anchored to the project root explicitly,
not to this file's folder, so moving modules into packages never moves the data.
"""

import os
import sys

# macro_studio/core/paths.py -> macro_studio/core -> macro_studio -> project root
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


DATA_DIR_ENV = "MACRO_STUDIO_DATA_DIR"


def get_base_dir():
    """Directory for user-writable data: beside the exe, or the project root.

    From source only, MACRO_STUDIO_DATA_DIR moves it elsewhere. The tests use this to run
    against a throwaway copy of the data, so they can never overwrite the real profiles.
    The exe ignores it: its data is always beside the exe.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return os.path.abspath(override)
    return _PROJECT_ROOT


def get_resource_path(relative_path):
    """Path to an asset: the user's copy first, then the exe's bundled copy."""
    base = get_base_dir()
    local_path = os.path.join(base, relative_path)
    if os.path.exists(local_path):
        return local_path

    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", base)
        bundle_path = os.path.join(meipass, relative_path)
        if os.path.exists(bundle_path):
            return bundle_path

    return local_path
