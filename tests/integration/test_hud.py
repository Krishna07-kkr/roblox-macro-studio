"""The floating HUD (command-line mode) is thread-safe (spec 002, A3, finding H8).

Its Tk root lives on its own thread. Other threads (the stop hotkey, the runner) must only
ask it to close; they must never touch Tk. Runs in a fresh process because it creates Tk.
"""

import sys
import time

failures = []


def check(name, cond, detail: object = ""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


def run():
    from macro_studio.core.overlay_hud import FloatingOverlayHUD

    # 1. Pause pressed before the window exists must not crash.
    calls = []
    early = FloatingOverlayHUD(lambda: calls.append("stop"), lambda: calls.append("pause"))
    try:
        early._handle_pause()
        ok = True
    except Exception as err:        # noqa: BLE001 - the test wants to see any failure
        ok = False
        print(err)
    check("pause before the window is built does not raise", ok and calls == ["pause"]
          and early.is_paused is True)

    # 2. Open and close from another thread, many times; each ends cleanly and quickly.
    slowest, problems = 0.0, []
    for i in range(15):
        hud = FloatingOverlayHUD(lambda: None, lambda: None)
        hud.start()
        if not hud.ready.wait(5):
            problems.append(f"{i}: never became ready")
            continue
        began = time.monotonic()
        hud.close()                              # called from this (a different) thread
        hud.thread.join(timeout=3)
        slowest = max(slowest, time.monotonic() - began)
        if hud.thread.is_alive():
            problems.append(f"{i}: thread still running")
        if hud.error is not None:
            problems.append(f"{i}: error {hud.error!r}")
    check("15 open/close cycles from another thread end cleanly", not problems, problems)
    check("closing takes well under half a second", slowest < 0.5, round(slowest, 3))

    # 3. The Stop button path: runner callback runs, window goes away, close is idempotent.
    stopped = []
    hud = FloatingOverlayHUD(lambda: stopped.append(1), lambda: None)
    hud.start()
    hud.ready.wait(5)
    hud.close()
    hud.close()
    hud.thread.join(timeout=3)
    check("closing twice is harmless", not hud.thread.is_alive() and hud.error is None)

    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_hud_is_thread_safe():
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
