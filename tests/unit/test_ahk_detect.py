"""AutoHotkey detection runs once, lazily and hidden (spec 002, A2, finding H11)."""

from types import SimpleNamespace

import pytest

from macro_studio.core import input_controller as ic_mod
from macro_studio.core.input_controller import CREATE_NO_WINDOW, InputController

AHK = r"C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe"


@pytest.fixture
def ahk(monkeypatch):
    """Pretend AutoHotkey v2 is installed; count and record every probe."""
    calls = []
    monkeypatch.setattr(ic_mod, "_AHK_INFO", None)
    monkeypatch.setattr(ic_mod.os.path, "isfile", lambda p: p == AHK)
    monkeypatch.setattr(ic_mod.shutil, "which", lambda name: None)

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout="v2", returncode=0)
    monkeypatch.setattr(ic_mod.subprocess, "run", fake_run)
    return calls


def test_building_a_controller_does_not_probe_for_autohotkey(ahk):
    for _ in range(5):
        InputController()
    assert ahk == []


def test_the_first_use_probes_once_and_every_controller_shares_the_answer(ahk):
    first, second = InputController(), InputController()
    assert first.ahk_path == AHK and first.ahk_version == 2
    probes = len(ahk)
    assert probes >= 1
    assert second.ahk_path == AHK and second.ahk_version == 2
    assert first.ahk_path == AHK
    assert len(ahk) == probes


def test_the_probe_never_opens_a_console_window(ahk):
    InputController().ahk_path
    assert ahk and all(kw.get("creationflags") == CREATE_NO_WINDOW for _argv, kw in ahk)


def test_no_autohotkey_installed_is_remembered_too(monkeypatch):
    calls = []
    monkeypatch.setattr(ic_mod, "_AHK_INFO", None)
    monkeypatch.setattr(ic_mod.os.path, "isfile", lambda p: False)
    monkeypatch.setattr(ic_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(ic_mod.subprocess, "run", lambda *a, **k: calls.append(1))
    one, two = InputController(), InputController()
    assert one.ahk_path is None and one.ahk_version == 0
    assert two.ahk_path is None
    assert calls == []


def test_native_engine_never_looks_for_autohotkey(ahk, monkeypatch):
    monkeypatch.setattr(ic_mod, "attach_input_desktop", lambda: True)
    ctl = InputController(engine="native", click_delay=0.0, hold_time=0.0)
    monkeypatch.setattr(ic_mod, "user32", SimpleNamespace(
        SetCursorPos=lambda x, y: 1, GetCursorPos=lambda ref: setattr(ref._obj, "x", 5) or 1,
        mouse_event=lambda *a: 1, SendInput=lambda *a: 1, IsWindow=lambda h: 1))
    ctl.click(5, 5, relative=False)
    assert ahk == []


def test_an_assigned_value_wins_over_detection(ahk):
    ctl = InputController()
    ctl.ahk_path, ctl.ahk_version = None, 0
    assert ctl.ahk_path is None and ahk == []


def test_an_autohotkey_exe_sitting_in_the_working_folder_is_never_run(monkeypatch, tmp_path):
    # shutil.which on Windows looks in the current folder first, and this app's current
    # folder is its own data folder. A planted AutoHotkey.exe there must not be executed.
    planted = tmp_path / "AutoHotkey.exe"
    planted.write_text("not really")
    calls = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ic_mod, "_AHK_INFO", None)
    monkeypatch.setattr(ic_mod.os.path, "isfile", lambda p: p == str(planted))
    monkeypatch.setattr(ic_mod.shutil, "which",
                        lambda name: str(planted) if name == "AutoHotkey.exe" else None)
    monkeypatch.setattr(ic_mod.subprocess, "run", lambda *a, **k: calls.append(a))
    ctl = InputController()
    assert ctl.ahk_path is None and calls == []


def test_a_real_install_found_on_the_path_outside_the_working_folder_is_used(monkeypatch, tmp_path):
    elsewhere = tmp_path / "tools"
    elsewhere.mkdir()
    exe = elsewhere / "AutoHotkey64.exe"
    exe.write_text("x")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setattr(ic_mod, "_AHK_INFO", None)
    monkeypatch.setattr(ic_mod.os.path, "isfile", lambda p: p == str(exe))
    monkeypatch.setattr(ic_mod.shutil, "which",
                        lambda name: str(exe) if name == "AutoHotkey64.exe" else None)
    monkeypatch.setattr(ic_mod.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(stdout="v2", returncode=0))
    assert InputController().ahk_path == str(exe)
