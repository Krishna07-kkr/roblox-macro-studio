"""System settings logic: hotkey bindings and the text size (spec 001, US7: FR-060, FR-061).

No Tk: the hotkey service and the table of actions are passed in, so every rule is unit-tested
(tests/unit/test_system_settings.py). F6, F7 and Esc are the studio's stop and pause keys and
are refused by the hotkey service itself; two actions can never share a key.
"""

from macro_studio.core.ui_theme import TEXT_SCALES, normalize_text_scale  # noqa: F401 - re-exported
from macro_studio.services.hotkeys import HotkeyConflict

DEFAULT_KEYS = {"toggle_afk": "F1", "optimize": "F2", "arrange_grid": "F3", "toggle_panel": "F4"}
ACTIONS = tuple(DEFAULT_KEYS)
LABELS = {"toggle_afk": "Turn anti-AFK on or off", "optimize": "Trim memory now",
          "arrange_grid": "Arrange windows in a grid", "toggle_panel": "Show or hide this window"}


class HotkeyBindings:
    """Binds the four everyday actions to keys, with rebinding that cannot break the stop keys."""

    def __init__(self, hotkeys, actions):
        self._hotkeys = hotkeys
        self._actions = actions          # () -> {action name: callable}; tools register theirs
        self.keys = {}                   # action -> the key it is bound to now

    def _bind(self, action, key):
        callback = self._actions().get(action)
        if callback is None:
            return False
        try:
            self._hotkeys.bind(action, key, callback)
        except ValueError as err:
            if isinstance(err, HotkeyConflict):
                raise
            raise HotkeyConflict(f"'{key}' is not a key name I know.") from None
        self.keys[action] = key
        return True

    def rebind(self, action, key):
        """Move an action to another key. Raises HotkeyConflict; the old key stays on failure."""
        return self._bind(action, key.strip())

    def bind_all(self, wanted):
        """Bind every action that has a tool behind it. Returns {action: problem} for the ones
        whose saved key could not be used (they fall back to their default key)."""
        problems = {}
        for action in ACTIONS:
            key = wanted.get(action) or DEFAULT_KEYS[action]
            try:
                self._bind(action, key)
            except HotkeyConflict as err:
                problems[action] = str(err)
                try:
                    self._bind(action, DEFAULT_KEYS[action])
                except HotkeyConflict as again:
                    problems[action] += f" (and the default key failed too: {again})"
        return problems
