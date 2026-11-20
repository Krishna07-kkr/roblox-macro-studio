"""The Clients page: rows, sorting, selection, actions and options (spec 001, US2).

Runs in its own process (creates Tk) on a temporary copy of the data. The page's service is
replaced by a recording fake, so no real process, window or network is touched.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))      # tests/

failures = []


def check(name, cond, detail: object = ""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


class FakeService:
    rename = False
    rename_with_game = False
    close_dialogs = False

    def __init__(self):
        self.calls = []

    def scan(self):
        return []

    def auto_rename(self):
        pass

    def close_error_dialogs(self):
        return 0

    def focus(self, pid):
        self.calls.append(("focus", pid))
        return True

    def trim_one(self, pid):
        self.calls.append(("trim", pid))
        return True

    def close_one(self, pid):
        self.calls.append(("close", pid))
        return True

    def rename_by_hand(self, pid, title):
        self.calls.append(("rename", pid, title))
        return True


def wait_for(condition, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


def run():
    import tkinter as tk
    from tkinter import messagebox, simpledialog

    from macro_studio.app import MacroStudioApp
    from macro_studio.tools.clients.service import ClientRow

    answers = {"yes": False}
    infos = []
    messagebox.askyesno = lambda *a, **k: answers["yes"]
    messagebox.showinfo = lambda t, m, **k: infos.append((t, m))
    messagebox.askyesnocancel = lambda *a, **k: False
    asked = {"title": "Main account"}
    simpledialog.askstring = lambda *a, **k: asked["title"]

    root = tk.Tk()
    root.geometry("1180x880+0+0")
    app = MacroStudioApp(root)
    clients = app.tool("clients")
    macro = app.tool("macro")
    macro.pm.save_profile = lambda name, data: True
    root.update()

    check("the sidebar offers a Clients page", "Clients" in app.view_titles, app.view_titles)
    app._switch_view(app.view_titles.index("Clients"))        # widgets only map when shown
    root.update()

    fake = FakeService()
    clients.service = fake

    def row(pid, account, game, ram, uptime, status="Running"):
        return ClientRow(pid=pid, hwnd=pid * 10, title="Roblox", account=account, game=game,
                         ram_mb=ram, uptime_s=uptime, status=status, started=0.0,
                         process_name="robloxplayerbeta.exe", user_id=1, place_id=2, resolved=True)

    rows = [row(30, "Cara", "Arm Wrestle", 520.4, 4000), row(9, "ann", "Arm Wrestle", 880.0, 359_000),
            row(100, "Bob", "Arm Wrestle", 310.9, 59, "Not responding")]
    clients.show(rows)
    root.update()

    def column(key):
        index = [c[0] for c in __import__("macro_studio.tools.clients.tool", fromlist=["x"]).COLUMNS].index(key)
        return [clients.tree.item(i, "values")[index] for i in clients.tree.get_children()]

    check("every client is a row", len(clients.tree.get_children()) == 3)
    check("the default order is biggest memory first", column("pid") == [9, 30, 100]
          or [str(x) for x in column("pid")] == ["9", "30", "100"], column("pid"))
    check("the summary adds up memory", "3 clients" in clients.lbl_summary.cget("text")
          and "1,711" in clients.lbl_summary.cget("text"), clients.lbl_summary.cget("text"))
    check("uptime shows hours, even past 99", "99:43:20" in column("uptime"), column("uptime"))
    check("status is shown", "Not responding" in column("status"))

    clients.sort_by("pid")
    check("a number column sorts biggest first on the first click",
          [str(x) for x in column("pid")] == ["100", "30", "9"], column("pid"))
    clients.sort_by("pid")
    check("a second click reverses it", [str(x) for x in column("pid")] == ["9", "30", "100"],
          column("pid"))
    clients.sort_by("account")
    check("names sort ignoring case", column("account") == ["ann", "Bob", "Cara"], column("account"))
    check("the sorted column shows an arrow",
          "▲" in clients.tree.heading("account", "text"), clients.tree.heading("account", "text"))

    # selection and refresh
    clients.tree.selection_set(["30"])
    clients.show(rows)
    check("a refresh keeps the selection", clients.selected_pids() == [30], clients.selected_pids())
    clients.show([row(30, "Cara", "Arm Wrestle", 600, 4001)])
    check("a client that disappeared drops out of the selection and the list",
          clients.selected_pids() == [30] and len(clients.tree.get_children()) == 1)
    clients.show(rows)
    clients.select_all()
    check("Select all selects every row", sorted(clients.selected_pids()) == [9, 30, 100])
    clients.select_none()
    check("Select none clears it", clients.selected_pids() == [])

    # actions go to the service, one at a time, off the Tk thread
    clients.tree.selection_set(["30", "9"])
    clients.focus_selected()
    check("Focus reaches every selected client", wait_for(lambda: ("focus", 30) in fake.calls
                                                          and ("focus", 9) in fake.calls), fake.calls)
    clients.trim_selected()
    check("Trim reaches every selected client", wait_for(lambda: ("trim", 30) in fake.calls
                                                         and ("trim", 9) in fake.calls), fake.calls)

    answers["yes"] = False
    clients.close_selected()
    time.sleep(0.2)
    check("Close asks first, and No closes nothing", not any(c[0] == "close" for c in fake.calls))
    answers["yes"] = True
    clients.close_selected()
    check("Yes closes the selected clients", wait_for(lambda: ("close", 30) in fake.calls
                                                      and ("close", 9) in fake.calls), fake.calls)

    clients.rename_selected()
    check("Rename needs exactly one client", infos and "one client" in infos[-1][1], infos)
    clients.tree.selection_set(["30"])
    clients.rename_selected()
    check("Rename sends the new title for that client",
          wait_for(lambda: ("rename", 30, "Main account") in fake.calls), fake.calls)

    # empty state
    clients.show([])
    root.update()
    check("an empty list says so", clients.empty_label.winfo_ismapped()
          and clients.lbl_summary.cget("text") == "No clients")
    clients.show(rows)
    root.update()
    check("the message goes away when a client appears", not clients.empty_label.winfo_ismapped())

    # options
    saved = []
    clients.ctx.settings.save = lambda: saved.append(1)
    clients.var_rename.set(True)
    clients._set_option("rename", True)
    check("an option is applied to the service and saved", fake.rename is True and saved,
          (fake.rename, saved))
    check("error dialogs are off by default", clients.settings.get("close_dialogs") is False)

    # the real service works on a PC with no Roblox open
    from macro_studio.tools.clients.service import ClientsService
    real = clients._build_service()
    check("the real service builds", isinstance(real, ClientsService))
    check("a real scan returns a list without raising", isinstance(real.scan(), list))

    app.shutdown()
    root.destroy()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_clients_page(tmp_base_dir):
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
