"""The Anti-AFK page: validation, apply, recording, Send now and deferring (spec 001, US5).

Runs in its own process (creates Tk) on a temporary copy of the data. The service and the
keyboard are fakes: no key is ever sent and no window is touched.
"""

import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))      # tests/

failures = []
PUMP = []


def check(name, cond, detail: object = ""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


def wait_for(condition, timeout=5.0):
    """Wait for `condition`, running a real mainloop meanwhile.

    A worker thread can only reach Tk (ui_call) while the main thread is inside mainloop();
    during update() the call waits a second and fails, which would hide label updates.
    """
    if condition():
        return True
    root = PUMP[0]
    deadline = time.monotonic() + timeout

    def poll():
        if condition() or time.monotonic() > deadline:
            root.quit()
        else:
            root.after(20, poll)
    root.after(20, poll)
    root.mainloop()
    return bool(condition())


def run():
    import tkinter as tk
    from tkinter import messagebox

    from macro_studio.app import MacroStudioApp
    from macro_studio.tools.afk.keys import VK_ESCAPE, KeyRecorder, vk_for
    from macro_studio.tools.afk.service import Report

    messagebox.askyesnocancel = lambda *a, **k: False
    root = tk.Tk()
    root.geometry("1180x880+0+0")
    PUMP.append(root)
    app = MacroStudioApp(root)
    afk = app.tool("afk")
    app.tool("macro").pm.save_profile = lambda name, data: True
    for other in ("macro:claim_timers", "live_feed:macro", "memory:auto", "clients:scan"):
        app.ctx.scheduler.remove(other)
    app._switch_view(app.view_titles.index("Anti-AFK"))
    root.update()

    class FakeService:
        enabled = False
        interval_s = jitter_s = hold_ms = idle_secs = 0
        idle_guard = restore_focus = skip_minimised = False

        def __init__(self):
            self.tokens, self.rounds, self.report = [], 0, Report(sent=2)

        def set_tokens(self, tokens):
            self.tokens = tokens

        def run_round(self):
            self.rounds += 1
            return self.report

        def run_scheduled(self):
            return self.report if self.enabled else None

    fake = FakeService()
    afk.service = fake

    check("the sidebar offers an Anti-AFK page", "Anti-AFK" in app.view_titles, app.view_titles)
    check("it starts off, with the spec's defaults",
          afk.settings.get("enabled") is False and afk.settings.get("keys") == "space"
          and (afk.settings.get("interval_s"), afk.settings.get("jitter_s"),
               afk.settings.get("hold_ms")) == (60, 5, 80))
    check("the real service starts off too", app.tool("afk").service is fake)

    # validation
    afk.e_keys.set("space, F6")
    check("the stop keys are refused and named", afk.apply() is False and "stop" in
          afk.lbl_error.cget("text") and afk.e_keys.error and fake.tokens == [],
          afk.lbl_error.cget("text"))
    afk.e_keys.set("space, banana")
    check("an unknown key is named", afk.apply() is False and "banana" in afk.lbl_error.cget("text"))
    afk.e_keys.set("space,w")
    afk.e_interval.set("4")
    check("an interval below the range is refused", afk.apply() is False
          and "less than 5" in afk.lbl_error.cget("text") and afk.e_interval.error)
    afk.e_interval.set("30")
    afk.e_hold.set("5000")
    check("a hold time above the range is refused", afk.apply() is False
          and "more than 1000" in afk.lbl_error.cget("text"))
    check("a refused value changed nothing", fake.tokens == [] and fake.interval_s == 0)

    # a good apply
    afk.e_hold.set("90")
    saved = []
    real_save = afk.ctx.settings.save
    afk.ctx.settings.save = lambda: (saved.append(1), real_save())
    check("good values apply", afk.apply() is True and [t.name for t in fake.tokens] == ["space", "w"]
          and (fake.interval_s, fake.hold_ms) == (30, 90) and saved)
    check("and are remembered", afk.settings.get("keys") == "space,w"
          and afk.settings.get("interval_s") == 30)
    check("errors clear once the values are good", afk.lbl_error.cget("text") == ""
          and not afk.e_keys.error)

    # switches
    afk.var_enabled.set(True)
    afk._enabled_changed()
    afk.var_idle.set(True)
    afk._options_changed()
    check("the switches reach the service and are saved", fake.enabled is True and fake.idle_guard
          is True and afk.settings.get("enabled") is True)

    # recording, with a scripted keyboard
    keyboard = SimpleNamespace(down=set(), now=0.0, fg=1)
    afk._recorder_factory = lambda: KeyRecorder(
        is_down=lambda vk: vk in keyboard.down, now=lambda: keyboard.now,
        foreground=lambda: keyboard.fg, studio_hwnd=99)
    afk.toggle_record()
    check("Record turns into Stop recording", afk.btn_record._text == "Stop recording")
    for key in ("a", "lbutton", "s"):
        keyboard.down = {vk_for(key)}
        root.update()
        time.sleep(0.08)
        root.update()
        keyboard.down = set()
        root.update()
        time.sleep(0.08)
        root.update()
    keyboard.down = {VK_ESCAPE}
    wait_for(lambda: afk.recorder is None, 2.0)
    check("Esc ends it and the keys land in the field", afk.e_keys.get() == "a,lbutton,s"
          and afk.btn_record._text == "Record", afk.e_keys.get())
    afk.toggle_record()
    afk.toggle_record()
    check("pressing Record twice cancels cleanly", afk.recorder is None
          and afk.btn_record._text == "Record")

    # send now, and the report text
    afk.e_keys.set("space,w")
    afk.send_now()
    check("Send now runs one round on the scheduler", wait_for(lambda: fake.rounds == 1), fake.rounds)
    check("and says what it did", wait_for(lambda: "Sent to 2 client(s)" in afk.lbl_result.cget("text")),
          afk.lbl_result.cget("text"))
    fake.report = Report(note="A macro is running, so anti-AFK waits.")
    afk.send_now()
    check("a refusal is shown as the reason", wait_for(
        lambda: "macro is running" in afk.lbl_result.cget("text")), afk.lbl_result.cget("text"))

    # deferring while the player is active, then going back to the normal rhythm
    fake.report = Report(deferred=True, note="You are using the PC")
    afk._round_job()
    check("a deferral is remembered", afk._deferred is True)
    fake.report = Report(sent=1)
    afk._round_job()
    check("and cleared once a round goes ahead", afk._deferred is False)

    afk.stop()
    check("stopping leaves no scheduled jobs behind", app.ctx.scheduler.next_run(afk.JOB) is None)

    app.shutdown()
    root.destroy()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_afk_page(tmp_base_dir):
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
