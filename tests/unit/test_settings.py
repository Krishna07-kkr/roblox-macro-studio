"""SettingsStore: per-tool preferences in studio_settings.json beside the app."""

import json

from macro_studio.settings import SettingsStore

DEFAULTS = {"enabled": True, "threshold_mb": 350, "interval_s": 3.0, "keys": "Space"}


def test_missing_file_gives_defaults(tmp_path):
    view = SettingsStore(tmp_path / "s.json").view("memory", DEFAULTS)
    assert view.all() == DEFAULTS


def test_set_save_and_reload(tmp_path):
    path = tmp_path / "s.json"
    store = SettingsStore(path)
    store.view("memory", DEFAULTS).set("threshold_mb", 500)
    store.save()
    assert SettingsStore(path).view("memory", DEFAULTS).get("threshold_mb") == 500


def test_unknown_keys_and_tools_are_kept(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"memory": {"future_key": 1}, "other_tool": {"x": 2}}))
    store = SettingsStore(path)
    store.view("memory", DEFAULTS).set("enabled", False)
    store.save()
    saved = json.loads(path.read_text())
    assert saved["memory"]["future_key"] == 1
    assert saved["other_tool"] == {"x": 2}


def test_wrong_type_falls_back_to_default(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"memory": {"threshold_mb": "lots", "enabled": 1}}))
    logged = []
    view = SettingsStore(path, log=logged.append).view("memory", DEFAULTS)
    assert view.get("threshold_mb") == 350
    assert view.get("enabled") is True  # 1 is not a bool
    assert any("threshold_mb" in m for m in logged)


def test_int_accepted_where_default_is_float(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"memory": {"interval_s": 5}}))
    assert SettingsStore(path).view("memory", DEFAULTS).get("interval_s") == 5


def test_corrupt_file_is_logged_and_defaults_used(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json")
    logged = []
    view = SettingsStore(path, log=logged.append).view("memory", DEFAULTS)
    assert view.all() == DEFAULTS
    assert logged


def test_save_is_atomic_no_temp_left(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    store.view("memory", DEFAULTS).set("enabled", False)
    store.save()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["s.json"]


def test_secret_round_trip_is_not_plaintext_on_disk(tmp_path):
    path = tmp_path / "s.json"
    store = SettingsStore(path)
    store.set_secret("notify", "webhook", "https://discord.com/api/webhooks/1/abc")
    store.save()
    assert "discord.com" not in path.read_text()
    assert SettingsStore(path).get_secret("notify", "webhook") == \
        "https://discord.com/api/webhooks/1/abc"


def test_missing_secret_is_empty_string(tmp_path):
    assert SettingsStore(tmp_path / "s.json").get_secret("notify", "webhook") == ""
