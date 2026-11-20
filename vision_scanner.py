"""Compatibility alias: moved to macro_studio/core/vision_scanner.py. Removed after one release.

The old name becomes the same module object (research R14). The star import only exists
so type checkers can see the names; at runtime the alias below replaces this module.
"""
import sys

from macro_studio.core import vision_scanner as _module
from macro_studio.core.vision_scanner import *  # noqa: F401,F403

sys.modules[__name__] = _module
