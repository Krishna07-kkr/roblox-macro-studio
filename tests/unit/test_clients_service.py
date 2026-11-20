"""ClientsService: scan, names, status, events, rename and safe actions (spec 001, US2)."""

from datetime import datetime, timedelta, timezone

import pytest

from conftest import FakeClock
from macro_studio.core import roblox
from macro_studio.tools.clients.service import (
    ClientsService,
    ProcessInfo,
    WindowInfo,
    sort_rows,
)

UTC = timezone.utc
T0 = datetime(2026, 11, 20, 8, 0, 0, tzinfo=UTC)
EPOCH0 = T0.timestamp()


class World:
    """Everything the service looks at, scriptable."""

    def __init__(self):
        self.clock = FakeClock(EPOCH0 + 100)
        self.procs, self.wins, self.logs, self.texts = {}, {}, [], {}
        self.events, self.titles, self.focused, self.trimmed, self.killed = [], {}, [], [], []
        self.names = {}                 # ("user", id) / ("place", id) -> label

    # ---- dependencies handed to the service
    def processes(self):
        return list(self.procs.values())

    def windows(self):
        return list(self.wins.values())

    def list_logs(self):
        return list(self.logs)

    def read_text(self, path, head):
        return self.texts.get((path, head), "")

    def process_info(self, pid):
        return self.procs.get(pid)

    def service(self, **options):
        lookup = FakeLookup(self.names)
        return ClientsService(
            processes=self.processes, windows=self.windows, list_logs=self.list_logs,
            read_text=self.read_text, lookup=lookup, publish=lambda n, **k: self.events.append((n, k)),
            clock=self.clock, wall_clock=self.clock, process_info=self.process_info,
            set_title=lambda hwnd, title: self.titles.__setitem__(hwnd, title),
            focus=self.focused.append, trim=self.trimmed.append, terminate=self.killed.append,
            **options)

    # ---- building the scene
    def client(self, pid, hwnd=None, title="Roblox", minimized=False, responsive=True,
               rss_mb=300, started_s=-60, user=None, place=None):
        started = EPOCH0 + started_s
        self.procs[pid] = ProcessInfo(pid=pid, name="robloxplayerbeta.exe",
                                      rss_bytes=int(rss_mb * 2**20), started=started)
        if hwnd:
            self.wins[pid] = WindowInfo(hwnd=hwnd, pid=pid, title=title, minimized=minimized,
                                        responsive=responsive)
        log_path = f"C:/logs/{pid}_last.log"
        stamp = datetime.fromtimestamp(started + 3, UTC)
        self.logs.append(roblox.LogFile(path=log_path, started=stamp, modified=started))
        if user:
            self.texts[(log_path, True)] = f"game_join_loadtime userid:{user}"
        if place:
            self.texts[(log_path, False)] = f"placeId {place}"


class FakeLookup:
    def __init__(self, names):
        self.names, self.wanted = names, set()

    def want_user(self, uid):
        self.wanted.add(("user", uid))

    def want_place(self, pid):
        self.wanted.add(("place", pid))

    def forget_user(self, uid):
        self.wanted.discard(("user", uid))

    def forget_place(self, pid):
        self.wanted.discard(("place", pid))

    def user_label(self, uid):
        return self.names.get(("user", uid))

    def place_label(self, pid):
        return self.names.get(("place", pid))

    def tick(self):
        pass


@pytest.fixture
def world():
    return World()


# ------------------------------------------------------------------ the list
def test_each_client_row_has_name_game_memory_pid_uptime_status(world):
    world.client(11, hwnd=101, rss_mb=412, started_s=-95, user=7, place=1234567)
    world.names = {("user", 7): "Ann", ("place", 1234567): "Arm Wrestle"}
    row, = world.service().scan()
    assert (row.pid, row.hwnd, row.account, row.game) == (11, 101, "Ann", "Arm Wrestle")
    assert round(row.ram_mb) == 412 and row.uptime_s == 195 and row.status == "Running"


def test_two_clients_get_their_own_names(world):
    world.client(11, hwnd=101, user=7, place=1111111)
    world.client(22, hwnd=102, user=8, place=1111111)
    world.names = {("user", 7): "Ann", ("user", 8): "Bob", ("place", 1111111): "G"}
    rows = {r.pid: r for r in world.service().scan()}
    assert (rows[11].account, rows[22].account) == ("Ann", "Bob")


def test_status_minimised_not_responding_and_no_window(world):
    world.client(1, hwnd=10, minimized=True)
    world.client(2, hwnd=20, responsive=False)
    world.client(3)                                          # process with no window yet
    status = {r.pid: r.status for r in world.service().scan()}
    assert status == {1: "Minimised", 2: "Not responding", 3: "No window"}


