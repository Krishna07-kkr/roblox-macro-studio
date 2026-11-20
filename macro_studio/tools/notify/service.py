"""The Notify service: which events become Discord messages, and how often (spec 001, US6).

No Tk, no network and no webhook addresses in the log: webhooks come from `hooks()`, delivery
goes through `send(url, message)`, time from `clock`. An event handler only queues a message
(it can run on any thread and must never wait for Discord); `deliver_pending()`, called from
one background worker, does the slow sending. Fixes over the AutoHotkey version: repeat limits
follow the event and the account (a reused process id no longer resets them), nothing blocks
the interface, and mentions are restricted to exactly the chosen ping.
"""

import queue
from dataclasses import dataclass

from macro_studio.tools.notify import webhook as wh

SUMMARY_LINES = 10


@dataclass(frozen=True)
class Rule:
    title: str
    color: int
    gate_s: int            # a repeat for the same account is held back this long
    critical: bool         # critical events get the ping
    default_on: bool


RULES = {
    "client_started": Rule("Client started", wh.GREEN, 0, False, False),
    "client_closed": Rule("Client closed", wh.RED, 0, False, True),
    "client_hung": Rule("Client not responding", wh.AMBER, 10 * 60, True, True),
    "all_closed": Rule("All clients closed", wh.RED, 60, True, True),
    "ram_over": Rule("Memory over the limit", wh.AMBER, 15 * 60, True, True),
    "afk_none": Rule("Anti-AFK found no client", wh.BLUE, 30 * 60, False, True),
}


def default_options():
    options = {"enabled": False, "embeds": True, "ping": "none", "ping_id": "", "summary_min": 0}
    options.update({f"ev_{name}": rule.default_on for name, rule in RULES.items()})
    return options


def _who(row, pid=None):
    if row is not None and getattr(row, "account", ""):
        return row.account
    return f"PID {getattr(row, 'pid', pid)}" if (row is not None or pid is not None) else "a client"


class NotifyService:
    def __init__(self, hooks, send, clock, log, options=None):
        self._hooks, self._send, self._clock, self._log = hooks, send, clock, log
        self.options = default_options()
        self.options.update(options or {})
        self._outbox = queue.Queue()
        self._last = {}                      # (event, who) -> when it was last queued
        self._warned_no_hooks = False
        self._rows = []
        self._last_summary = None
        self.results = {}                    # webhook name -> (when, ok, text)

    # ------------------------------------------------------------ events in
    def set_rows(self, rows):
        self._rows = list(rows)

    def on_event(self, name, **payload):
        """Queue a message for an event, if it is switched on and not repeating too soon."""
        rule = RULES.get(name)
        if rule is None or not self.options.get("enabled") or not self.options.get(f"ev_{name}"):
            return
        text, subject = self._describe(name, payload)
        key = (name, subject)
        now = self._clock()
        if rule.gate_s and key in self._last and now - self._last[key] < rule.gate_s:
            return
        self._last[key] = now
        self._outbox.put(self._message(rule, text))

    def _describe(self, name, payload):
        row = payload.get("row")
        if name == "client_started":
            return f"{_who(row)} is now running.", _who(row)
        if name == "client_closed":
            return f"{_who(row, payload.get('pid'))} closed.", _who(row, payload.get("pid"))
        if name == "client_hung":
            return f"{_who(row)} stopped responding.", _who(row)
        if name == "all_closed":
            return "Every Roblox client has closed.", "all"
        if name == "ram_over":
            rows = payload.get("rows", [])
            names = ", ".join(f"{_who(r)} ({r.ram_mb:,.0f} MB)" for r in rows)
            return f"Over {payload.get('threshold_mb')} MB: {names}.", "|".join(sorted(_who(r) for r in rows))
        return "Anti-AFK found no Roblox client to send keys to.", "all"

    def _message(self, rule, text):
        ping = wh.Ping(self.options.get("ping", "none"), str(self.options.get("ping_id", ""))) \
            if rule.critical else None
        return wh.build_message(rule.title, text, color=rule.color, ping=ping,
                                embeds=bool(self.options.get("embeds", True)))

    # ------------------------------------------------------------ summary
    def summary_due(self):
        minutes = self.options.get("summary_min", 0)
        if not self.options.get("enabled") or not minutes:
            return False
        return self._last_summary is None or self._clock() - self._last_summary >= minutes * 60

    def send_summary(self):
        self._last_summary = self._clock()
        rows = self._rows
        if rows:
            total = sum(r.ram_mb for r in rows)
            lines = [f"{_who(r)}: {r.ram_mb:,.0f} MB, {r.status}" for r in rows[:SUMMARY_LINES]]
            text = f"{len(rows)} client(s), {total:,.0f} MB in use.\n" + "\n".join(lines)
        else:
            text = "No Roblox client is open."
        self._outbox.put(wh.build_message("Status summary", text, color=wh.BLUE,
                                          embeds=bool(self.options.get("embeds", True))))

    # ------------------------------------------------------------ delivery (worker thread)
    def deliver_pending(self):
        """Send every queued message to every webhook. Returns how many messages went out."""
        delivered = 0
        while True:
            try:
                message = self._outbox.get_nowait()
            except queue.Empty:
                return delivered
            hooks = self._hooks()
            if not hooks:
                if not self._warned_no_hooks:
                    self._warned_no_hooks = True
                    self._log("[NOTIFY] No Discord webhook is set up, so nothing was sent.")
                continue
            self._warned_no_hooks = False
            for name, url in hooks:
                self._record(name, url, self._send(url, message))
            delivered += 1

    def _record(self, name, url, result):
        self.results[name] = (self._clock(), result.ok, result.text)
        if not result.ok:
            self._log(f"[NOTIFY] {name} ({wh.mask_webhook(url)}): {result.text}")

    def test(self, name):
        """Send a test message to one webhook, whatever the other switches say."""
        for hook_name, url in self._hooks():
            if hook_name == name:
                result = self._send(url, wh.build_message(
                    "Test message", "Roblox Macro Studio can reach this channel.", color=wh.GREEN,
                    embeds=bool(self.options.get("embeds", True))))
                self._record(name, url, result)
                return result
        return wh.Result(False, f"There is no webhook called '{name}'.")
