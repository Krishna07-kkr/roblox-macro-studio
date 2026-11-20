"""The popup guard runs before every macro click, in both runners (spec 002, story A7)."""

import numpy as np

from macro_studio.runners.base import RunnerBase
from macro_studio.runners.goliath import MacroRunner
from macro_studio.runners.mutation import MutationRunner


class SpyGuard:
    def __init__(self, allow=True, active=True):
        self.allow = allow
        self.active = active
        self.seen = []

    def ensure_clear(self, hwnd):
        self.seen.append(("guard", hwnd))
        return self.allow


class Wm:
    def is_foreground(self, hwnd):
        return True

    def force_activate_window(self, hwnd, *a, **k):
        return True

    def get_client_offset(self, hwnd):
        return (0, 0)


class Input:
    def __init__(self, order):
        self.order = order
        self.clicks = []

    def click(self, x, y, client_offset=None, relative=True, hwnd=None):
        self.order.append(("click", x, y))
        self.clicks.append((x, y))
        return True


def goliath(guard):
    r = object.__new__(MacroRunner)
    r.wm = Wm()
    r.order = []
    r.input = Input(r.order)
    r.use_relative = True
    r.running = True
    r.logs = []
    r.log = r.logs.append
    if guard is not None:
        r.popup_guard = guard
        guard.seen = r.order        # one list shows the real order of events
    return r


class Mapper:
    def point(self, x, y):
        return x, y


def mutation(guard):
    r = object.__new__(MutationRunner)
    r.wm = Wm()
    r.order = []
    r.input = Input(r.order)
    r.mutation_cfg = {"targets": {"mutate": {"x": 400, "y": 500}}}
    r.logs = []
    r.log = r.logs.append
    if guard is not None:
        r.popup_guard = guard
        guard.seen = r.order
    return r


# ------------------------------------------------------------------ Goliath
def test_goliath_checks_for_the_popup_before_every_click():
    r = goliath(SpyGuard())
    assert r._click_point(7, {"x": 10, "y": 20}, "Slot 1", "the slot") is True
    assert r.order == [("guard", 7), ("click", 10, 20)]


def test_goliath_does_not_click_when_the_guard_says_no():
    r = goliath(SpyGuard(allow=False))
    assert r._click_point(7, {"x": 10, "y": 20}, "Slot 1", "the slot") is False
    assert r.input.clicks == []


def test_goliath_without_a_guard_still_clicks():
    r = goliath(None)                       # how the older tests build a runner
    assert r._click_point(7, {"x": 10, "y": 20}, "Slot 1", "the slot") is True
    assert r.input.clicks == [(10, 20)]


def test_goliath_stops_blind_popup_clicks_once_the_guard_is_active():
    r = goliath(SpyGuard(active=True))
    r.dismiss_popups = True
    r.capture_failures = False
    r.d_after_confirm = 0.0
    r.sleep_interruptible = lambda s: True
    assert r._dismiss_popup(7, {"popup_close": {"x": 534, "y": 236}}, "Account") is False
    assert r.input.clicks == []             # the guard sees the popup first; no blind click


# ------------------------------------------------------------------ Mutation
def test_mutation_checks_for_the_popup_before_every_click():
    r = mutation(SpyGuard())
    assert r._click_target(7, Mapper(), "mutate") is True
    assert r.order == [("guard", 7), ("click", 400, 500)]


def test_mutation_does_not_click_when_the_guard_says_no():
    r = mutation(SpyGuard(allow=False))
    assert r._click_target(7, Mapper(), "mutate") is False
    assert r.input.clicks == []


def test_mutation_without_a_guard_still_clicks():
    r = mutation(None)
    assert r._click_target(7, Mapper(), "mutate") is True


# ------------------------------------------------------------------ shared plumbing
class Probe(RunnerBase):
    name = "probe"

    def __init__(self, config):
        self.config = config
        self.vs = type("Vs", (), {"capture_client_full": lambda s, h: np.zeros((600, 800, 3), np.uint8)})()
        self.wm = Wm()
        self.order = []
        self.input = Input(self.order)
        self.logs = []
        self.log = self.logs.append
        self.pause_events = 0
        self._init_runtime()

    def _on_pause_changed(self):
        self.pause_events += 1

    def _run_loop(self):
        pass


def test_runner_builds_an_inactive_guard_when_no_picture_was_captured(tmp_path):
    r = Probe({})
    r._build_popup_guard(template_dir=str(tmp_path))
    assert r.popup_guard.active is False


def test_guard_pause_pauses_the_runner():
    r = Probe({})
    r._guard_pause()
    assert r.paused is True and r.pause_events == 1


def test_guard_stop_stops_the_runner():
    r = Probe({})
    r._guard_stop()
    assert r.running is False


def test_guard_click_goes_through_the_normal_input_without_the_guard():
    r = Probe({})
    assert r._guard_click(7, 12, 34) is True
    assert r.order == [("click", 12, 34)]


# ------------------------------------------------------------------ the real guard in a runner
def test_goliath_closes_a_real_popup_then_sends_the_intended_click(tmp_path):
    import cv2

    from macro_studio.core.popup_guard import PopupGuard, save_template

    rng = np.random.default_rng(5)
    popup = np.zeros((600, 800, 3), np.uint8)
    cv2.rectangle(popup, (300, 200), (459, 299), (40, 40, 200), -1)
    cv2.rectangle(popup, (435, 202), (457, 221), (0, 0, 255), -1)
    cv2.rectangle(popup, (320, 260), (439, 289), (60, 200, 60), -1)      # the Buy button
    cv2.circle(popup, (340, 240), 14, (0, 255, 255), -1)
    background = rng.integers(0, 256, (600, 800, 3), dtype=np.uint8)
    with_popup = background.copy()
    with_popup[200:300, 300:460] = popup[200:300, 300:460]

    settings = save_template(str(tmp_path), with_popup, box=(300, 200, 160, 100), close=(450, 212))

    r = goliath(None)
    shown = [with_popup, background]                  # popup, then gone after the close click

    def capture(hwnd):
        return shown.pop(0) if len(shown) > 1 else shown[0]

    r.popup_guard = PopupGuard({"popup_guard": settings}, str(tmp_path / "templates"),
                               capture=capture, click=r._guard_click, log=r.log,
                               wait=lambda s: True, pause=r._guard_pause, stop=r._guard_stop)
    r.paused = False
    r._on_pause_changed = lambda: None
    assert r._click_point(7, {"x": 120, "y": 340}, "Slot 1", "the Claim button") is True
    # first the popup's close button, and only then the macro's own click; Buy (360, 275) never
    assert r.input.clicks == [(450, 212), (120, 340)]
    assert (360, 275) not in r.input.clicks
