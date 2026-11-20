"""AfkService: rounds of key presses to every client, safely (spec 001, US5, FR-040..043)."""

import threading

import pytest

from conftest import FakeClock
from macro_studio.tools.afk.keys import BadKey, Token, parse_sequence
from macro_studio.tools.afk.service import AfkService


class Rng:
    """Deterministic: the middle of every range, or the low end for whole numbers."""

    def uniform(self, a, b):
        return (a + b) / 2

    def randint(self, a, b):
        return a


class IO:
    def __init__(self):
        self.calls = []
        self.wins = [(30, 300, False), (10, 100, False), (20, 200, False)]   # hwnd, pid, minimised
        self.fg = 777
        self.idle = 999.0
        self.fail_activate = set()

    def windows(self):
        return list(self.wins)

    def foreground(self):
        return self.fg

    def activate(self, hwnd):
        self.calls.append(("activate", hwnd))
        return hwnd not in self.fail_activate

    def restore_foreground(self, hwnd):
        self.calls.append(("focus_back", hwnd))

    def minimise(self, hwnd):
        self.calls.append(("minimise", hwnd))

    def restore(self, hwnd):
        self.calls.append(("restore", hwnd))

    def key(self, vk, hold_s):
        self.calls.append(("key", vk, round(hold_s, 3)))

    def mouse(self, hwnd, name):
        self.calls.append(("mouse", hwnd, name))

    def idle_seconds(self):
        return self.idle

    def kinds(self):
        return [c[0] for c in self.calls]


class Automation:
    def __init__(self, free=True):
        self.free = free
        self.stop_event = threading.Event()

    def try_borrow(self, tool):
        return self.free


class World:
    def __init__(self):
        self.io, self.auto, self.events, self.logs = IO(), Automation(), [], []
        self.clock = FakeClock(0.0)
        self.waits = []
        self.stop_after_waits = None

    def wait(self, seconds):
        self.waits.append(seconds)
        if self.stop_after_waits is not None and len(self.waits) >= self.stop_after_waits:
            self.auto.stop_event.set()
            return False
        return not self.auto.stop_event.is_set()

    def service(self, sequence="space,w", **options):
        service = AfkService(io=self.io, automation=self.auto, wait=self.wait, clock=self.clock,
                             rng=Rng(), publish=lambda n, **k: self.events.append(n),
                             log=self.logs.append, **options)
        service.set_tokens(parse_sequence(sequence))
        return service


@pytest.fixture
def world():
    return World()


# ------------------------------------------------------------------ a normal round
def test_every_client_is_brought_to_the_front_then_gets_the_keys_in_pid_order(world):
    report = world.service("space,w").run_round()
    assert report.sent == 3 and report.skipped == []
    activated = [c[1] for c in world.io.calls if c[0] == "activate"]
    assert activated == [10, 20, 30]
    # a client is brought forward before it gets its keys, and each client's keys come together
    assert world.io.kinds()[:4] == ["activate", "key", "key", "activate"]
    assert world.io.calls[0] == ("activate", 10)


def test_hold_time_varies_a_little_and_never_drops_below_20_ms(world):
    service = world.service("space", hold_ms=80)
    service.run_round()
    assert {c[2] for c in world.io.calls if c[0] == "key"} == {0.065}      # 80 - 15
    world.io.calls.clear()
    world.service("space", hold_ms=30).run_round()
    assert {c[2] for c in world.io.calls if c[0] == "key"} == {0.02}       # floor


def test_mouse_tokens_use_the_mouse_and_keys_use_the_keyboard(world):
    world.service("space,lbutton").run_round()
    assert ("mouse", 10, "lbutton") in world.io.calls
    assert sum(1 for c in world.io.calls if c[0] == "key") == 3


def test_a_short_gap_separates_the_presses(world):
    world.service("space,w").run_round()
    assert any(0.06 <= w <= 0.14 for w in world.waits)


