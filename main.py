"""Compatibility alias: moved to macro_studio/runners/goliath.py. Removed after one release.

Imported, this name becomes the same module object as the new one (research R14).
Run as a script (`python main.py`), it starts the same command line as before.
"""
import sys

if __name__ == "__main__":
    from macro_studio.cli import main as cli_main

    cli_main()
else:
    from macro_studio.runners import goliath as _module
    from macro_studio.runners.goliath import *  # noqa: F401,F403

    sys.modules[__name__] = _module
