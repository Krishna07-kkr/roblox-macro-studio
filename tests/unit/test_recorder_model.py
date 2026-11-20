"""Recording files: steps, the safe store, validation (spec 003, C001)."""

import json
import os

import numpy as np
import pytest

from macro_studio.tools.recorder.model import (
    BadName,
    BadRecording,
    Recording,
    RecordingStore,
    Step,
    check_name,
    delete_step,
    trim,
)


def sample():
    pic = np.full((48, 48, 3), 90, dtype=np.uint8)
    steps = [
        Step("move", 0, x=100, y=100),
        Step("down", 40, x=100, y=100, button="left", picture=pic, look_box=(76, 76, 48, 48)),
        Step("up", 90, x=100, y=100, button="left"),
        Step("wheel", 200, x=100, y=100, delta=-120),
        Step("key_down", 300, vk=0x41),
        Step("key_up", 80, vk=0x41),
    ]
    return Recording("demo", 800, 600, steps)


@pytest.fixture
def store(tmp_path):
    return RecordingStore(str(tmp_path))


# ------------------------------------------------------------------ names
@pytest.mark.parametrize("name", ["demo", "Egg run 2", "a-b_c", "A" * 48])
def test_plain_names_are_accepted(name):
    assert check_name(name) == name


@pytest.mark.parametrize("name", ["", " ", "..", "../x", "a/b", "a\\b", "C:evil", "con.", "x" * 49,
                                  "trailing ", " leading", ".hidden", "a\x00b", "new\nline",
                                  "con", "NUL", "com1", "LPT9"])
def test_unsafe_names_are_refused(name):
    with pytest.raises(BadName):
        check_name(name)


# ------------------------------------------------------------------ store
def test_save_and_load_round_trip(store):
    store.save(sample())
    rec = store.load("demo")
    assert rec.name == "demo" and (rec.client_w, rec.client_h) == (800, 600)
    assert [s.kind for s in rec.steps] == ["move", "down", "up", "wheel", "key_down", "key_up"]
    assert rec.steps[1].look["box"] == [76, 76, 48, 48]
    assert rec.steps[3].delta == -120 and rec.steps[4].vk == 0x41
    assert rec.duration_ms == 0 + 40 + 90 + 200 + 300 + 80


def test_the_picture_is_saved_beside_the_steps_and_loads_back(store, tmp_path):
    store.save(sample())
    folder = tmp_path / "recordings" / "demo"
    assert (folder / "steps.json").is_file()
    pngs = [p for p in os.listdir(folder) if p.endswith(".png")]
    assert len(pngs) == 1
    rec = store.load("demo")
    picture = store.picture("demo", rec.steps[1].look["file"])
    assert picture is not None and picture.shape == (48, 48, 3) and int(picture[0, 0, 0]) == 90


def test_saving_again_replaces_and_drops_stale_pictures(store, tmp_path):
    rec = sample()
    store.save(rec)
    rec.steps[1].picture, rec.steps[1].look, rec.steps[1].look_box = None, None, None
    store.save(rec)
    folder = tmp_path / "recordings" / "demo"
    assert [p for p in os.listdir(folder) if p.endswith(".png")] == []


def test_save_is_atomic_when_writing_fails(store, tmp_path, monkeypatch):
    store.save(sample())
    before = (tmp_path / "recordings" / "demo" / "steps.json").read_text()
    import macro_studio.tools.recorder.model as model

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(model, "write_json_atomic", boom)
    with pytest.raises(OSError):
        store.save(sample())
    assert (tmp_path / "recordings" / "demo" / "steps.json").read_text() == before


def test_list_shows_name_steps_and_length(store):
    store.save(sample())
    other = sample()
    other.name = "second"
    store.save(other)
    rows = store.list()
    assert [r.name for r in rows] == ["demo", "second"]
    assert rows[0].steps == 6 and rows[0].seconds == pytest.approx(0.71, abs=0.01)


def test_list_skips_a_broken_folder_without_failing(store, tmp_path):
    store.save(sample())
    broken = tmp_path / "recordings" / "bad"
    broken.mkdir()
    (broken / "steps.json").write_text("{nope")
    assert [r.name for r in store.list()] == ["demo"]


def test_delete_removes_the_whole_recording_and_nothing_else(store, tmp_path):
    store.save(sample())
    keep = tmp_path / "recordings" / "keep.txt"
    keep.write_text("x")
    store.delete("demo")
    assert not (tmp_path / "recordings" / "demo").exists()
    assert keep.exists()


def test_delete_of_an_unknown_name_is_harmless(store):
    store.delete("never-existed")


