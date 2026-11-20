"""NotifyService: which events become Discord messages, to whom, and how often (US6)."""

from types import SimpleNamespace

from conftest import FakeClock
from macro_studio.tools.notify.service import NotifyService
from macro_studio.tools.notify.webhook import Result

SECRET = "SuperSecretToken123"
URL_A = f"https://discord.com/api/webhooks/111/{SECRET}A"
URL_B = f"https://discord.com/api/webhooks/222/{SECRET}B"


def row(pid, account="Ann", ram_mb=400.0):
    return SimpleNamespace(pid=pid, account=account, ram_mb=ram_mb, status="Running")


class World:
    def __init__(self, hooks=((" Main", URL_A), ("Backup", URL_B))):
        self.clock = FakeClock(1000.0)
        self.sent, self.logs = [], []
        self.fail = set()
        self.hooks = [(n.strip(), u) for n, u in hooks]

    def send(self, url, message, file=None):
        self.sent.append((url, message))
        return Result(False, "HTTP 404") if url in self.fail else Result(True, "Sent")

    def service(self, **options):
        defaults = {"enabled": True, "embeds": True, "ping": "none", "ping_id": ""}
        defaults.update(options)
        return NotifyService(hooks=lambda: list(self.hooks), send=self.send, clock=self.clock,
                             log=self.logs.append, options=defaults)

    def messages(self, url=None):
        return [m for u, m in self.sent if url is None or u == url]


def titles(world, url=None):
    return [m["embeds"][0]["title"] if "embeds" in m else m["content"] for m in world.messages(url)]


def fire(service, name, **payload):
    service.on_event(name, **payload)
    return service.deliver_pending()


# ------------------------------------------------------------------ switches
def test_nothing_is_sent_while_notifications_are_off():
    world = World()
    fire(world.service(enabled=False), "client_closed", pid=1, row=row(1))
    assert world.sent == []


def test_each_event_type_can_be_switched_off_and_started_is_off_by_default():
    world = World()
    service = world.service()
    fire(service, "client_started", row=row(1))
    assert world.sent == []
    service.options["ev_client_started"] = True
    fire(service, "client_started", row=row(2))
    assert len(world.sent) == 2                       # one message per webhook
    service.options["ev_client_closed"] = False
    world.sent.clear()
    fire(service, "client_closed", pid=1, row=row(1))
    assert world.sent == []


def test_an_event_nobody_asked_about_is_ignored():
    world = World()
    fire(world.service(), "something_else")
    assert world.sent == []


def test_no_webhooks_means_nothing_to_send_and_one_log_line_not_many():
    world = World(hooks=())
    service = world.service()
    fire(service, "client_closed", pid=1, row=row(1))
    fire(service, "client_closed", pid=2, row=row(2, "Bob"))
    assert world.sent == [] and sum("webhook" in line.lower() for line in world.logs) == 1


# ------------------------------------------------------------------ delivery
def test_every_webhook_gets_its_own_copy_exactly_once_per_occurrence():
    world = World()
    service = world.service()
    fire(service, "client_closed", pid=1, row=row(1))
    fire(service, "client_closed", pid=2, row=row(2, "Bob"))
    assert [u for u, _ in world.sent].count(URL_A) == 2 and [u for u, _ in world.sent].count(URL_B) == 2


def test_a_failing_webhook_does_not_stop_the_others_and_its_result_is_kept():
    world = World()
    world.fail = {URL_A}
    service = world.service()
    fire(service, "client_closed", pid=1, row=row(1))
    assert len(world.messages(URL_B)) == 1
    assert service.results["Main"][1] is False and service.results["Backup"][1] is True
    assert "404" in service.results["Main"][2]


def test_messages_wait_in_a_queue_so_the_caller_never_blocks():
    world = World()
    service = world.service()
    service.on_event("client_closed", pid=1, row=row(1))
    assert world.sent == []                          # nothing sent from the event handler itself
    assert service.deliver_pending() == 1 and len(world.sent) == 2


def test_a_test_message_goes_to_one_webhook_even_when_notifications_are_off():
    world = World()
    service = world.service(enabled=False)
    result = service.test("Backup")
    assert result.ok and [u for u, _ in world.sent] == [URL_B]
    assert service.results["Backup"][1] is True


def test_testing_a_webhook_that_does_not_exist_is_a_clean_failure():
    assert not World().service().test("Nobody").ok


