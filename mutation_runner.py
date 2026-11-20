"""Compatibility alias: moved to macro_studio/runners/mutation.py. Removed after one release.

Imported, this name becomes the same module object as the new one (research R14).
Run as a script (`python mutation_runner.py`), it starts the same command line as before.
"""
import sys

if __name__ == "__main__":
    from macro_studio.runners.mutation import main

    main()
else:
    from macro_studio.runners import mutation as _module
    from macro_studio.runners.mutation import *  # noqa: F401,F403

    sys.modules[__name__] = _module
