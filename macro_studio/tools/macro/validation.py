"""Checks for what the player types into the macro tool: profile names and number fields.

No Tk and no widgets: `read_number` only needs an object with a `get()` method, so every rule
here is unit-tested without a window (tests/unit/test_ui_rules.py).
"""

import re

_PROFILE_NAME = re.compile(r"^[A-Za-z0-9 _-]{1,40}$")
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                   *(f"LPT{i}" for i in range(1, 10))}


def clean_profile_name(raw, existing):
    """('Name', None) when `raw` can be a new profile file, else (None, reason).

    A profile is a file in profiles/, so the name must be a plain file name, and it
    must not silently replace another profile (Windows file names ignore case).
    """
    name = (raw or "").strip()
    if not name:
        return None, "Type a name for the profile."
    if not _PROFILE_NAME.match(name):
        return None, ("Use only letters, numbers, spaces, - and _ "
                      "(at most 40 characters).")
    name = name.replace(" ", "_")
    if name.upper() in _RESERVED_NAMES:
        return None, f"'{name}' is reserved by Windows. Pick another name."
    if name.lower() in {e.lower() for e in existing}:
        return None, f"A profile called '{name}' already exists. Pick another name."
    return name, None


class FieldError(ValueError):
    """A settings value that cannot be used, with the field that holds it."""

    def __init__(self, field, message):
        super().__init__(message)
        self.field = field


def read_number(field, label, kind: type = int, minimum=None, blank=None, maximum=None):
    """The number in `field`, or FieldError naming `label` and what is wrong."""
    text = field.get().strip()
    if not text and blank is not None:
        return blank
    try:
        value = kind(text)
    except ValueError:
        what = "a whole number" if kind is int else "a number"
        raise FieldError(field, f"{label} must be {what}, not '{text}'.") from None
    if minimum is not None and value < minimum:
        raise FieldError(field, f"{label} can't be less than {minimum}.")
    if maximum is not None and value > maximum:
        raise FieldError(field, f"{label} can't be more than {maximum}.")
    return value
