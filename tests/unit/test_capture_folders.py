"""Capture folders and the debug log stay small, and the debug switch works (A4, H14)."""

import os

import numpy as np
import pytest

from macro_studio.core import vision_scanner as vs_mod
from macro_studio.core.vision_scanner import VisionScanner
from macro_studio.runners import base as base_mod


def image(value=100, size=(40, 60)):
    return np.full((size[0], size[1], 3), value, dtype=np.uint8)


def pngs(folder):
    return sorted(n for n in os.listdir(folder) if n.endswith(".png"))


@pytest.fixture
def scanner(tmp_base_dir):
    return VisionScanner({"detection": {"save_debug_screenshots": True}})


@pytest.fixture
def quiet_scanner(tmp_base_dir):
    return VisionScanner({"detection": {"save_debug_screenshots": False}})


# ------------------------------------------------------------------ the switch
def test_debug_switch_off_writes_no_evidence_screenshot(quiet_scanner):
    assert quiet_scanner.save_full_capture(image(), "failed") is None
    assert pngs(quiet_scanner.debug_dir) == []


def test_debug_switch_off_writes_no_proof_collage(quiet_scanner):
    quiet_scanner._save_proof_collage(image(), image(), "Slot 1", "ADD", True, "ok")
    assert pngs(quiet_scanner.proof_dir) == []


def test_debug_switch_on_is_the_default_and_still_writes(tmp_base_dir):
    scanner = VisionScanner({})
    path = scanner.save_full_capture(image(), "failed")
    assert path and os.path.isfile(path)
    scanner._save_proof_collage(image(), image(), "Slot 1", "ADD", False, "no")
    assert len(pngs(scanner.proof_dir)) == 1


# ------------------------------------------------------------------ names never collide
def test_two_saves_in_the_same_second_are_two_files(scanner):
    for _ in range(6):
        assert scanner.save_full_capture(image(), "failed")
    assert len(pngs(scanner.debug_dir)) == 6


def test_two_proof_collages_in_the_same_second_are_two_files(scanner):
    for _ in range(6):
        scanner._save_proof_collage(image(), image(), "Slot 1", "ADD", True, "ok")
    assert len(pngs(scanner.proof_dir)) == 6


# ------------------------------------------------------------------ folders are capped
def test_debug_folder_keeps_only_the_newest_files(scanner, monkeypatch):
    monkeypatch.setattr(vs_mod, "MAX_CAPTURES", 10)
    for i in range(25):
        scanner.save_full_capture(image(i), f"n{i}")
    assert len(pngs(scanner.debug_dir)) == 10
    assert any("n24" in name for name in pngs(scanner.debug_dir))       # the newest survived
    assert not any("_n0.png" in name for name in pngs(scanner.debug_dir))


def test_proof_folder_keeps_only_the_newest_files(scanner, monkeypatch):
    monkeypatch.setattr(vs_mod, "MAX_CAPTURES", 8)
    for _ in range(20):
        scanner._save_proof_collage(image(), image(), "Slot 1", "ADD", True, "ok")
    assert len(pngs(scanner.proof_dir)) == 8


# ------------------------------------------------------------------ the debug log
class Probe(base_mod.RunnerBase):
    name = "probe"

    def __init__(self):
        self._init_runtime()

    def _run_loop(self):
        pass


def test_debug_log_rotates_to_one_backup(tmp_path, monkeypatch):
    log = tmp_path / "debug_log.txt"
    monkeypatch.setattr(base_mod, "LOG_FILE_PATH", str(log))
    monkeypatch.setattr(base_mod, "LOG_MAX_BYTES", 2000)
    monkeypatch.setattr(base_mod, "LOG_CHECK_EVERY", 5)
    runner = Probe()
    for i in range(300):
        runner.log(f"line {i} " + "x" * 40)
    assert log.stat().st_size <= 2000 + 5 * 120               # never far above the limit
    backup = tmp_path / "debug_log.txt.1"
    assert backup.exists()
    assert "line 299" in log.read_text(encoding="utf-8")       # the newest line is in the live log
    assert sorted(os.listdir(tmp_path)) == ["debug_log.txt", "debug_log.txt.1"]


def test_a_log_that_cannot_be_written_does_not_stop_the_macro(tmp_path, monkeypatch):
    monkeypatch.setattr(base_mod, "LOG_FILE_PATH", str(tmp_path / "no_such_folder" / "x.txt"))
    Probe().log("still fine")
