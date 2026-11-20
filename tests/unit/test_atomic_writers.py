"""Every writer of user data is atomic: a failure leaves the old file intact (A4, H15, SC-A06)."""

import importlib
import json
import os

import pytest

from macro_studio import profiles as profiles_mod
from macro_studio.runners.claim_scheduler import ClaimScheduler


@pytest.fixture
def swap_fails(monkeypatch):
    """Make the final swap of every atomic write fail, like a full disk or a locked file."""
    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def leftovers(folder):
    return [n for n in os.listdir(folder) if n.startswith(".tmp-")]


# ------------------------------------------------------------------ profiles
@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles_mod, "PROFILES_DIR", str(tmp_path))
    return profiles_mod.ProfileManager()


def test_saving_a_profile_cannot_corrupt_the_old_one(manager, tmp_path, swap_fails):
    before = read(tmp_path / "Goliath.json")
    changed = dict(before, meta=dict(before["meta"], name="changed"))
    with pytest.raises(OSError):
        manager.save_profile("Goliath", changed)
    assert read(tmp_path / "Goliath.json") == before
    assert leftovers(tmp_path) == []


def test_a_profile_save_that_works_is_complete_json(manager, tmp_path):
    data = read(tmp_path / "Goliath.json")
    data["meta"]["name"] = "renamed"
    assert manager.save_profile("Goliath", data) is True
    assert read(tmp_path / "Goliath.json")["meta"]["name"] == "renamed"


def test_duplicating_a_profile_is_all_or_nothing(manager, tmp_path, swap_fails):
    with pytest.raises(OSError):
        manager.duplicate_profile("Goliath", "Copy")
    assert not (tmp_path / "Copy.json").exists()
    assert leftovers(tmp_path) == []


def test_duplicating_still_refuses_to_overwrite(manager):
    with pytest.raises(FileExistsError):
        manager.duplicate_profile("Goliath", "Mutation")


# ------------------------------------------------------------------ claim schedule
def test_the_claim_schedule_survives_a_failed_save(tmp_path, swap_fails):
    path = tmp_path / "account_schedule.json"
    path.write_text('{"123": {"last_claim": 1.0}}')
    scheduler = ClaimScheduler(wait_minutes=10, path=str(path))
    scheduler.records = {"999": {"last_claim": 2.0}}
    scheduler.save()                                  # reports quietly; must not damage the file
    assert read(path) == {"123": {"last_claim": 1.0}}
    assert leftovers(tmp_path) == []


# ------------------------------------------------------------------ Goliath calibrator
def test_the_goliath_calibrator_config_survives_a_failed_save(tmp_path, monkeypatch, swap_fails):
    from macro_studio.core import scaling
    monkeypatch.setattr(scaling, "get_base_dir", lambda: str(tmp_path))
    (tmp_path / "config.json").write_text('{"old": true}')
    mod = importlib.import_module("macro_studio.calibrators.goliath")
    with pytest.raises(OSError):
        mod.save_config({"new": True})
    assert read(tmp_path / "config.json") == {"old": True}


# ------------------------------------------------------------------ nothing writes JSON the old way
@pytest.mark.parametrize("module", [
    "macro_studio.profiles",
    "macro_studio.runners.claim_scheduler",
    "macro_studio.calibrators.goliath",
    "macro_studio.calibrators.mutation",
    "macro_studio.tools.macro.tool",
])
def test_no_module_dumps_json_straight_into_an_open_file(module):
    mod = importlib.import_module(module)
    with open(mod.__file__, encoding="utf-8") as handle:
        source = handle.read()
    assert "json.dump(" not in source, f"{module} still writes JSON non-atomically"
