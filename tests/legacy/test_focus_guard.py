"""A click must never be issued into an unfocused window.

Windows mouse input is window-agnostic: it lands on whatever is in front when
it is injected. So if activation fails and the runner clicks anyway, the pet
select and Mutate presses go into some other window. The guard refuses that.

The second half checks the guard inside the real loop, because a guard that
silently drops an account forever would be its own bug.
"""
import mutation_runner as mr


class FakeWM:
    def __init__(self, ok):
        self.ok = ok
    def force_activate_window(self, hwnd):
        return self.ok


def _runner(activation_ok):
    r = mr.MutationRunner.__new__(mr.MutationRunner)
    r.wm = FakeWM(activation_ok)
    r.running = True
    r.focus_delay = 0.0
    r.log_callback = None
    r.log = lambda m: None
    r.sleep_interruptible = lambda s: None
    return r


def test():
    st = mr.WindowState(1, "Account #1")
    assert _runner(False)._focus(st) is False, "must refuse when activation fails"
    assert st.status == "STUCK"
    assert _runner(True)._focus(mr.WindowState(1, "a")) is True, "must proceed when focused"

    # Config coords must exist, or every click silently no-ops. These are the
    # calibrated points that shipped in dist/, synced back into the source.
    import json
    cfg = json.load(open("profiles/Mutation.json", encoding="utf-8"))
    assert cfg["targets"]["mutate"] == {"x": 535, "y": 384}, cfg["targets"]
    assert cfg["targets"]["pet_select"] == {"x": 182, "y": 220}, cfg["targets"]
    assert cfg["scan_region"] == {"x": 176, "y": 114, "width": 426, "height": 412}, \
        cfg["scan_region"]
    print("focus guard + coords OK")


# ---------------------------------------------------------------------------
# The guard inside the running loop
# ---------------------------------------------------------------------------

class FlakyWM:
    """hwnd 101 always focuses; 102 refuses until `fails_until` attempts pass."""

    def __init__(self, fails_until=99):
        self.fails_until = fails_until
        self.attempts = {101: 0, 102: 0}

    def force_activate_window(self, hwnd, *a, **k):
        self.attempts[hwnd] += 1
        if hwnd == 102 and self.attempts[hwnd] <= self.fails_until:
            return False
        return True

    def get_client_offset(self, hwnd):
        return (0, 0)

    def get_window_title(self, hwnd):
        return f"Roblox {hwnd}"


class FakeMapper:
    is_identity = True
    def point(self, x, y):
        return x, y
    def describe(self):
        return "identity"


class FakeInput:
    def __init__(self):
        self.clicks = []
    def click(self, x, y, client_offset=None, relative=True, hwnd=None):
        self.clicks.append((hwnd, x, y))


def _loop_runner(wm, max_cycles=1):
    r = mr.MutationRunner.__new__(mr.MutationRunner)
    r.log_callback = None
    r.stats_callback = None
    r.frame_callback = None
    r.max_cycles = max_cycles
    r.running = True
    r.paused = False
    r.hud = None
    r.stats = {"cycles": 0, "claims": 0, "adds": 0, "accounts": 0, "active_hwnd": 0}
    r.config = {"meta": {"macro_mode": "mutation"}}
    r.mutation_cfg = {
        "targets": {"pet_select": {"x": 5, "y": 5}, "mutate": {"x": 9, "y": 9}},
        "scan_region": {"x": 0, "y": 0, "width": 4, "height": 4},
    }
    r.focus_delay = r.after_pet_delay = r.after_mutate_delay = r.poll_delay = 0.0
    r.wm = wm
    r.input = FakeInput()
    r.log = lambda m: None
    r._mapper = lambda hwnd: FakeMapper()
    r.read_state = lambda hwnd, mapper: (mr.STATE_PET_LIST, 50.0, 10.0, None)
    r.windows = [mr.WindowState(101, "Account #1"), mr.WindowState(102, "Account #2")]
    r._discover_windows = lambda: r.windows
    return r


def test_loop():
    # One window will not come forward: it gets no clicks, the other still runs.
    wm = FlakyWM(fails_until=99)
    r = _loop_runner(wm, max_cycles=1)
    r.run()
    clicked = {h for h, _, _ in r.input.clicks}
    assert 102 not in clicked, f"clicked an unfocused window: {r.input.clicks}"
    assert 101 in clicked, "a focusable account must still be worked"
    # 102 is stuck specifically on focus; 101 got as far as pressing Mutate
    # (the fake never shows a wheel, so it reports its own retry reason).
    assert r.windows[1].reason == "could not bring the window to the front", \
        r.windows[1].reason
    assert "front" not in r.windows[0].reason, r.windows[0].reason

    # A stuck account must be retried, not dropped for the rest of the run.
    wm2 = FlakyWM(fails_until=2)
    r2 = _loop_runner(wm2, max_cycles=3)
    r2.run()
    clicked2 = {h for h, _, _ in r2.input.clicks}
    assert 102 in clicked2, "stuck account was never retried"
    assert wm2.attempts[102] > 2, wm2.attempts

    # The pet-stacking guard still holds: a pet is never selected twice while an
    # earlier selection is pending.
    st = mr.WindowState(1, "Account #1")
    st.pet_selected = True
    r3 = _loop_runner(FlakyWM(fails_until=0), max_cycles=1)
    r3.input.clicks.clear()
    r3.start_mutation(st)
    pet_clicks = [c for c in r3.input.clicks if c[1] == 5]
    assert pet_clicks == [], f"pet re-selected while one was pending: {pet_clicks}"

    print("focus guard in loop OK (skips unfocused, retries later, no pet stacking)")


if __name__ == "__main__":
    test()
    test_loop()
