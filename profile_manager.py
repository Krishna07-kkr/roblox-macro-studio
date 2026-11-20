"""Compatibility alias: moved to macro_studio/profiles.py. Removed after one release.

The old name becomes the same module object (research R14): assigning
`profile_manager.PROFILES_DIR` still changes the real module. The star import only exists
so type checkers can see the names; at runtime the alias below replaces this module.
"""
import sys

from macro_studio import profiles as _module
from macro_studio.profiles import *  # noqa: F401,F403

sys.modules[__name__] = _module
