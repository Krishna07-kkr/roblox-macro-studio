"""Smoke-test that the redesigned GUI still drives the same config wiring."""

import copy
import tkinter as tk

from app_gui import MacroStudioApp

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


def run():
    from tkinter import messagebox

    # Switching profiles with unsaved edits asks "save first?". Nobody is there to
    # answer, so answer "don't save": the test's values never reach the profile file.
    messagebox.askyesnocancel = lambda *a, **k: False
    root = tk.Tk()
    app = MacroStudioApp(root)
    root.update()

    original = copy.deepcopy(app.current_cfg)

    # try/finally so a failed check can never leave test values in a real profile.
    try:

        # 1. Round-trip: edit fields, sync, confirm config picked up the new values.
        app.slot_ui_rows[0]["e_sc_x"].set(999)
        app.slot_ui_rows[0]["e_sr_w"].set(123)
        app.e_ps_x.set(77)
        app.e_cy_y.set(88)
        app.e_win_w.set(1024)
        app.e_diag_gap.set(12)
        app.e_d_cycle.set(9.5)

        check("_sync_ui_to_config returns True", app._sync_ui_to_config() is True)
        cfg = app.current_cfg
        check("slot_click.x synced", cfg["slots"][0]["slot_click"]["x"] == 999,
              cfg["slots"][0]["slot_click"])
        check("scan_region.width synced", cfg["slots"][0]["scan_region"]["width"] == 123,
              cfg["slots"][0]["scan_region"])
        check("pet_select.x synced", cfg["common_actions"]["pet_select"]["x"] == 77)
        check("confirm_yes.y synced", cfg["common_actions"]["confirm_yes"]["y"] == 88)

        # Pop-up close: 0,0 means "not calibrated" and must not reach the config,
        # or the runner would click the top-left corner of the game window.
        check("popup_close absent while left at 0,0",
              "popup_close" not in cfg["common_actions"], cfg["common_actions"])
        app.e_pc_x.set(534)
        app.e_pc_y.set(236)
        app._sync_ui_to_config()
        check("popup_close saved once set",
              cfg["common_actions"].get("popup_close") == {"x": 534, "y": 236},
              cfg["common_actions"].get("popup_close"))
        app.e_pc_x.set(0)
        app.e_pc_y.set(0)
        app._sync_ui_to_config()
        check("clearing popup_close removes it again",
              "popup_close" not in cfg["common_actions"], cfg["common_actions"])
        check("window width synced", cfg["window_settings"]["standard_width"] == 1024)
        check("diagonal gap synced", cfg["window_settings"]["diagonal_gap"] == 12)
        check("cycle delay synced (float)", cfg["delays"]["cycle_delay"] == 9.5)
        check("slot count preserved", len(cfg["slots"]) == len(original.get("slots", [])))

        # 2. Toggles write booleans through.
        before = app.var_arrange.get()
        app.var_arrange.set(not before)
        app._sync_ui_to_config()
        check("toggle writes bool", cfg["window_settings"]["auto_arrange_windows"] == (not before))
        check("hud flag present", "show_floating_stop_button" in cfg["ui"])
        app.var_arrange.set(before)

        # 3. Add / delete slot rebuilds the rows and the preview cards together.
        n = len(app.current_cfg["slots"])
        app._add_slot_entry()
        check("add slot -> rows", len(app.slot_ui_rows) == n + 1, len(app.slot_ui_rows))
        app._delete_slot_entry(n)
        check("delete slot -> rows", len(app.slot_ui_rows) == n, len(app.slot_ui_rows))

        # 4. Runner callbacks used by MacroRunner must not raise.
        try:
            app._update_stats({"accounts": 2, "cycles": 5, "claims": 3, "adds": 4,
                               "active_hwnd": 123, "active_account": "Account #2",
                               "active_title": "Roblox"})
            app._update_slot_frame(1, None, "CLAIM_READY", "green claim button")
            app._update_slot_frame(1, None, "SOME_UNKNOWN_STATE", "x" * 80)
            app._append_log("hello")
            root.update()
            check("runner callbacks safe", True)
        except Exception as e:
            check("runner callbacks safe", False, e)

        check("stats reached metric label", app.metric_cycles.cget("text") == "5",
              app.metric_cycles.cget("text"))

        # 5. Start/stop button state machine (without launching a real run).
        app._on_runner_stopped()
        check("stop resets start button", app.btn_start._state == "normal")
        check("stop disables stop button", app.btn_stop._state == "disabled")

        # 6. View switching sets nav + title.
        app._switch_view(2)
        check("view switch title", app.lbl_page_title.cget("text") == "Settings")
        check("view switch nav active", app.nav_items[2]._active is True)
        app._switch_view(0)

        # 7. Profile switch reloads config and refreshes fields.
        if len(app.profiles) > 1:
            other = [p for p in app.profiles if p != app.active_profile_name][0]
            app._select_profile(other)
            root.update()
            check("profile switched", app.active_profile_name == other)
            if app._cfg_mode() == "mutation":
                # Mutation profiles show one targets card, not per-slot rows.
                check("mutation page has no slot rows", app.slot_ui_rows == [])
                check("mutation targets card built", app.e_mut_mt_x is not None)
            else:
                check("profile rows rebuilt",
                      len(app.slot_ui_rows) == len(app.current_cfg["slots"]))
        else:
            print("SKIP  profile switch (only one profile present)")

        # 8. Mutation mode writes the keys MutationRunner actually reads.
        if "Mutation" in app.profiles:
            mut_original = copy.deepcopy(app.pm.load_profile("Mutation"))
            # try/finally: a failing check must not leave test coordinates behind in
            # the real profile - that would silently arm the macro with junk points.
            try:
                app._select_profile("Mutation")
                root.update()
                check("mutation mode active", app._cfg_mode() == "mutation")
                app.e_mut_ps_x.set(111)
                app.e_mut_mt_y.set(222)
                app.e_mut_sr_w.set(333)
                check("mutation sync returns True", app._sync_ui_to_config() is True)
                mcfg = app.current_cfg
                check("targets.pet_select.x synced", mcfg["targets"]["pet_select"]["x"] == 111,
                      mcfg["targets"]["pet_select"])
                check("targets.mutate.y synced", mcfg["targets"]["mutate"]["y"] == 222,
                      mcfg["targets"]["mutate"])
                check("scan_region.width synced", mcfg["scan_region"]["width"] == 333,
                      mcfg["scan_region"])
                # No Goliath-shaped keys may survive in a mutation profile.
                check("no goliath slots leak", "slots" not in mcfg, list(mcfg))
                check("no common_actions leak", "common_actions" not in mcfg, list(mcfg))
                check("no claim_schedule leak", "claim_schedule" not in mcfg, list(mcfg))
                check("mutation profile passes preflight",
                      app._preflight_check() is None, app._preflight_check())
            finally:
                app.pm.save_profile("Mutation", mut_original)
        else:
            print("SKIP  mutation target wiring (no Mutation profile)")

    finally:
        app.pm.save_profile("Goliath", original)

    root.destroy()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())


def test_legacy_checks(tmp_base_dir):
    """Run every check in this script in its own process (it creates a Tk root)."""
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output
