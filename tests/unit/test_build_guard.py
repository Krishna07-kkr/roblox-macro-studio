"""The build never ships per-machine settings (FR-053: they hold the webhook)."""

import ast
import os

import pytest

import build_exe
from conftest import PROJECT_ROOT


def test_guard_rejects_settings_in_release(tmp_path):
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "studio_settings.json").write_text("{}")
    with pytest.raises(SystemExit):
        build_exe.assert_no_private_files(str(tmp_path))


def test_guard_accepts_clean_release(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    build_exe.assert_no_private_files(str(tmp_path))


def test_spec_bundles_no_private_files_and_uses_relative_paths():
    with open(os.path.join(PROJECT_ROOT, "RobloxMacroStudio.spec"), encoding="utf-8") as f:
        source = f.read()
    for name in build_exe.PRIVATE_FILES:
        assert name not in source
    assert "C:/Users" not in source and "C:\\\\Users" not in source
    ast.parse(source)  # still valid Python
