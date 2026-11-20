"""The Recorder page: record, save, list, manage, play (spec 003).

Runs in its own process (creates Tk) on a temporary copy of the data. The input hook, the windows
and the player are fakes: no input is ever sent and no window is touched.
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
    """Wait for `condition` inside a real mainloop (workers reach Tk only then)."""
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

    import numpy as np

    from macro_studio.app import MacroStudioApp
    from macro_studio.core.inputhook import RawEvent
    from macro_studio.tools.recorder.player import Result
    from macro_studio.tools.recorder.service import RecorderService

    messagebox.askyesnocancel = lambda *a, **k: False
    root = tk.Tk()
    root.geometry("1180x880+0+0")
    PUMP.append(root)
    app = MacroStudioApp(root)
    page = app.tool("recorder")
    app.tool("macro").pm.save_profile = lambda name, data: True
    for other in ("macro:claim_timers", "live_feed:macro", "memory:auto", "clients:scan"):
        app.ctx.scheduler.remove(other)
    app._switch_view(app.view_titles.index("Recorder"))
    root.update()

    class Hook:
        def __init__(self):
            self.events, self.started = [], False
            self.error = ""

        def start(self):
            self.started = True
            return True

        def stop(self):
            self.started = False

        def drain(self, handle, limit=500):
            batch, self.events = self.events[:limit], self.events[limit:]
            for e in batch:
                handle(e)
            return len(batch)

    class World:
        ring = SimpleNamespace(grab=lambda h: True, clear=lambda: None)

        def window_at(self, x, y):
            return 9

        def client_info(self, hwnd):
            return (0, 0, 800, 600)

        def foreground(self):
            return 9

        def capture(self, hwnd, t):
            return np.zeros((600, 800, 3), dtype=np.uint8)

        def window_under_cursor(self):
            return 9

    class Player:
        def __init__(self):
            self.calls = []
            self.result = Result(True, "Done.", 3)

        def play(self, rec, hwnd, load, speed=1.0, step_timeout=10.0, progress=None, verify=True):
            self.calls.append((rec.name, hwnd, speed, step_timeout))
            self.verify_seen = verify
            return self.result

    hook, world, player = Hook(), World(), Player()
    asked, answers = [], {"confirm": True, "ask": None}
    page._confirm = lambda title, text: asked.append(text) or answers["confirm"]
    page._ask = lambda title, prompt, initial: answers["ask"]
    page.world = world
    page._unsubscribe()                      # ignore any real Roblox clients the Clients tool saw
    page.rows = []
    page.service = RecorderService(store=page.store, hook=hook, world=world,
                                   automation=app.ctx.automation, player_factory=lambda: player,
                                   log=app.ctx.log)

    check("the sidebar offers a Recorder page, next to Settings",
          app.view_titles[3] == "Recorder", app.view_titles)

    # ---- empty
    check("starts with no recordings and says what to do", page.lbl_empty.winfo_ismapped()
          or page.tree.get_children() == (), page.tree.get_children())
    check("Play, Rename, Duplicate, Delete are off with nothing selected",
          page.btn_play._state == "disabled" and page.btn_delete._state == "disabled")
    check("the pop-up guard note is shown", "pop-up guard" in page.lbl_guard.cget("text").lower())

    # ---- record
    page.toggle_record()
    check("Record starts the hook", hook.started and page.service.recording)
    check("the button turns into Stop recording", "Stop" in page.btn_record._text,
          page.btn_record._text)
    hook.events = [RawEvent("move", 1.0, 50, 60), RawEvent("down", 1.2, 50, 60, button="left"),
                   RawEvent("up", 1.3, 50, 60, button="left")]
    page._poll_job()
    check("events become steps", page.service.step_count == 3, page.service.step_count)
    hook.events = [RawEvent("key_down", 2.0, vk=0x77)]                      # F8
    page._poll_job()
    check("F8 ends the recording by itself", wait_for(lambda: not page.service.recording)
          and not hook.started)
    root.update()
    check("a name is suggested and the save row appears",
          page.e_name.get() == "Recording 1" and page.save_row.winfo_ismapped(), page.e_name.get())
    check("the state line says how much was recorded", "3 steps" in page.lbl_state.cget("text"),
          page.lbl_state.cget("text"))

    # ---- save
    page.e_name.set("../evil")
    check("a bad name is refused with a message", page.save_clicked() is False
          and page.lbl_save.cget("text") != "")
    page.e_name.set("Egg run")
    check("a good name saves", page.save_clicked() is True)
    check("it shows in the list", wait_for(lambda: "Egg run" in page.tree.get_children()),
          page.tree.get_children())
    check("and is selected, so Play is ready", page.selected_name() == "Egg run"
          and page.btn_play._state == "normal", page.selected_name())
    check("the file really exists on disk", page.store.exists("Egg run"))

    # ---- empty recording
    page.toggle_record()
    page.toggle_record()
    check("recording nothing says so and offers no save", "Nothing was recorded"
          in page.lbl_state.cget("text") and not page.save_row.winfo_ismapped(),
          page.lbl_state.cget("text"))

    # ---- manage
    answers["ask"] = "Egg run 2"
    page.duplicate_clicked()
    check("duplicate makes a copy", wait_for(lambda: "Egg run 2" in page.tree.get_children()))
    page.tree.selection_set("Egg run 2")
    page._selection_changed()
    answers["ask"] = "Pets"
    page.rename_clicked()
    check("rename changes the name", wait_for(lambda: "Pets" in page.tree.get_children()
                                              and "Egg run 2" not in page.tree.get_children()))
    answers["confirm"] = False
    page.tree.selection_set("Pets")
    page.delete_clicked()
    root.update()
    check("a no keeps the recording", page.store.exists("Pets"))
    answers["confirm"] = True
    page.delete_clicked()
    check("a yes deletes it", wait_for(lambda: "Pets" not in page.tree.get_children())
          and not page.store.exists("Pets"))

    # ---- play
    page.tree.selection_set("Egg run")
    page._selection_changed()
    page.rows = []
    check("Play with no client open explains why", page.play_clicked() is False
          and "client" in page.lbl_play.cget("text").lower(), page.lbl_play.cget("text"))
    page.rows = [SimpleNamespace(pid=4242, hwnd=777, title="Roblox", account="Alt One")]
    page.e_speed.set("9")
    check("a bad speed is refused and outlined", page.play_clicked() is False
          and page.e_speed.error, page.lbl_play.cget("text"))
    page.e_speed.set("1.5")
    page.e_loops.set("2")
    check("Play starts", page.play_clicked() is True)
    check("the run finishes and the page says so",
          wait_for(lambda: "Played" in page.lbl_play.cget("text")), page.lbl_play.cget("text"))
    check("it played the chosen recording on the chosen window at the chosen speed",
          player.calls[:1] == [("Egg run", 777, 1.5, 10)], player.calls)
    check("by default it replays at the exact recorded spots, with no screen check",
          player.verify_seen is False, getattr(player, "verify_seen", "?"))
    check("the lock is free again and Play is back", app.ctx.automation.owner is None
          and page.btn_play._state == "normal" and page.btn_stop._state == "disabled")

    player.result = Result(False, "Step 2: the spot does not look right.", 1)
    page.play_clicked()
    check("a failed run shows its reason",
          wait_for(lambda: "Step 2" in page.lbl_play.cget("text")), page.lbl_play.cget("text"))

    page.stop_clicked()
    check("Stop sets the halt flag", page._halt.is_set())

    # ---- the screen check is an opt-in switch
    page.var_verify.set(True)
    page._verify_changed()
    player.result = Result(True, "Done.", 3)
    page.play_clicked()
    check("turning the screen check on is passed to the player",
          wait_for(lambda: player.verify_seen is True and "Played" in page.lbl_play.cget("text")),
          player.verify_seen)
    page.var_verify.set(False)
    page._verify_changed()

    # ---- the island
    check("the island exists by default", page.island is not None)
    check("and stays hidden with no Roblox window to sit by", not page.island.shown)
    page._rect_of = lambda hwnd: (400, 300, 800, 600) if hwnd == 777 else None
    page._sync_island()
    root.update()
    check("it appears above the chosen client's window", page.island.shown and page.island.hwnd == 777,
          (page.island.shown, page.island.hwnd))
    check("it lists the saved recordings", page.island.names == ["Egg run"], page.island.names)
    page._island_select("Egg run")
    check("choosing in the island chooses on the page", page.selected_name() == "Egg run")

    focused = []
    page.io = SimpleNamespace(focus=lambda hwnd: focused.append(hwnd) or True)
    page._island_record()
    check("Record from the island brings that window to the front first, then records",
          wait_for(lambda: page.service.recording) and focused == [777], (focused, page.service.recording))
    hook.events = [RawEvent("key_down", 3.0, vk=0x77)]
    page._poll_job()
    check("and it can be ended from the island too", wait_for(lambda: not page.service.recording))
    page._set_client(4242)
    check("choosing a client in the dropdown focuses it as well",
          wait_for(lambda: focused[-1] == 777 and len(focused) >= 2), focused)

    page.var_island.set(False)
    page._island_changed()
    check("the island switch removes it", page.island is None)
    page.var_island.set(True)
    page._island_changed()
    check("and brings it back", page.island is not None)
    page._hide_island()                           # what right-clicking the pill does
    check("right-click hides it and turns the switch off for good",
          page.island is None and not page.var_island.get() and page.settings.get("island") is False)
    page.var_island.set(True)
    page._island_changed()

    # ---- leaving
    page.stop()
    check("stopping leaves no scheduled jobs behind", all(
        app.ctx.scheduler.next_run(j) is None for j in ("recorder:poll", "recorder:play")))

    app.shutdown()
    root.destroy()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_recorder_page(tmp_base_dir):
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
