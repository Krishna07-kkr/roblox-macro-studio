"""The Notify page: webhooks stored encrypted, masked, tested and used (spec 001, US6).

Runs in its own process (creates Tk) on a temporary copy of the data. HTTP is a fake: nothing
leaves the machine.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))      # tests/

failures = []
PUMP = []
TOKEN = "TopSecretToken_987654321"
URL = f"https://discord.com/api/webhooks/123456789/{TOKEN}"


def check(name, cond, detail: object = ""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


def wait_for(condition, timeout=6.0):
    """Wait inside a real mainloop: worker threads reach Tk only while it runs."""
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


class FakeHttp:
    def __init__(self):
        self.posts, self.fail = [], None

    def post(self, url, body, content_type, timeout=10.0):
        from macro_studio.core.net import HttpError
        self.posts.append((url, json.loads(body)))
        if self.fail:
            raise HttpError(self.fail)
        return ""


def run():
    import tkinter as tk
    from tkinter import messagebox

    from macro_studio.app import MacroStudioApp

    answer = {"yes": True}
    messagebox.askyesno = lambda *a, **k: answer["yes"]
    messagebox.askyesnocancel = lambda *a, **k: False
    root = tk.Tk()
    root.geometry("1180x880+0+0")
    PUMP.append(root)
    app = MacroStudioApp(root)
    notify = app.tool("notify")
    app.tool("macro").pm.save_profile = lambda name, data: True
    for other in ("macro:claim_timers", "live_feed:macro", "memory:auto", "clients:scan", "afk:round"):
        app.ctx.scheduler.remove(other)
    app._switch_view(app.view_titles.index("Notifications"))
    root.update()
    http = FakeHttp()
    notify.http = http
    logs = []
    app.ctx.events.subscribe("log", lambda message, **k: logs.append(message))

    check("the sidebar offers a Notifications page", "Notifications" in app.view_titles)
    check("notifications start off", notify.settings.get("enabled") is False
          and notify.service.options["enabled"] is False)

    # adding webhooks
    notify.e_name.set("Main")
    notify.e_url.set("https://evil.example.com/api/webhooks/1/abc")
    check("a wrong address is refused with a reason", notify.add_hook() is False
          and "not a Discord webhook" in notify.lbl_hook_error.cget("text"))
    notify.e_url.set("https://discord.com/api/webhooks/123/short")
    notify.e_name.set("")
    check("a webhook needs a name", notify.add_hook() is False and "name" in
          notify.lbl_hook_error.cget("text").lower())
    notify.e_name.set("Main")
    notify.e_url.set(f"  {URL}  ")
    check("a good address is added (spaces trimmed)", notify.add_hook() is True
          and notify.settings.get("hook_names") == ["Main"])
    check("the address field is emptied so the secret is not kept on screen",
          notify.e_url.get() == "" and notify.e_name.get() == "")
    shown = notify.tree.item("Main", "values")
    check("the list shows the address hidden", shown[1] == "…/webhooks/123456789/****"
          and TOKEN not in " ".join(map(str, shown)), shown)
    check("the secret is readable through the store", dict(notify.hooks()).get("Main") == URL)
    with open(app.ctx.settings.path, encoding="utf-8") as handle:
        settings_text = handle.read()
    check("the settings file never holds the address as text", TOKEN not in settings_text
          and "discord.com/api" not in settings_text)
    notify.e_name.set("main")
    notify.e_url.set(URL)
    check("a duplicate name (any case) is refused", notify.add_hook() is False
          and "already exists" in notify.lbl_hook_error.cget("text"))

    # testing
    notify.tree.selection_set(["Main"])
    notify.test_selected()
    check("Test sends a test message to that webhook", wait_for(lambda: len(http.posts) == 1)
          and http.posts[0][0] == URL and http.posts[0][1]["embeds"][0]["title"] == "Test message",
          http.posts)
    check("and the list shows it was sent", wait_for(lambda: "Sent" in notify.tree.item("Main", "values")[2]),
          notify.tree.item("Main", "values"))
    http.fail = 404
    notify.test_selected()
    check("a failure is shown in the list without the secret", wait_for(
        lambda: "refused" in notify.tree.item("Main", "values")[2])
          and TOKEN not in str(notify.tree.item("Main", "values")), notify.tree.item("Main", "values"))
    http.fail = None

    # a real event, end to end, on the long-lived worker
    http.posts.clear()
    notify.var["enabled"].set(True)
    notify._toggles_changed()
    check("switching on is saved", notify.settings.get("enabled") is True)
    app.ctx.events.publish("client_closed", pid=5, row=type("R", (), {"account": "Ann", "pid": 5})())
    check("a client closing reaches Discord as one message", wait_for(lambda: len(http.posts) == 1)
          and "Ann" in http.posts[0][1]["embeds"][0]["description"], http.posts)
    worker = notify._worker
    app.ctx.events.publish("client_closed", pid=6, row=type("R", (), {"account": "Bob", "pid": 6})())
    wait_for(lambda: len(http.posts) == 2)
    check("every message uses the same long-lived worker thread", notify._worker is worker
          and worker.is_alive())
    check("the full address never reached the log", TOKEN not in "\n".join(logs), logs[-3:])

    # options
    notify.e_summary.set("3")
    check("a summary interval of 3 minutes is refused", notify.save_options() is False
          and "between 1 and 4" in notify.lbl_error.cget("text"), notify.lbl_error.cget("text"))
    notify.e_summary.set("2000")
    check("one above 1440 is refused", notify.save_options() is False
          and "more than 1440" in notify.lbl_error.cget("text"))
    notify.e_summary.set("60")
    notify.e_ping_id.set("not a number")
    check("a non-numeric ping id is refused", notify.save_options() is False)
    notify.e_ping_id.set("12345")
    check("good options are saved", notify.save_options() is True
          and notify.service.options["summary_min"] == 60 and notify.settings.get("ping_id") == "12345")
    notify._set_ping("role")
    check("a ping choice reaches the service", notify.service.options["ping"] == "role")

    # removing
    notify.tree.selection_set(["Main"])
    answer["yes"] = False
    notify.remove_selected()
    check("Remove asks first, and No keeps it", notify.settings.get("hook_names") == ["Main"])
    answer["yes"] = True
    notify.remove_selected()
    check("Yes removes it and empties the stored secret", notify.settings.get("hook_names") == []
          and notify.hooks() == [])

    notify.stop()
    notify._worker.join(2)
    check("stopping ends the worker", not notify._worker.is_alive())

    app.shutdown()
    root.destroy()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_notify_page(tmp_base_dir):
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