# ------------------------------------------------------------------ repeat limits
def test_not_responding_repeats_at_most_every_10_minutes_per_account():
    world = World()
    service = world.service()
    fire(service, "client_hung", row=row(1, "Ann"))
    world.clock.advance(9 * 60)
    fire(service, "client_hung", row=row(1, "Ann"))
    assert len(world.messages(URL_A)) == 1
    fire(service, "client_hung", row=row(2, "Bob"))              # another account: not held back
    assert len(world.messages(URL_A)) == 2
    world.clock.advance(61)
    fire(service, "client_hung", row=row(1, "Ann"))
    assert len(world.messages(URL_A)) == 3


def test_the_repeat_limit_follows_the_account_not_the_process_id():
    # The AutoHotkey version keyed it by pid, so a reused pid reset the limit.
    world = World()
    service = world.service()
    fire(service, "client_hung", row=row(11, "Ann"))
    fire(service, "client_hung", row=row(99, "Ann"))             # same account, a new pid
    assert len(world.messages(URL_A)) == 1


def test_memory_repeats_every_15_minutes_and_all_closed_every_minute():
    world = World()
    service = world.service()
    over = [row(1, "Ann", 900)]
    fire(service, "ram_over", rows=over, threshold_mb=350)
    world.clock.advance(14 * 60)
    fire(service, "ram_over", rows=over, threshold_mb=350)
    assert len(world.messages(URL_A)) == 1
    world.clock.advance(61)
    fire(service, "ram_over", rows=over, threshold_mb=350)
    assert len(world.messages(URL_A)) == 2
    fire(service, "all_closed")
    fire(service, "all_closed")
    assert titles(world, URL_A).count("All clients closed") == 1


# ------------------------------------------------------------------ the message itself
def test_titles_and_text_say_what_happened():
    world = World()
    service = world.service()
    fire(service, "client_closed", pid=5, row=row(5, "Ann"))
    fire(service, "client_hung", row=row(6, "Bob"))
    fire(service, "ram_over", rows=[row(7, "Cara", 900)], threshold_mb=350)
    fire(service, "afk_none")
    text = json_text(world.messages(URL_A))
    assert "Ann" in text and "Bob" in text and "Cara" in text and "350" in text
    assert "no Roblox client" in text.lower() or "no roblox client" in text.lower()


def json_text(messages):
    import json
    return json.dumps(messages)


def test_a_ping_is_added_only_to_the_events_that_matter():
    world = World()
    service = world.service(ping="everyone")
    fire(service, "client_closed", pid=1, row=row(1))            # routine: no ping
    fire(service, "client_hung", row=row(2, "Bob"))              # critical: ping
    first, second = world.messages(URL_A)
    assert first["allowed_mentions"] == {"parse": []} and first.get("content", "") == ""
    assert second["content"] == "@everyone" and second["allowed_mentions"] == {"parse": ["everyone"]}


def test_a_user_or_role_ping_uses_the_id_and_a_bad_id_sends_no_ping():
    world = World()
    fire(world.service(ping="user", ping_id="42"), "client_hung", row=row(1))
    assert world.messages(URL_A)[0]["content"] == "<@42>"
    world2 = World()
    fire(world2.service(ping="role", ping_id="not a number"), "client_hung", row=row(1))
    assert world2.messages(URL_A)[0].get("content", "") == ""


def test_plain_messages_are_used_when_embeds_are_off():
    world = World()
    fire(world.service(embeds=False), "client_closed", pid=1, row=row(1, "Ann"))
    message = world.messages(URL_A)[0]
    assert "embeds" not in message and "Ann" in message["content"]


# ------------------------------------------------------------------ secrets
def test_the_full_webhook_address_never_reaches_the_log():
    world = World()
    world.fail = {URL_A}
    service = world.service()
    fire(service, "client_closed", pid=1, row=row(1))
    service.test("Main")
    everything = "\n".join(world.logs) + repr(service.results)
    assert SECRET not in everything


# ------------------------------------------------------------------ the periodic summary
def test_the_summary_lists_clients_and_total_memory():
    world = World()
    service = world.service()
    service.set_rows([row(1, "Ann", 400.0), row(2, "Bob", 600.0)])
    service.send_summary()
    service.deliver_pending()
    text = json_text(world.messages(URL_A))
    assert "2 client" in text and "1,000 MB" in text and "Ann" in text and "Bob" in text


def test_the_summary_is_due_only_when_switched_on_and_the_interval_has_passed():
    world = World()
    service = world.service(summary_min=0)
    assert service.summary_due() is False
    service.options["summary_min"] = 60
    assert service.summary_due() is True                  # never sent yet
    service.send_summary()
    world.clock.advance(59 * 60)
    assert service.summary_due() is False
    world.clock.advance(61)
    assert service.summary_due() is True


def test_a_summary_with_no_clients_says_so():
    world = World()
    service = world.service()
    service.set_rows([])
    service.send_summary()
    service.deliver_pending()
    assert "no" in json_text(world.messages(URL_A)).lower()