def test_a_client_that_closed_disappears_and_its_log_is_released(world):
    world.client(1, hwnd=10, user=7)
    service = world.service()
    service.scan()
    del world.procs[1], world.wins[1]
    assert service.scan() == []


def test_unknown_names_show_as_unresolved_not_blank_errors(world):
    world.client(1, hwnd=10)
    row, = world.service().scan()
    assert row.account == "" and row.game == ""


def test_the_name_lookup_is_asked_for_what_the_log_revealed(world):
    world.client(1, hwnd=10, user=7, place=1234567)
    service = world.service()
    service.scan()
    assert ("user", 7) in service._lookup.wanted and ("place", 1234567) in service._lookup.wanted


# ------------------------------------------------------------------ events
def names_of(world):
    return [name for name, _payload in world.events]


def test_started_and_closed_events_fire_once(world):
    service = world.service()
    service.scan()
    world.client(1, hwnd=10)
    service.scan()
    service.scan()
    assert names_of(world) == ["client_started"]
    del world.procs[1], world.wins[1]
    service.scan()
    assert names_of(world) == ["client_started", "client_closed", "all_closed"]


def test_hung_and_recovered_events(world):
    world.client(1, hwnd=10)
    service = world.service()
    service.scan()
    world.wins[1] = WindowInfo(10, 1, "Roblox", False, False)
    service.scan()
    service.scan()
    world.wins[1] = WindowInfo(10, 1, "Roblox", False, True)
    service.scan()
    assert names_of(world) == ["client_hung", "client_recovered"]     # open before the first scan


def test_the_first_scan_does_not_announce_clients_that_were_already_open(world):
    world.client(1, hwnd=10)
    world.service().scan()
    assert names_of(world) == []


# ------------------------------------------------------------------ sorting
def test_sort_is_numeric_for_numbers_and_ignores_case_for_names(world):
    world.client(9, hwnd=1, rss_mb=900, started_s=-1000)
    world.client(100, hwnd=2, rss_mb=80, started_s=-359_000)         # ~99.7 hours up
    world.client(30, hwnd=3, rss_mb=5000, started_s=-50)
    world.names = {}
    rows = world.service().scan()
    assert [r.pid for r in sort_rows(rows, "pid", True)] == [9, 30, 100]
    assert [r.pid for r in sort_rows(rows, "ram", False)] == [30, 9, 100]
    assert [r.pid for r in sort_rows(rows, "uptime", False)] == [100, 9, 30]      # not as text
    named = [type("R", (), {"account": n, "pid": i})() for i, n in enumerate(["bob", "Ann", "cara"])]
    assert [r.account for r in sort_rows(named, "account", True)] == ["Ann", "bob", "cara"]


# ------------------------------------------------------------------ renaming
def test_windows_are_renamed_to_the_account_name(world):
    world.client(1, hwnd=10, title="Roblox", user=7, place=1111111)
    world.names = {("user", 7): "Ann", ("place", 1111111): "Arm Wrestle"}
    service = world.service(rename=True, rename_with_game=False)
    service.scan()
    service.auto_rename()
    assert world.titles == {10: "Ann"}


def test_the_game_name_can_be_added(world):
    world.client(1, hwnd=10, user=7, place=1111111)
    world.names = {("user", 7): "Ann", ("place", 1111111): "Arm Wrestle"}
    service = world.service(rename=True, rename_with_game=True)
    service.scan()
    service.auto_rename()
    assert world.titles == {10: "Ann - Arm Wrestle"}


def test_an_unchanged_title_is_not_set_again(world):
    world.client(1, hwnd=10, title="Ann", user=7)
    world.names = {("user", 7): "Ann"}
    service = world.service(rename=True)
    service.scan()
    service.auto_rename()
    assert world.titles == {}


def test_nothing_is_renamed_while_the_option_is_off(world):
    world.client(1, hwnd=10, user=7)
    world.names = {("user", 7): "Ann"}
    service = world.service(rename=False)
    service.scan()
    service.auto_rename()
    assert world.titles == {}


def test_a_title_the_player_set_by_hand_is_left_alone(world):
    world.client(1, hwnd=10, user=7)
    world.names = {("user", 7): "Ann"}
    service = world.service(rename=True)
    service.scan()
    service.rename_by_hand(1, "My main")
    service.auto_rename()
    assert world.titles == {10: "My main"}


def test_after_30_seconds_without_a_name_the_title_falls_back_to_the_pid(world):
    world.client(1, hwnd=10, user=7)
    service = world.service(rename=True)
    service.scan()
    service.auto_rename()
    assert world.titles == {}                            # still waiting for the name
    world.clock.advance(31)
    service.scan()
    service.auto_rename()
    assert world.titles == {10: "PID 1 (id 7)"}


