"""WindowManager._process_name: right name, and never a leaked process handle (A1, H17)."""

import pytest

from macro_studio.core import window_manager as wm_mod
from macro_studio.core.window_manager import WindowManager


class FakePsutil:
    """Stand-in for the psutil module."""

    class Error(Exception):
        pass

    def __init__(self, name=None, error=None):
        self._name, self._error = name, error

    def Process(self, pid):
        if self._error:
            raise self._error
        return self

    def name(self):
        return self._name


class FakeWin32:
    """Stand-in for win32api + win32process."""

    error = RuntimeError

    def __init__(self, path=r"C:\Games\RobloxPlayerBeta.exe", fail_query=False, fail_open=False):
        self.path, self.fail_query, self.fail_open = path, fail_query, fail_open
        self.opened, self.closed = [], []

    def OpenProcess(self, access, inherit, pid):
        if self.fail_open:
            raise self.error("access denied")
        self.opened.append(pid)
        return 900 + pid

    def GetModuleFileNameEx(self, handle, module):
        if self.fail_query:
            raise self.error("cannot read")
        return self.path

    def CloseHandle(self, handle):
        self.closed.append(handle)


@pytest.fixture
def manager():
    return WindowManager.__new__(WindowManager)


def install(monkeypatch, psutil_stub, win32):
    monkeypatch.setattr(wm_mod, "psutil", psutil_stub)
    monkeypatch.setattr(wm_mod, "win32api", win32, raising=False)
    monkeypatch.setattr(wm_mod, "win32process", win32, raising=False)


def test_psutil_name_is_lowercased_and_win32_is_not_touched(manager, monkeypatch):
    win32 = FakeWin32()
    install(monkeypatch, FakePsutil(name="RobloxPlayerBeta.EXE"), win32)
    assert manager._process_name(5) == "robloxplayerbeta.exe"
    assert win32.opened == []


def test_fallback_reads_the_exe_name_and_closes_the_handle(manager, monkeypatch):
    win32 = FakeWin32()
    install(monkeypatch, FakePsutil(error=FakePsutil.Error("gone")), win32)
    assert manager._process_name(5) == "robloxplayerbeta.exe"
    assert win32.closed == [905]


def test_fallback_closes_the_handle_even_when_reading_the_name_fails(manager, monkeypatch):
    win32 = FakeWin32(fail_query=True)
    install(monkeypatch, FakePsutil(error=FakePsutil.Error("gone")), win32)
    assert manager._process_name(5) == ""
    assert win32.closed == [905]


def test_a_process_that_cannot_be_opened_gives_an_empty_name(manager, monkeypatch):
    win32 = FakeWin32(fail_open=True)
    install(monkeypatch, FakePsutil(error=FakePsutil.Error("gone")), win32)
    assert manager._process_name(5) == ""
    assert win32.closed == []


def test_without_psutil_the_fallback_still_works(manager, monkeypatch):
    win32 = FakeWin32()
    install(monkeypatch, None, win32)
    assert manager._process_name(5) == "robloxplayerbeta.exe"
    assert win32.closed == [905]
