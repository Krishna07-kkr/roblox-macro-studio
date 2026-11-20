"""Compatibility alias: moved to macro_studio/core/scaling.py. Removed after one release.

The old name becomes the same module object (research R14), so code that assigns module
globals through the old name still changes the real module. The star import only exists
so type checkers can see the names; at runtime the alias below replaces this module.
"""
import sys

from macro_studio.core import scaling as _module
from macro_studio.core.scaling import *  # noqa: F401,F403

sys.modules[__name__] = _module
