"""Atomic writes, log rotation and folder caps (spec 002, A4, findings H14 and H15)."""

import json
import os
import time

import pytest

from macro_studio.core import fileio


def names(folder):
    return sorted(os.listdir(folder))


# ------------------------------------------------------------------ write_json_atomic
def test_writes_valid_json_and_leaves_no_temp_file(tmp_path):
    target = tmp_path / "profile.json"
    fileio.write_json_atomic(str(target), {"a": [1, 2], "b": "ü"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": [1, 2], "b": "ü"}
    assert names(tmp_path) == ["profile.json"]


def test_creates_the_folder_if_needed(tmp_path):
    target = tmp_path / "deep" / "er" / "x.json"
    fileio.write_json_atomic(str(target), {"ok": True})
    assert json.loads(target.read_text()) == {"ok": True}


def test_a_failure_before_the_swap_keeps_the_old_file(tmp_path, monkeypatch):
    target = tmp_path / "profile.json"
    target.write_text('{"old": true}')

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(fileio.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        fileio.write_json_atomic(str(target), {"new": True})
    assert json.loads(target.read_text()) == {"old": True}
    assert names(tmp_path) == ["profile.json"]          # and no temp file left behind


def test_data_that_cannot_be_written_as_json_keeps_the_old_file(tmp_path):
    target = tmp_path / "profile.json"
    target.write_text('{"old": true}')
    with pytest.raises(TypeError):
        fileio.write_json_atomic(str(target), {"bad": object()})
    assert json.loads(target.read_text()) == {"old": True}
    assert names(tmp_path) == ["profile.json"]


def test_a_missing_target_is_created_not_required(tmp_path):
    target = tmp_path / "new.json"
    fileio.write_json_atomic(str(target), [])
    assert json.loads(target.read_text()) == []


# ------------------------------------------------------------------ copy_atomic
def test_copy_is_complete_or_not_there_at_all(tmp_path, monkeypatch):
    src, dst = tmp_path / "a.json", tmp_path / "b.json"
    src.write_text('{"x": 1}')
    fileio.copy_atomic(str(src), str(dst))
    assert dst.read_text() == '{"x": 1}'

    other = tmp_path / "c.json"
    monkeypatch.setattr(fileio.os, "replace", lambda s, d: (_ for _ in ()).throw(OSError("no")))
    with pytest.raises(OSError):
        fileio.copy_atomic(str(src), str(other))
    assert not other.exists()
    assert names(tmp_path) == ["a.json", "b.json"]


# ------------------------------------------------------------------ rotate_log
def test_a_small_log_is_left_alone(tmp_path):
    log = tmp_path / "debug_log.txt"
    log.write_text("x" * 100)
    fileio.rotate_log(str(log), max_bytes=1000)
    assert names(tmp_path) == ["debug_log.txt"]


def test_a_big_log_moves_to_a_single_backup(tmp_path):
    log = tmp_path / "debug_log.txt"
    log.write_text("a" * 2000)
    fileio.rotate_log(str(log), max_bytes=1000)
    assert not log.exists()
    assert (tmp_path / "debug_log.txt.1").read_text() == "a" * 2000
    log.write_text("b" * 2000)
    fileio.rotate_log(str(log), max_bytes=1000)
    assert (tmp_path / "debug_log.txt.1").read_text() == "b" * 2000     # one backup, the newest
    assert names(tmp_path) == ["debug_log.txt.1"]


def test_rotating_a_missing_log_is_fine(tmp_path):
    fileio.rotate_log(str(tmp_path / "nope.txt"), max_bytes=10)


# ------------------------------------------------------------------ prune_folder
def make_files(folder, count):
    for i in range(count):
        path = folder / f"shot_{i:03d}.png"
        path.write_bytes(b"x")
        stamp = time.time() - (count - i) * 10        # file 0 is the oldest
        os.utime(path, (stamp, stamp))


def test_prune_keeps_the_newest_files(tmp_path):
    make_files(tmp_path, 12)
    removed = fileio.prune_folder(str(tmp_path), keep=5)
    assert removed == 7
    assert names(tmp_path) == [f"shot_{i:03d}.png" for i in range(7, 12)]


def test_prune_ignores_subfolders_and_missing_folders(tmp_path):
    (tmp_path / "sub").mkdir()
    make_files(tmp_path, 3)
    assert fileio.prune_folder(str(tmp_path), keep=1) == 2
    assert (tmp_path / "sub").is_dir()
    assert fileio.prune_folder(str(tmp_path / "missing"), keep=1) == 0


def test_prune_only_touches_matching_files(tmp_path):
    make_files(tmp_path, 4)
    (tmp_path / "notes.txt").write_text("keep me")
    fileio.prune_folder(str(tmp_path), keep=1, suffix=".png")
    assert (tmp_path / "notes.txt").exists()
    assert len([n for n in names(tmp_path) if n.endswith(".png")]) == 1
