"""Per-tool preferences in studio_settings.json beside the app.

One JSON object, one key per tool. Unknown keys are kept so newer versions never lose
settings written by older ones (and the other way round). A value whose type does not
match the tool's default falls back to that default with a log line. Secrets are stored
encrypted with Windows DPAPI for the current user (contracts/services-contract.md).
"""

import base64
import json
import os
import tempfile

from macro_studio.core.paths import get_base_dir

SETTINGS_FILENAME = "studio_settings.json"
_SECRETS_KEY = "_secrets"
_DPAPI_PREFIX = "dpapi:"


def _type_ok(value, default):
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, type(default))


def _protect(plaintext):
    import win32crypt
    blob = win32crypt.CryptProtectData(plaintext.encode("utf-8"), "Macro Studio",
                                       None, None, None, 0)
    return _DPAPI_PREFIX + base64.b64encode(blob).decode("ascii")


def _unprotect(stored):
    import win32crypt
    blob = base64.b64decode(stored[len(_DPAPI_PREFIX):])
    return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1].decode("utf-8")


class SettingsView:
    """One tool's slice of the settings, with that tool's defaults."""

    def __init__(self, store, tool_id, defaults):
        self._store = store
        self._tool_id = tool_id
        self._defaults = dict(defaults)

    def get(self, key):
        default = self._defaults.get(key)
        raw = self._store._data.get(self._tool_id, {})
        if key not in raw:
            return default
        value = raw[key]
        if key in self._defaults and not _type_ok(value, default):
            self._store._log(f"[SETTINGS] {self._tool_id}.{key}={value!r} is not valid; "
                             f"using the default {default!r}.")
            return default
        return value

    def set(self, key, value):
        self._store._data.setdefault(self._tool_id, {})[key] = value

    def all(self):
        return {key: self.get(key) for key in self._defaults}


class SettingsStore:
    def __init__(self, path=None, log=None):
        self.path = str(path) if path else os.path.join(get_base_dir(), SETTINGS_FILENAME)
        self._log = log or (lambda message: None)
        self._data = self._load()

    def _load(self):
        if not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            self._log(f"[SETTINGS] Could not read {self.path} ({e}); using defaults.")
            return {}
        if not isinstance(data, dict):
            self._log(f"[SETTINGS] {self.path} is not a settings object; using defaults.")
            return {}
        return data

    def view(self, tool_id, defaults):
        return SettingsView(self, tool_id, defaults)

    def save(self):
        """Write atomically: a crash mid-save never leaves a half-written file."""
        folder = os.path.dirname(self.path) or "."
        fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def set_secret(self, tool_id, key, plaintext):
        secrets = self._data.setdefault(_SECRETS_KEY, {}).setdefault(tool_id, {})
        secrets[key] = _protect(plaintext) if plaintext else ""

    def get_secret(self, tool_id, key):
        stored = self._data.get(_SECRETS_KEY, {}).get(tool_id, {}).get(key, "")
        if not stored:
            return ""
        if not stored.startswith(_DPAPI_PREFIX):
            self._log(f"[SETTINGS] {tool_id}.{key} is not encrypted; ignoring it.")
            return ""
        try:
            return _unprotect(stored)
        except Exception as e:  # a profile copied from another Windows account
            self._log(f"[SETTINGS] Could not decrypt {tool_id}.{key} ({e}); re-enter it.")
            return ""
