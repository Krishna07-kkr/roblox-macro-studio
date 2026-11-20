"""Start with Windows: one per-user startup entry, no administrator rights (spec 001, US7)."""

import sys

from macro_studio.core import autostart

RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE, KEY_READ = 2, 1
    REG_SZ = 1

    def __init__(self, fail=False):
        self.values, self.opened, self.fail = {}, [], fail

    def OpenKey(self, root, path, reserved=0, access=0):
        if self.fail:
            raise PermissionError("denied")
        self.opened.append((root, path))
        return (root, path)

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = (kind, value)

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name][1], self.values[name][0]

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]

    def CloseKey(self, key):
        pass


def test_enabling_writes_one_value_under_the_current_users_run_key():
    reg = FakeWinreg()
    assert autostart.enable(r"C:\Apps\Studio\RobloxMacroStudio.exe", winreg=reg) is True
    assert reg.opened == [("HKCU", RUN)]
    kind, command = reg.values["RobloxMacroStudio"]
    assert kind == reg.REG_SZ and command == '"C:\\Apps\\Studio\\RobloxMacroStudio.exe"'


def test_the_command_is_quoted_so_a_path_with_spaces_works():
    reg = FakeWinreg()
    autostart.enable(r"C:\Program Files\My Apps\Studio.exe", winreg=reg)
    assert reg.values["RobloxMacroStudio"][1].startswith('"C:\\Program Files')


def test_it_reports_whether_it_is_on():
    reg = FakeWinreg()
    assert autostart.is_enabled(winreg=reg) is False
    autostart.enable(r"C:\x.exe", winreg=reg)
    assert autostart.is_enabled(winreg=reg) is True


def test_disabling_removes_the_value_and_is_harmless_when_it_is_not_there():
    reg = FakeWinreg()
    autostart.enable(r"C:\x.exe", winreg=reg)
    assert autostart.disable(winreg=reg) is True and "RobloxMacroStudio" not in reg.values
    assert autostart.disable(winreg=reg) is True


def test_a_registry_that_refuses_gives_false_not_a_crash():
    reg = FakeWinreg(fail=True)
    assert autostart.enable(r"C:\x.exe", winreg=reg) is False
    assert autostart.disable(winreg=reg) is False
    assert autostart.is_enabled(winreg=reg) is False


def test_it_never_touches_any_other_value_or_key():
    reg = FakeWinreg()
    reg.values["OtherApp"] = (1, "other.exe")
    autostart.enable(r"C:\x.exe", winreg=reg)
    autostart.disable(winreg=reg)
    assert reg.values == {"OtherApp": (1, "other.exe")}
    assert set(reg.opened) == {("HKCU", RUN)}


def test_the_default_command_is_this_program():
    command = autostart.default_command()
    assert command.startswith('"') and command.count('"') >= 2
    assert sys.executable.split("\\")[-1].lower() in command.lower()


def test_a_command_with_a_quote_in_it_is_refused():
    reg = FakeWinreg()
    assert autostart.enable('C:\\evil" & calc & "x.exe', winreg=reg) is False
    assert reg.values == {}
