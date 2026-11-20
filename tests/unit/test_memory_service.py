"""MemoryService: trim over-limit clients safely (spec 001, US3, FR-020)."""

from types import SimpleNamespace

import pytest

from conftest import FakeClock
from macro_studio.core.processes import ProcessInfo
from macro_studio.tools.memory.service import MAX_THRESHOLD_MB, MIN_THRESHOLD_MB, MemoryService


def row(pid, ram_mb, started=1000.0, name="robloxplayerbeta.exe", account=""):
    return SimpleNamespace(pid=pid, ram_mb=ram_mb, started=started, process_name=name,
                           account=account)


class World:
    def __init__(self):
        self.clock = FakeClock(5000.0)
        self.trimmed, self.events, self.logs = [], [], []
        self.live = {}                 # pid -> ProcessInfo the OS would report now
        self.denied = set()
        self.after_mb = {}             # pid -> memory after the trim

    def trim(self, pid):
        if pid in self.denied:
            raise PermissionError("access denied")
        self.trimmed.append(pid)
        if pid in self.after_mb and pid in self.live:
            old = self.live[pid]
            self.live[pid] = ProcessInfo(pid, old.name, int(self.after_mb[pid] * 2**20), old.started)

    def info(self, pid):
        return self.live.get(pid)

    def service(self, rows, **options):
        for r in rows:
            self.live.setdefault(r.pid, ProcessInfo(r.pid, r.process_name, int(r.ram_mb * 2**20),
                                                    r.started))
        service = MemoryService(trim=self.trim, process_info=self.info,
                                publish=lambda n, **k: self.events.append((n, k)),
                                clock=self.clock, log=self.logs.append, **options)
        service.set_rows(rows)
        return service


@pytest.fixture
def world():
    return World()


# ------------------------------------------------------------------ what gets trimmed
def test_only_clients_at_or_over_the_threshold_are_trimmed(world):
    rows = [row(1, 200), row(2, 350), row(3, 900)]
    report = world.service(rows, enabled=True, threshold_mb=350).run_auto()
    assert sorted(world.trimmed) == [2, 3]
    assert [t.pid for t in report.trimmed] == [2, 3] or sorted(t.pid for t in report.trimmed) == [2, 3]


def test_trim_all_ignores_the_threshold(world):
    rows = [row(1, 10), row(2, 20)]
    world.service(rows, enabled=True, trim_all=True, threshold_mb=5000).run_auto()
    assert sorted(world.trimmed) == [1, 2]


def test_the_automatic_pass_does_nothing_while_disabled(world):
    service = world.service([row(1, 900)], enabled=False)
    assert service.run_auto() is None
    assert world.trimmed == []


def test_trimming_on_demand_works_even_while_disabled(world):
    service = world.service([row(1, 900)], enabled=False)
    report = service.trim_now()
    assert world.trimmed == [1] and len(report.trimmed) == 1


def test_no_clients_gives_an_empty_report(world):
    report = world.service([], enabled=True).trim_now()
    assert report.trimmed == [] and report.skipped == [] and report.freed_mb == 0


# ------------------------------------------------------------------ safety
def test_a_pid_that_now_belongs_to_another_program_is_not_trimmed(world):
    rows = [row(1, 900)]
    service = world.service(rows)
    world.live[1] = ProcessInfo(1, "notepad.exe", 1, 1000.0)
    report = service.trim_now()
    assert world.trimmed == [] and report.skipped == [(1, "no longer the same program")]


def test_a_pid_reused_by_a_newer_client_is_not_trimmed(world):
    service = world.service([row(1, 900, started=1000.0)])
    world.live[1] = ProcessInfo(1, "robloxplayerbeta.exe", 5, 4000.0)
    assert service.trim_now().skipped == [(1, "no longer the same program")]
    assert world.trimmed == []


def test_a_client_that_already_closed_is_skipped(world):
    service = world.service([row(1, 900)])
    del world.live[1]
    assert service.trim_now().skipped == [(1, "already closed")]


def test_a_refused_trim_is_skipped_and_reported_once(world):
    world.denied.add(1)
    service = world.service([row(1, 900), row(2, 900)], enabled=True)
    first = service.run_auto()
    assert world.trimmed == [2]
    assert first.skipped == [(1, "Windows refused")]
    service.run_auto()
    service.run_auto()
    assert sum("Windows refused" in line or "refused" in line for line in world.logs) == 1


# ------------------------------------------------------------------ what it reports
def test_the_report_says_how_much_was_freed(world):
    world.after_mb = {1: 120.0, 2: 200.0}
    report = world.service([row(1, 500), row(2, 600)]).trim_now()
    by_pid = {t.pid: t for t in report.trimmed}
    assert round(by_pid[1].before_mb) == 500 and round(by_pid[1].after_mb) == 120
    assert round(report.freed_mb) == 780


def test_freed_memory_is_never_negative(world):
    world.after_mb = {1: 900.0}                      # memory came straight back
    report = world.service([row(1, 500)]).trim_now()
    assert report.freed_mb == 0


# ------------------------------------------------------------------ the limits
def test_the_threshold_range_is_enforced_in_one_place():
    assert (MIN_THRESHOLD_MB, MAX_THRESHOLD_MB) == (50, 8000)
    for bad in (49, 8001, -5, 0):
        with pytest.raises(ValueError):
            MemoryService(trim=None, process_info=None, publish=None, clock=None, log=None,
                          threshold_mb=bad)


def test_the_threshold_can_be_changed_later_within_the_range(world):
    service = world.service([row(1, 400)], enabled=True, threshold_mb=500)
    assert service.run_auto().trimmed == []
    service.threshold_mb = 300
    assert len(service.run_auto().trimmed) == 1
    with pytest.raises(ValueError):
        service.threshold_mb = 10


# ------------------------------------------------------------------ the memory-over-limit event
def test_ram_over_is_published_for_clients_over_the_limit(world):
    service = world.service([row(1, 100), row(2, 900, account="Ann")], threshold_mb=350)
    service.check_limit()
    assert [name for name, _ in world.events] == ["ram_over"]
    payload = world.events[0][1]
    assert [r.pid for r in payload["rows"]] == [2] and payload["threshold_mb"] == 350


def test_ram_over_repeats_only_after_15_minutes_per_client(world):
    service = world.service([row(2, 900)], threshold_mb=350)
    service.check_limit()
    world.clock.advance(14 * 60)
    service.check_limit()
    assert len(world.events) == 1
    world.clock.advance(61)
    service.check_limit()
    assert len(world.events) == 2


def test_a_new_client_over_the_limit_is_announced_at_once(world):
    service = world.service([row(2, 900)], threshold_mb=350)
    service.check_limit()
    service.set_rows([row(2, 900), row(3, 950)])
    world.live[3] = ProcessInfo(3, "robloxplayerbeta.exe", 950 * 2**20, 1000.0)
    service.check_limit()
    assert [r.pid for r in world.events[1][1]["rows"]] == [3]


def test_nothing_is_published_when_every_client_is_under_the_limit(world):
    world.service([row(1, 100)], threshold_mb=350).check_limit()
    assert world.events == []
