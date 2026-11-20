"""The real Windows adapters behind the Clients tool, checked against this machine."""

import os

from macro_studio.core import processes
from macro_studio.tools.clients import system
from macro_studio.tools.clients.service import ProcessInfo, WindowInfo


def test_process_and_window_lists_are_lists_of_the_right_things():
    assert all(isinstance(p, ProcessInfo) for p in system.list_processes())
    assert all(isinstance(w, WindowInfo) for w in system.list_windows())


def test_process_info_describes_a_real_process_and_none_for_a_missing_one():
    mine = system.process_info(os.getpid())
    assert mine is not None and "python" in mine.name and mine.rss_bytes > 0
    assert system.process_info(4_000_000) is None


def test_log_head_and_tail_read_the_right_ends(tmp_path):
    path = tmp_path / "x_last.log"
    path.write_bytes(b"HEAD" + b"." * 600_000 + b"TAIL")
    head = system.read_log_text(str(path), head=True)
    tail = system.read_log_text(str(path), head=False)
    assert head.startswith("HEAD") and "TAIL" not in head and len(head) == 400_000
    assert tail.endswith("TAIL") and "HEAD" not in tail and len(tail) == 200_000


def test_a_short_log_is_read_whole_from_either_end(tmp_path):
    path = tmp_path / "x_last.log"
    path.write_text("userid:5 placeid:123456")
    assert system.read_log_text(str(path), head=True) == system.read_log_text(str(path), head=False)


def test_trim_of_a_missing_process_raises_so_the_service_reports_it():
    import pytest
    with pytest.raises(PermissionError):
        system.trim_process(4_000_000)
    with pytest.raises(PermissionError):
        system.terminate_process(4_000_000)


def test_the_dialog_list_is_a_list_of_hwnd_and_pid_pairs():
    for hwnd, pid in system.list_error_dialogs():
        assert isinstance(hwnd, int) and isinstance(pid, int)


class _FakeProc:
    def __init__(self, pid, name, log):
        self.pid, self.info, self._log = pid, {"name": name}, log

    def memory_info(self):
        self._log.append(("memory", self.pid))
        return type("M", (), {"wset": 123 * 2**20, "rss": 1})()

    def create_time(self):
        self._log.append(("started", self.pid))
        return 1000.0 + self.pid


def test_only_roblox_processes_are_asked_for_memory_and_start_time(monkeypatch):
    # Asking psutil for memory and start time of every process took ~500 ms on a PC with 350
    # processes (measured); the name alone takes ~1 ms. Details are fetched only for Roblox.
    asked = []
    procs = [_FakeProc(1, "System", asked), _FakeProc(2, "RobloxPlayerBeta.exe", asked),
             _FakeProc(3, "chrome.exe", asked), _FakeProc(4, "robloxplayerbeta.exe", asked)]
    monkeypatch.setattr(processes.psutil, "process_iter", lambda attrs=None: iter(procs))
    found = system.list_processes()
    assert sorted(p.pid for p in found) == [2, 4]
    assert {pid for _what, pid in asked} == {2, 4}
    assert found[0].rss_bytes == 123 * 2**20 and found[0].name == "robloxplayerbeta.exe"


def test_a_client_that_vanishes_mid_scan_is_skipped_not_fatal(monkeypatch):
    class Vanishing(_FakeProc):
        def memory_info(self):
            raise processes.psutil.NoSuchProcess(self.pid)
    monkeypatch.setattr(processes.psutil, "process_iter",
                        lambda attrs=None: iter([Vanishing(7, "robloxplayerbeta.exe", []),
                                                 _FakeProc(8, "robloxplayerbeta.exe", [])]))
    assert [p.pid for p in system.list_processes()] == [8]
