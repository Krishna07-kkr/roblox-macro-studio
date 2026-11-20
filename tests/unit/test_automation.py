"""AutomationLock and EventBus (Constitution II and IV)."""

from macro_studio.services.automation import AutomationLock
from macro_studio.services.events import EventBus


def test_one_owner_at_a_time():
    lock = AutomationLock()
    assert lock.acquire("goliath")
    assert not lock.acquire("mutation")
    assert lock.owner == "goliath"
    lock.release("mutation")  # not the owner: ignored
    assert lock.owner == "goliath"
    lock.release("goliath")
    assert lock.owner is None


def test_tools_cannot_borrow_while_a_runner_owns_input():
    lock = AutomationLock()
    assert lock.try_borrow("layout")
    lock.acquire("goliath")
    assert not lock.try_borrow("layout")


def test_emergency_stop_reaches_every_stoppable():
    lock, stopped = AutomationLock(), []

    class Stoppable:
        def __init__(self, name):
            self.name = name

        def on_emergency_stop(self):
            stopped.append(self.name)

    lock.register_stoppable(Stoppable("runner"))
    lock.register_stoppable(Stoppable("afk"))
    lock.acquire("goliath")
    lock.emergency_stop()
    assert stopped == ["runner", "afk"]
    assert lock.stop_event.is_set()
    assert lock.owner == "goliath"          # still winding down; it releases itself when it ends
    assert lock.stopping
    lock.release("goliath")
    assert lock.owner is None and not lock.stopping


def test_failing_stoppable_does_not_block_the_rest():
    lock, stopped = AutomationLock(on_error=lambda o, e: None), []

    class Bad:
        def on_emergency_stop(self):
            raise RuntimeError("x")

    class Good:
        def on_emergency_stop(self):
            stopped.append("good")

    lock.register_stoppable(Bad())
    lock.register_stoppable(Good())
    lock.emergency_stop()
    assert stopped == ["good"]


def test_new_run_clears_previous_stop():
    lock = AutomationLock()
    lock.emergency_stop()
    assert lock.acquire("goliath")
    assert not lock.stop_event.is_set()


def test_user_restart_after_emergency_stop_is_possible():
    # F6 with no runner, then the user turns anti-AFK back on: it must work again,
    # and input stays blocked until that explicit restart.
    lock = AutomationLock()
    lock.emergency_stop()
    assert lock.stop_event.is_set()
    lock.clear_stop()
    assert not lock.stop_event.is_set()
    assert lock.try_borrow("afk")


def test_borrow_depends_only_on_runner_ownership():
    lock = AutomationLock()
    lock.emergency_stop()
    assert lock.try_borrow("layout")  # nobody owns input; stop_event still guards sends


def test_nobody_else_may_start_while_the_stopped_runner_winds_down():
    lock = AutomationLock()
    lock.acquire("goliath")
    lock.emergency_stop()
    assert not lock.acquire("mutation")
    assert not lock.try_borrow("afk")
    lock.release("goliath")
    assert lock.acquire("mutation")


def test_a_runner_that_never_ends_cannot_block_everything_forever(fake_clock):
    errors = []
    lock = AutomationLock(on_error=lambda obj, exc: errors.append((obj, exc)), clock=fake_clock)
    lock.acquire("goliath")
    lock.emergency_stop()
    fake_clock.advance(lock.STOP_GRACE_S - 0.5)
    assert not lock.acquire("mutation")
    fake_clock.advance(1.0)
    assert lock.acquire("mutation")
    assert lock.owner == "mutation"
    assert errors and isinstance(errors[0][1], TimeoutError) and errors[0][0] == "goliath"


def test_an_old_runner_finishing_late_does_not_release_the_new_owner(fake_clock):
    lock = AutomationLock(on_error=lambda obj, exc: None, clock=fake_clock)
    lock.acquire("goliath")
    lock.emergency_stop()
    fake_clock.advance(lock.STOP_GRACE_S + 1)
    lock.acquire("goliath-2")
    lock.release("goliath")
    assert lock.owner == "goliath-2"


def test_unregister_stoppable():
    lock, stopped = AutomationLock(), []

    class S:
        def on_emergency_stop(self):
            stopped.append(1)

    s = S()
    lock.register_stoppable(s)
    lock.unregister_stoppable(s)
    lock.emergency_stop()
    assert stopped == []


def test_event_bus_delivers_payload_and_isolates_failures():
    errors, got = [], []
    bus = EventBus(on_error=lambda event, exc: errors.append(event))
    bus.subscribe("client_closed", lambda **p: 1 / 0)
    bus.subscribe("client_closed", lambda **p: got.append(p))
    bus.publish("client_closed", pid=42, user_name="Alice")
    assert got == [{"pid": 42, "user_name": "Alice"}]
    assert errors == ["client_closed"]


def test_event_bus_unsubscribe():
    got = []
    bus = EventBus()
    unsubscribe = bus.subscribe("all_closed", lambda **p: got.append(1))
    unsubscribe()
    bus.publish("all_closed")
    assert got == []
