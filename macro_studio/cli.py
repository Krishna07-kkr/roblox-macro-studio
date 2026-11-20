"""Command line for running a macro without the studio window.

`python main.py [--mode goliath|mutation]` lands here. It lives outside both runner
modules so neither runner knows the other exists (test_mode_separation).

Runners built here get no shared services, so each creates its own hotkey poller and
automation lock: F6 / Esc stop and F7 pauses exactly as before the restructure.
"""

import argparse
import traceback


def main(argv=None):
    parser = argparse.ArgumentParser(description="Arm Wrestle Simulator Macro Runner")
    parser.add_argument("--mode", "-m", choices=["goliath", "mutation"], default="goliath",
                        help="Macro mode to run: 'goliath' (default) or 'mutation'")
    args, _ = parser.parse_known_args(argv)

    try:
        if args.mode == "mutation":
            from macro_studio.runners.mutation import MutationRunner
            runner = MutationRunner()
        else:
            from macro_studio.runners.goliath import MacroRunner
            runner = MacroRunner()
        runner.run()
    except KeyboardInterrupt:
        print("\nMacro interrupted by user.")
    except Exception as e:
        print(f"\n[ERROR] An unexpected error occurred: {e}")
        traceback.print_exc()
