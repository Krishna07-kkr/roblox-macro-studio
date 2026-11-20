"""Compatibility alias: the studio moved to macro_studio/app.py. Removed after one release.

Imported, this name becomes the same module object as macro_studio.app (research R14),
so `from app_gui import MacroStudioApp` keeps working. Run as a script (`python
app_gui.py`, start_gui.bat, the exe entry), it starts the studio as before.
"""
import sys

if __name__ == "__main__":
    from macro_studio.app import main

    main()
else:
    from macro_studio import app as _module
    from macro_studio.app import *  # noqa: F401,F403

    sys.modules[__name__] = _module