# ------------------------------------------------------------------ actions that cannot hit the wrong process
def test_focus_goes_to_the_clients_window(world):
    world.client(1, hwnd=10)
    service = world.service()
    service.scan()
    assert service.focus(1) is True and world.focused == [10]


def test_trim_and_close_work_on_the_process_that_was_listed(world):
    world.client(1, hwnd=10)
    service = world.service()
    service.scan()
    assert service.trim_one(1) is True and world.trimmed == [1]
    assert service.close_one(1) is True and world.killed == [1]


def test_close_refuses_when_the_pid_now_belongs_to_another_program(world):
    world.client(1, hwnd=10)
    service = world.service()
    service.scan()
    world.procs[1] = ProcessInfo(pid=1, name="notepad.exe", rss_bytes=1, started=EPOCH0 + 50)
    assert service.close_one(1) is False and world.killed == []


def test_close_refuses_when_the_pid_was_reused_by_a_new_roblox_client(world):
    world.client(1, hwnd=10, started_s=-60)
    service = world.service()
    service.scan()
    world.procs[1] = ProcessInfo(pid=1, name="robloxplayerbeta.exe", rss_bytes=1, started=EPOCH0 + 90)
    assert service.close_one(1) is False and world.killed == []


def test_close_and_trim_ignore_a_pid_that_was_never_listed(world):
    service = world.service()
    service.scan()
    assert service.close_one(999) is False and service.trim_one(999) is False
    assert world.killed == [] and world.trimmed == []


def test_a_client_that_is_already_gone_is_not_an_error(world):
    world.client(1, hwnd=10)
    service = world.service()
    service.scan()
    del world.procs[1]
    assert service.close_one(1) is False


# ------------------------------------------------------------------ misc
def test_a_scan_that_fails_part_way_keeps_the_last_good_list(world):
    world.client(1, hwnd=10)
    service = world.service()
    assert len(service.scan()) == 1
    world.processes = lambda: (_ for _ in ()).throw(OSError("denied"))
    service._processes = world.processes
    assert len(service.scan()) == 1                       # the previous rows, not a crash


def test_uptime_never_goes_negative(world):
    world.client(1, hwnd=10, started_s=+500)               # clock skew
    row, = world.service().scan()
    assert row.uptime_s == 0 and isinstance(timedelta(seconds=row.uptime_s), timedelta)


def test_an_os_refusal_while_closing_or_trimming_is_reported_not_raised(world):
    world.client(1, hwnd=10)
    service = world.service()
    service.scan()

    def denied(pid):
        raise PermissionError("access denied")
    service._terminate = denied
    service._trim = denied
    assert service.close_one(1) is False
    assert service.trim_one(1) is False


# ------------------------------------------------------------------ error dialogs (FR-013)
def test_error_dialogs_are_closed_only_when_the_option_is_on_and_each_is_logged(world):
    world.client(1, hwnd=10)
    world.client(2, hwnd=20)
    seen, closed, logs = [], [], []
    service = world.service(close_dialogs=True)
    service._dialogs = lambda: [(900, 1), (901, 2), (902, 777)]       # (hwnd, owner pid)
    service._close_dialog = closed.append
    service._log = logs.append
    service.scan()
    assert service.close_error_dialogs() == 2                         # pid 777 is not a client
    assert closed == [900, 901] and len(logs) == 2 and seen == []


def test_error_dialogs_are_left_alone_while_the_option_is_off(world):
    world.client(1, hwnd=10)
    closed = []
    service = world.service(close_dialogs=False)
    service._dialogs = lambda: [(900, 1)]
    service._close_dialog = closed.append
    service.scan()
    assert service.close_error_dialogs() == 0 and closed == []


def test_without_a_dialog_source_nothing_happens(world):
    world.client(1, hwnd=10)
    service = world.service(close_dialogs=True)
    service.scan()
    assert service.close_error_dialogs() == 0


def test_scans_from_two_threads_at_once_do_not_corrupt_anything(world):
    import threading
    for pid in range(1, 9):
        world.client(pid, hwnd=pid * 10, user=pid, place=1000000 + pid)
    service = world.service(rename=True)
    failures = []

    def hammer():
        try:
            for _ in range(300):
                rows = service.scan()
                service.auto_rename()
                assert len(rows) == 8
                service.close_one(3)
                world.killed.clear()
        except Exception as err:        # noqa: BLE001 - collect anything a thread raised
            failures.append(repr(err))
    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert failures == []


def test_other_tools_are_handed_the_rows_after_every_scan(world):
    world.client(1, hwnd=10)
    got = []
    service = world.service(on_rows=got.append)
    service.scan()
    service.scan()
    assert len(got) == 2 and [r.pid for r in got[0]] == [1]
