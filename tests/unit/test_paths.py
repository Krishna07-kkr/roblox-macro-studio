"""The data root must not move when modules move into the macro_studio package."""

import os
import sys

from conftest import PROJECT_ROOT


def test_base_dir_is_project_root_from_source():
    from macro_studio.core import paths
    assert os.path.samefile(paths.get_base_dir(), PROJECT_ROOT)


def test_base_dir_is_exe_folder_when_frozen(monkeypatch, tmp_path):
    from macro_studio.core import paths
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "RobloxMacroStudio.exe"))
    assert paths.get_base_dir() == str(tmp_path)


def test_resource_prefers_user_copy_then_bundle(monkeypatch, tmp_path):
    from macro_studio.core import paths
    exe_dir, bundle = tmp_path / "exe", tmp_path / "bundle"
    exe_dir.mkdir()
    bundle.mkdir()
    (bundle / "config.json").write_text("{}")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "app.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    assert paths.get_resource_path("config.json") == str(bundle / "config.json")
    (exe_dir / "config.json").write_text("{}")
    assert paths.get_resource_path("config.json") == str(exe_dir / "config.json")


def test_scaling_uses_the_same_data_root():
    import scaling
    from macro_studio.core import paths
    assert scaling.get_base_dir() == paths.get_base_dir()
    assert scaling.get_resource_path("profiles") == paths.get_resource_path("profiles")
