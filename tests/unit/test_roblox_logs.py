"""Roblox log pairing and parsing (spec 001, US2, FR-014).

Rules come from RobloxMultiTool_fixed.ahk: a client's own log starts within -15..+60 s of
its process start; a log is never shared between two clients; after repeated misses the
newest unclaimed recent log is used; a game change counts only after two matching reads.
"""

from datetime import datetime, timedelta, timezone

from macro_studio.core import roblox

UTC = timezone.utc
START = datetime(2026, 11, 20, 8, 30, 0, tzinfo=UTC)


def log(name_time, path=None, modified=0.0):
    stamp = name_time.strftime("%Y%m%dT%H%M%SZ")
    return roblox.LogFile(path=path or f"{stamp}_last.log", started=name_time, modified=modified)


# ------------------------------------------------------------------ file names
def test_log_start_time_comes_from_the_file_name():
    assert roblox.log_start_time("0.612.0.6120648_20261120T083012Z_Player_ABCD_last.log") == \
        datetime(2026, 11, 20, 8, 30, 12, tzinfo=UTC)


def test_a_name_without_a_timestamp_has_no_start_time():
    assert roblox.log_start_time("notes.txt") is None


def test_only_player_logs_are_listed(tmp_path):
    for name in ["a_20261120T083012Z_Player_last.log", "b_20261120T083012Z_Studio_last.log",
                 "c_20261120T083012Z_CrashHandler_last.log", "d_20261120T083012Z_Player.log",
                 "e_nodate_last.log"]:
        (tmp_path / name).write_text("x")
    names = [entry.path.split("\\")[-1].split("/")[-1] for entry in roblox.list_logs(str(tmp_path))]
    assert names == ["a_20261120T083012Z_Player_last.log"]


def test_a_missing_log_folder_lists_nothing(tmp_path):
    assert roblox.list_logs(str(tmp_path / "nope")) == []


# ------------------------------------------------------------------ pairing by start time
def test_the_log_that_started_with_the_process_is_chosen():
    mine = log(START + timedelta(seconds=4))
    other = log(START + timedelta(seconds=300))
    assert roblox.pair_log(START, [other, mine], claimed=set()) is mine


def test_the_closest_of_several_candidates_wins():
    near = log(START + timedelta(seconds=2))
    far = log(START + timedelta(seconds=40))
    assert roblox.pair_log(START, [far, near], claimed=set()) is near


def test_window_is_15_seconds_before_to_60_seconds_after():
    assert roblox.pair_log(START, [log(START - timedelta(seconds=15))], set()) is not None
    assert roblox.pair_log(START, [log(START - timedelta(seconds=16))], set()) is None
    assert roblox.pair_log(START, [log(START + timedelta(seconds=60))], set()) is not None
    assert roblox.pair_log(START, [log(START + timedelta(seconds=61))], set()) is None


def test_a_log_claimed_by_another_client_is_skipped():
    first = log(START + timedelta(seconds=3))
    second = log(START + timedelta(seconds=20))
    assert roblox.pair_log(START, [first, second], claimed={first.path}) is second


# ------------------------------------------------------------------ fallback after misses
def test_fallback_takes_the_newest_unclaimed_recent_log():
    now = 100_000.0
    old = log(START - timedelta(seconds=800), modified=now - 1500)       # too old
    recent = log(START - timedelta(seconds=200), modified=now - 100)
    assert roblox.pair_log_fallback(START, [old, recent], set(), now) is recent


def test_fallback_refuses_a_log_that_started_too_long_from_the_process():
    now = 100_000.0
    far = log(START - timedelta(seconds=901), modified=now - 10)
    assert roblox.pair_log_fallback(START, [far], set(), now) is None


def test_fallback_never_takes_a_claimed_log():
    now = 100_000.0
    only = log(START - timedelta(seconds=30), modified=now - 10)
    assert roblox.pair_log_fallback(START, [only], {only.path}, now) is None


def test_pairer_uses_the_fallback_only_after_three_misses():
    now = 100_000.0
    stray = log(START - timedelta(seconds=300), modified=now - 20)
    pairer = roblox.LogPairer(clock=lambda: now)
    for _ in range(3):
        assert pairer.pair(111, START, [stray]) is None
    assert pairer.pair(111, START, [stray]) is stray
    assert pairer.claimed == {stray.path: 111}


def test_two_clients_never_end_up_with_the_same_log():
    a = log(START + timedelta(seconds=2))
    b = log(START + timedelta(seconds=9))
    pairer = roblox.LogPairer(clock=lambda: 0.0)
    assert pairer.pair(1, START, [a, b]) is a
    assert pairer.pair(2, START, [a, b]) is b


def test_a_closed_client_gives_its_log_back_for_reuse():
    a = log(START + timedelta(seconds=2))
    pairer = roblox.LogPairer(clock=lambda: 0.0)
    pairer.pair(1, START, [a])
    pairer.release(1)
    assert pairer.claimed == {}


def test_a_client_keeps_its_log_once_paired():
    a = log(START + timedelta(seconds=2))
    pairer = roblox.LogPairer(clock=lambda: 0.0)
    assert pairer.pair(1, START, [a]) is a
    assert pairer.pair(1, START, []) is a            # the log list may be empty later


# ------------------------------------------------------------------ what is inside a log
def test_user_id_is_read_from_the_join_line():
    text = "noise\n[FLog::Output] game_join_loadtime placeid:5 userid:98765 ts\nmore"
    assert roblox.parse_user_id(text) == 98765


def test_user_id_falls_back_to_a_plain_userid_field():
    assert roblox.parse_user_id('x {"userid": 4242}') == 4242


def test_no_user_id_means_none():
    assert roblox.parse_user_id("nothing here") is None


def test_the_last_place_id_in_the_tail_wins():
    text = "placeId 1111111\nstuff\nplace id: 22222222\nlater PlaceId=33333333"
    assert roblox.parse_place_id(text) == 33333333


def test_the_word_replace_is_not_a_place_id():
    # the AHK regex matched "place" inside "replace"; a word boundary prevents that
    assert roblox.parse_place_id("replace 99999999 now") is None
    assert roblox.parse_place_id("placeid:12345678 then replace 99999999") == 12345678


def test_short_numbers_are_not_place_ids():
    assert roblox.parse_place_id("place 12") is None


def test_a_place_change_needs_two_matching_reads():
    tracker = roblox.PlaceTracker()
    assert tracker.update(1000000) == 1000000          # the first place is taken at once
    assert tracker.update(2000000) == 1000000          # one sighting of a new place: not yet
    assert tracker.update(2000000) == 2000000          # seen twice in a row: accepted
    assert tracker.update(3000000) == 2000000
    assert tracker.update(2000000) == 2000000          # back to the current one resets the doubt
    assert tracker.update(3000000) == 2000000


def test_no_candidate_keeps_the_current_place():
    tracker = roblox.PlaceTracker()
    tracker.update(1000000)
    assert tracker.update(None) == 1000000