def test_a_traversal_name_never_touches_files_outside(store, tmp_path):
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "steps.json").write_text("{}")
    with pytest.raises(BadName):
        store.delete("../secret")
    with pytest.raises(BadName):
        store.load("../secret")
    assert (outside / "steps.json").exists()


def test_rename_and_duplicate(store):
    store.save(sample())
    store.duplicate("demo", "copy")
    store.rename("copy", "renamed")
    assert [r.name for r in store.list()] == ["demo", "renamed"]
    assert store.picture("renamed", store.load("renamed").steps[1].look["file"]) is not None
    with pytest.raises(BadRecording):
        store.rename("renamed", "demo")           # never overwrite another recording


# ------------------------------------------------------------------ validation of a file on disk
def write_raw(tmp_path, data, name="hand"):
    folder = tmp_path / "recordings" / name
    folder.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data)
    (folder / "steps.json").write_text(text)


def good(**over):
    data = {"version": 1, "name": "hand", "client": {"width": 800, "height": 600},
            "steps": [{"kind": "down", "dt": 5, "x": 1, "y": 2, "button": "left"}]}
    data.update(over)
    return data


@pytest.mark.parametrize("data", [
    "not json",
    [],
    good(version=2),
    good(client={"width": 0, "height": 600}),
    good(client={"width": 800}),
    good(steps="x"),
    good(steps=[{"kind": "teleport", "dt": 1}]),
    good(steps=[{"kind": "down", "dt": -1, "x": 1, "y": 2, "button": "left"}]),
    good(steps=[{"kind": "down", "dt": 1, "x": "a", "y": 2, "button": "left"}]),
    good(steps=[{"kind": "down", "dt": 1, "x": 1, "y": 2, "button": "turbo"}]),
    good(steps=[{"kind": "down", "dt": 10 ** 9, "x": 1, "y": 2, "button": "left"}]),
    good(steps=[{"kind": "key_down", "dt": 1, "vk": 0}]),
    good(steps=[{"kind": "key_down", "dt": 1, "vk": 999}]),
    good(steps=[{"kind": "wheel", "dt": 1, "x": 1, "y": 2, "delta": 10 ** 6}]),
    good(steps=[{"kind": "down", "dt": 1, "x": 1, "y": 2, "button": "left",
                 "look": {"file": "../x.png", "box": [0, 0, 30, 30]}}]),
    good(steps=[{"kind": "down", "dt": 1, "x": 1, "y": 2, "button": "left",
                 "look": {"file": "a.png", "box": [0, 0, 30]}}]),
])
def test_a_bad_file_is_refused_with_a_message(store, tmp_path, data):
    write_raw(tmp_path, data)
    with pytest.raises(BadRecording):
        store.load("hand")


def test_stop_keys_in_a_file_are_refused(store, tmp_path):
    for vk in (0x75, 0x76, 0x77, 0x1B):                # F6, F7, F8, Esc
        write_raw(tmp_path, good(steps=[{"kind": "key_down", "dt": 1, "vk": vk}]))
        with pytest.raises(BadRecording):
            store.load("hand")


def test_a_huge_file_is_refused(store, tmp_path):
    write_raw(tmp_path, "x" * (21 * 1024 * 1024))
    with pytest.raises(BadRecording):
        store.load("hand")


# ------------------------------------------------------------------ editing
def test_delete_step_keeps_the_timing_of_what_follows():
    rec = sample()
    delete_step(rec, 3)                       # the wheel step (200 ms)
    assert [s.kind for s in rec.steps] == ["move", "down", "up", "key_down", "key_up"]
    assert rec.steps[3].dt == 200 + 300       # its pause moved to the next step
    assert rec.duration_ms == 40 + 90 + 500 + 80


def test_deleting_a_press_also_deletes_its_release():
    rec = sample()
    delete_step(rec, 1)
    assert [s.kind for s in rec.steps] == ["move", "wheel", "key_down", "key_up"]


def test_deleting_a_release_also_deletes_its_press():
    rec = sample()
    delete_step(rec, 2)
    assert [s.kind for s in rec.steps] == ["move", "wheel", "key_down", "key_up"]


def test_deleting_a_key_press_deletes_its_release_too():
    rec = sample()
    delete_step(rec, 4)
    assert [s.kind for s in rec.steps] == ["move", "down", "up", "wheel"]


def test_trim_cuts_from_both_ends_without_leaving_a_button_down():
    rec = sample()
    trim(rec, start_ms=100, end_ms=500)       # starts between the press and the release
    kinds = [s.kind for s in rec.steps]
    assert kinds.count("down") == kinds.count("up")
    assert kinds.count("key_down") == kinds.count("key_up")


def test_trim_with_nothing_left_leaves_an_empty_recording():
    rec = sample()
    trim(rec, start_ms=10_000, end_ms=20_000)
    assert rec.steps == []