# ------------------------------------------------------------------ minimised windows
def test_minimised_clients_are_skipped_by_default(world):
    world.io.wins = [(10, 100, True), (20, 200, False)]
    report = world.service().run_round()
    assert report.sent == 1 and report.skipped == [(100, "minimised")]
    assert ("restore", 10) not in world.io.calls


def test_they_can_be_woken_and_put_back(world):
    world.io.wins = [(10, 100, True)]
    world.service(skip_minimised=False).run_round()
    kinds = world.io.kinds()
    assert kinds.index("restore") < kinds.index("key") < kinds.index("minimise")


# ------------------------------------------------------------------ focus
def test_focus_goes_back_to_what_the_player_was_using(world):
    world.service(restore_focus=True).run_round()
    assert world.io.calls[-1] == ("focus_back", 777)


def test_focus_is_left_alone_when_the_option_is_off(world):
    world.service(restore_focus=False).run_round()
    assert "focus_back" not in world.io.kinds()


def test_a_window_that_will_not_come_forward_is_skipped_and_the_rest_continue(world):
    world.io.fail_activate = {20}
    report = world.service().run_round()
    assert report.sent == 2 and (200, "could not be brought to the front") in report.skipped


# ------------------------------------------------------------------ the guards
def test_nothing_is_sent_while_a_macro_owns_the_input(world):
    world.auto.free = False
    report = world.service().run_round()
    assert world.io.calls == [] and report.sent == 0 and "macro" in report.note.lower()


def test_nothing_is_sent_after_the_emergency_stop(world):
    world.auto.stop_event.set()
    assert world.service().run_round().sent == 0 and world.io.calls == []


def test_a_stop_in_the_middle_ends_the_round_at_once(world):
    world.stop_after_waits = 2
    report = world.service("space,w,s,a").run_round()
    keys = sum(1 for c in world.io.calls if c[0] == "key")
    assert keys < 6 and report.sent < 3


def test_the_idle_guard_waits_while_the_player_is_active(world):
    world.io.idle = 5.0
    report = world.service(idle_guard=True, idle_secs=30).run_round()
    assert report.deferred and world.io.calls == []
    world.io.idle = 31.0
    assert not world.service(idle_guard=True, idle_secs=30).run_round().deferred


def test_the_idle_guard_is_off_by_default(world):
    world.io.idle = 0.0
    assert world.service().run_round().sent == 3


# ------------------------------------------------------------------ no clients
def test_no_clients_sends_nothing_and_raises_the_alert_at_most_every_30_minutes(world):
    world.io.wins = []
    service = world.service()
    service.run_round()
    service.run_round()
    assert world.events == ["afk_none"]
    world.clock.advance(29 * 60)
    service.run_round()
    assert world.events == ["afk_none"]
    world.clock.advance(61)
    service.run_round()
    assert world.events == ["afk_none", "afk_none"]


# ------------------------------------------------------------------ timing and switching
def test_the_next_round_is_the_interval_plus_or_minus_the_jitter_never_under_3_seconds():
    service = AfkService(io=IO(), automation=Automation(), wait=lambda s: True, clock=FakeClock(),
                         rng=Rng(), publish=lambda *a, **k: None, log=print, interval_s=60,
                         jitter_s=5)
    assert 55 <= service.next_delay() <= 65
    tiny = AfkService(io=IO(), automation=Automation(), wait=lambda s: True, clock=FakeClock(),
                      rng=Rng(), publish=lambda *a, **k: None, log=print, interval_s=4, jitter_s=5)
    assert tiny.next_delay() >= 3


def test_the_scheduled_round_does_nothing_while_off_but_send_now_always_works(world):
    service = world.service(enabled=False)
    assert service.run_scheduled() is None and world.io.calls == []
    assert service.run_round().sent == 3


# ------------------------------------------------------------------ defence in depth
def test_the_stop_keys_are_refused_even_if_they_get_past_the_parser(world):
    service = world.service()
    for vk in (0x75, 0x76, 0x1B):
        with pytest.raises(BadKey):
            service.set_tokens([Token("x", "key", vk)])
