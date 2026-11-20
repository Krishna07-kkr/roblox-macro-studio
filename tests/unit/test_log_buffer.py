"""The Logs page's buffer: capped, filtered, exportable (spec 001, US7, FR-062)."""

import os

import pytest

from macro_studio.core import fileio
from macro_studio.tools.logs.buffer import LogBuffer, is_game_activity


@pytest.mark.parametrize("line, expected", [
    ("[CLIENTS] A Roblox client opened (Ann).", True),
    ("[AFK] No Roblox client is open, so nothing was sent.", True),
    ("[MEMORY] Trimmed 2 client(s), freed 640 MB.", True),
    ("[LAYOUT] Arranged 3 window(s) as grid.", True),
    ("[NOTIFY] Main (…/webhooks/1/****): HTTP 404", True),
    ("[POPUP] Pop-up closed.", True),
    ("[JOB] 'live_feed:macro' took 1.1 s", False),
    ("[SETTINGS] clients.rename='x' is not valid", False),
    ("[HOTKEY] optimize: boom", False),
    ("[EVENTS] log: boom", False),
    ("[STOP] goliath: stuck", False),
    ("plain text with no tag", True),
])
def test_game_activity_hides_the_studios_own_chatter(line, expected):
    assert is_game_activity(line) is expected


def test_lines_come_back_in_the_order_they_were_added_with_a_time():
    buf = LogBuffer(now=lambda: "10:00:00")
    buf.add("one")
    buf.add("two")
    assert buf.lines() == ["10:00:00  one", "10:00:00  two"]


def test_the_buffer_keeps_only_the_newest_lines():
    buf = LogBuffer(max_lines=5, now=lambda: "t")
    for i in range(20):
        buf.add(f"line {i}")
    shown = buf.lines()
    assert len(shown) == 5 and shown[0].endswith("line 15") and shown[-1].endswith("line 19")


def test_the_filter_hides_chatter_but_keeps_everything_stored():
    buf = LogBuffer(now=lambda: "t")
    buf.add("[CLIENTS] opened")
    buf.add("[JOB] slow")
    assert buf.lines(game_only=True) == ["t  [CLIENTS] opened"]
    assert len(buf.lines(game_only=False)) == 2


def test_clear_empties_it():
    buf = LogBuffer(now=lambda: "t")
    buf.add("x")
    buf.clear()
    assert buf.lines() == [] and buf.text() == ""


def test_text_is_the_visible_lines_joined_for_copy_and_export():
    buf = LogBuffer(now=lambda: "t")
    buf.add("[CLIENTS] a")
    buf.add("[JOB] b")
    assert buf.text(game_only=True) == "t  [CLIENTS] a"
    assert buf.text(game_only=False) == "t  [CLIENTS] a\nt  [JOB] b"


def test_a_version_number_changes_when_something_is_added_so_the_page_knows_to_redraw():
    buf = LogBuffer(now=lambda: "t")
    before = buf.version
    buf.add("x")
    assert buf.version != before
    same = buf.version
    buf.lines()
    assert buf.version == same


def test_adding_from_several_threads_loses_nothing_and_never_raises():
    import threading
    buf = LogBuffer(max_lines=100000, now=lambda: "t")

    def spam():
        for i in range(500):
            buf.add(f"line {i}")
    threads = [threading.Thread(target=spam) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(buf.lines()) == 3000


def test_a_line_with_odd_characters_survives():
    buf = LogBuffer(now=lambda: "t")
    buf.add("café — 🎮 \t tab")
    assert "café" in buf.text()


# ------------------------------------------------------------------ exporting
def test_text_is_written_atomically_and_in_utf8(tmp_path):
    target = tmp_path / "log.txt"
    fileio.write_text_atomic(str(target), "café\nline 2")
    assert target.read_text(encoding="utf-8") == "café\nline 2"
    assert os.listdir(tmp_path) == ["log.txt"]


def test_a_failed_text_write_keeps_the_old_file(tmp_path, monkeypatch):
    target = tmp_path / "log.txt"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(OSError):
        fileio.write_text_atomic(str(target), "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert [n for n in os.listdir(tmp_path) if n.startswith(".tmp-")] == []
