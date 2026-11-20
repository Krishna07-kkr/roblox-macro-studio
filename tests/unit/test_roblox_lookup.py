"""Account and game name lookups (spec 001, US2, FR-015), driven by FakeHttp and a fake clock."""

import json

import pytest

from conftest import FakeClock, FakeHttp
from macro_studio.core import roblox

USER_URL = "https://users.roblox.com/v1/users/123"
UNIVERSE_URL = "https://apis.roblox.com/universes/v1/places/555/universe"
GAME_URL = "https://games.roblox.com/v1/games?universeIds=999"


def make(routes):
    clock = FakeClock(1000.0)
    http = FakeHttp(routes, clock=clock)
    return roblox.NameLookup(http, clock=clock), http, clock


def user_json(name):
    return json.dumps({"id": 123, "name": name})


def test_a_user_name_is_fetched_parsed_and_cached():
    lookup, http, _clock = make({USER_URL: user_json("Ann")})
    lookup.want_user(123)
    assert lookup.user_label(123) is None               # nothing yet
    lookup.tick()
    assert lookup.user_label(123) == "Ann"
    lookup.tick()
    lookup.want_user(123)
    lookup.tick()
    assert len(http.requests) == 1                      # never asked twice


def test_names_with_unicode_escapes_are_decoded():
    lookup, _http, _clock = make({USER_URL: '{"name": "Caf\\u00e9"}'})
    lookup.want_user(123)
    lookup.tick()
    assert lookup.user_label(123) == "Café"


def test_a_place_needs_two_requests_and_gives_the_game_name():
    lookup, http, _clock = make({
        UNIVERSE_URL: json.dumps({"universeId": 999}),
        GAME_URL: json.dumps({"data": [{"id": 999, "name": "Arm Wrestle Simulator"}]}),
    })
    lookup.want_place(555)
    lookup.tick()
    assert lookup.place_label(555) == "Arm Wrestle Simulator"
    assert [req[2] for req in http.requests] == [UNIVERSE_URL, GAME_URL]


def test_retry_after_2_5_then_5_seconds_then_a_placeholder_then_60_seconds():
    lookup, http, clock = make({USER_URL: [ConnectionError("down"), TimeoutError("slow"),
                                           ConnectionError("down"), user_json("Ann")]})
    lookup.want_user(123)
    lookup.tick()                                       # first failure
    assert len(http.requests) == 1 and lookup.user_label(123) is None
    clock.advance(2.4)
    lookup.tick()
    assert len(http.requests) == 1                      # not due yet
    clock.advance(0.2)
    lookup.tick()                                       # second failure
    assert len(http.requests) == 2
    clock.advance(4.9)
    lookup.tick()
    assert len(http.requests) == 2
    clock.advance(0.2)
    lookup.tick()                                       # third failure: placeholder
    assert len(http.requests) == 3 and lookup.user_label(123) == "id 123"
    clock.advance(59)
    lookup.tick()
    assert len(http.requests) == 3
    clock.advance(1.1)
    lookup.tick()                                       # the 60 s retry succeeds
    assert lookup.user_label(123) == "Ann"


def test_a_failed_place_lookup_shows_a_place_placeholder():
    lookup, _http, clock = make({UNIVERSE_URL: ConnectionError("down")})
    lookup.want_place(555)
    for gap in (0, 2.6, 5.1):
        clock.advance(gap)
        lookup.tick()
    assert lookup.place_label(555) == "place 555"


def test_only_one_lookup_runs_per_tick():
    routes = {f"https://users.roblox.com/v1/users/{i}": json.dumps({"name": f"U{i}"})
              for i in (1, 2, 3)}
    lookup, http, _clock = make(routes)
    for i in (1, 2, 3):
        lookup.want_user(i)
    lookup.tick()
    assert len(http.requests) == 1
    lookup.tick()
    lookup.tick()
    assert [lookup.user_label(i) for i in (1, 2, 3)] == ["U1", "U2", "U3"]


@pytest.mark.parametrize("body", ["not json", "[]", '{"nope": 1}', '{"name": ""}', '{"name": 5}'])
def test_a_bad_reply_counts_as_a_failure_not_a_name(body):
    lookup, _http, _clock = make({USER_URL: body})
    lookup.want_user(123)
    lookup.tick()
    assert lookup.user_label(123) is None


def test_asking_again_for_the_same_name_does_not_restart_the_retries():
    lookup, http, clock = make({USER_URL: [ConnectionError("down"), user_json("Ann")]})
    lookup.want_user(123)
    lookup.tick()
    lookup.want_user(123)                               # the dashboard asks every refresh
    lookup.tick()
    assert len(http.requests) == 1
    clock.advance(2.6)
    lookup.tick()
    assert lookup.user_label(123) == "Ann"


def test_forget_drops_unwanted_work():
    lookup, http, _clock = make({USER_URL: user_json("Ann")})
    lookup.want_user(123)
    lookup.forget_user(123)
    lookup.tick()
    assert http.requests == []
