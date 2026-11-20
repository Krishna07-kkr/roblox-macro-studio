"""Old top-level module names must be the very same module as their new home.

A `from new import *` shim would copy names: assigning `profile_manager.PROFILES_DIR`
in a test would then change only the copy while the real module kept writing to the
real profiles/ folder. Aliases avoid that (research R14).
"""

import importlib

import pytest

MOVED = {
    "scaling": "macro_studio.core.scaling",
    "window_manager": "macro_studio.core.window_manager",
    "input_controller": "macro_studio.core.input_controller",
    "overlay_hud": "macro_studio.core.overlay_hud",
    "ui_theme": "macro_studio.core.ui_theme",
    "vision_scanner": "macro_studio.core.vision_scanner",
    "main": "macro_studio.runners.goliath",
    "mutation_runner": "macro_studio.runners.mutation",
    "claim_scheduler": "macro_studio.runners.claim_scheduler",
    "profile_manager": "macro_studio.profiles",
    "calibrate_gui": "macro_studio.calibrators.goliath",
    "calibrate_mutation": "macro_studio.calibrators.mutation",
}


def test_profiles_dir_patched_through_old_name_is_used_by_real_module(monkeypatch, tmp_path):
    # The exact pattern test_mode_separation.py relies on (research R14).
    import profile_manager
    from macro_studio import profiles
    monkeypatch.setattr(profile_manager, "PROFILES_DIR", str(tmp_path))
    assert profiles.PROFILES_DIR == str(tmp_path)


@pytest.mark.parametrize("old, new", sorted(MOVED.items()))
def test_old_name_is_the_new_module(old, new):
    assert importlib.import_module(old) is importlib.import_module(new)


@pytest.mark.parametrize("old, new", sorted(MOVED.items()))
def test_assignment_through_old_name_reaches_new_module(old, new, monkeypatch):
    sentinel = object()
    monkeypatch.setattr(importlib.import_module(old), "_SHIM_PROBE", sentinel, raising=False)
    assert importlib.import_module(new)._SHIM_PROBE is sentinel
