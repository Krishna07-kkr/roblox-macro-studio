"""Start with Windows: a per-user startup entry (spec 001, US7).

One value under HKEY_CURRENT_USER\\...\\Run, so no administrator rights are needed and nothing
system-wide changes. The value holds the program path in quotes. A path that already contains
a quote is refused: the registry value becomes a command line, and a quote could end the path
early and run something else.
"""

import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "RobloxMacroStudio"


def _registry(winreg):
    if winreg is not None:
        return winreg
    import winreg as real
    return real


def default_command():
    """The command that starts this program: the exe when built, else Python and the package."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" -m macro_studio'


def enable(command=None, winreg=None):
    """Turn start-with-Windows on. True if the entry was written."""
    reg = _registry(winreg)
    command = command or default_command()
    program = command.split(" -m ")[0].strip('"')
    if '"' in program:
        return False
    if " -m " not in command and not command.startswith('"'):
        command = f'"{command}"'
    try:
        key = reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE)
        try:
            reg.SetValueEx(key, VALUE_NAME, 0, reg.REG_SZ, command)
        finally:
            reg.CloseKey(key)
    except OSError:
        return False
    return True


def disable(winreg=None):
    """Turn it off. True if it is off afterwards (also when it was never on)."""
    reg = _registry(winreg)
    try:
        key = reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE)
        try:
            reg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass
        finally:
            reg.CloseKey(key)
    except OSError:
        return False
    return True


def is_enabled(winreg=None):
    reg = _registry(winreg)
    try:
        key = reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_READ)
        try:
            reg.QueryValueEx(key, VALUE_NAME)
        finally:
            reg.CloseKey(key)
    except OSError:
        return False
    return True
