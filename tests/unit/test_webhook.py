"""Discord webhooks: URL checks, message building and sending with backoff (spec 001, US6)."""

import json

import pytest

from macro_studio.core.net import HttpError
from macro_studio.tools.notify import webhook as wh

GOOD = "https://discord.com/api/webhooks/123456789012345678/AbC_dEf-123"


# ------------------------------------------------------------------ URL checks and masking
@pytest.mark.parametrize("url", [
    GOOD, "https://ptb.discord.com/api/webhooks/1/abc", "https://canary.discordapp.com/api/webhooks/9/x_y-Z",
    "HTTPS://DISCORD.COM/api/webhooks/5/tok"])
def test_real_webhook_addresses_are_accepted(url):
    assert wh.validate_webhook(url) == (True, "")


@pytest.mark.parametrize("url", [
    "", "   ", "http://discord.com/api/webhooks/1/abc",
    "https://discord.com.evil.com/api/webhooks/1/abc", "https://evil.com/api/webhooks/1/abc",
    "https://discord.com/api/webhooks/abc/abc", "https://discord.com/api/webhooks/1/",
    "https://discord.com/api/webhooks/1/abc/extra", "https://discord.com/api/webhooks/1/abc?wait=true",
    "https://user@discord.com/api/webhooks/1/abc", "https://discord.com/api/webhooks/1/abc def",
    "javascript:alert(1)", "https://discord.com/api/webhooks/1/abc\nHost: evil"])
def test_anything_else_is_refused_with_a_reason(url):
    ok, reason = wh.validate_webhook(url)
    assert ok is False and reason


def test_the_secret_part_is_never_shown():
    masked = wh.mask_webhook(GOOD)
    assert masked == "…/webhooks/123456789012345678/****"
    assert "AbC_dEf" not in masked
    assert "AbC_dEf" not in wh.mask_webhook("not a url but AbC_dEf")


# ------------------------------------------------------------------ building messages
def test_an_embed_message_carries_title_text_and_colour():
    body = wh.build_message("Client closed", "Ann left", color=wh.RED, embeds=True)
    assert body["embeds"][0]["title"] == "Client closed" and body["embeds"][0]["description"] == "Ann left"
    assert body["embeds"][0]["color"] == wh.RED and "timestamp" in body["embeds"][0]
    assert body["username"] == "Roblox Macro Studio" and "content" not in body or body["content"] == ""


def test_without_embeds_title_and_text_go_into_the_content():
    body = wh.build_message("Client closed", "Ann left", embeds=False)
    assert "embeds" not in body and "Client closed" in body["content"] and "Ann left" in body["content"]


@pytest.mark.parametrize("kind, ident, content, allowed", [
    ("everyone", "", "@everyone", {"parse": ["everyone"]}),
    ("here", "", "@here", {"parse": ["everyone"]}),
    ("user", "42", "<@42>", {"users": ["42"]}),
    ("role", "77", "<@&77>", {"roles": ["77"]}),
])
def test_each_ping_allows_exactly_what_it_mentions(kind, ident, content, allowed):
    body = wh.build_message("t", "x", ping=wh.Ping(kind, ident), embeds=True)
    assert content in body["content"] and body["allowed_mentions"] == allowed


def test_no_ping_means_nothing_can_be_mentioned_even_if_the_text_says_so():
    body = wh.build_message("@everyone", "@here <@123> <@&9>", embeds=True)
    assert body["allowed_mentions"] == {"parse": []} and body.get("content", "") == ""


def test_a_ping_with_a_bad_id_is_dropped_not_sent():
    body = wh.build_message("t", "x", ping=wh.Ping("user", "12 34"), embeds=True)
    assert body["allowed_mentions"] == {"parse": []} and "<@" not in body.get("content", "")


def test_text_with_quotes_newlines_and_unicode_survives_as_json():
    text = 'He said "hi"\nline two \\ backslash — café 🎮'
    body = wh.build_message("t", text, embeds=True)
    assert json.loads(json.dumps(body))["embeds"][0]["description"] == text


def test_very_long_text_is_cut_to_the_embed_limit():
    body = wh.build_message("t", "x" * 6000, embeds=True)
    assert len(body["embeds"][0]["description"]) <= 4000


# ------------------------------------------------------------------ multipart
def test_a_screenshot_is_sent_as_multipart_with_the_json_and_one_file():
    payload = {"content": "shot"}
    body, ctype = wh.build_multipart(payload, b"\x89PNGdata", "shot.png", boundary="BOUNDARY")
    assert ctype == "multipart/form-data; boundary=BOUNDARY"
    assert b'name="payload_json"' in body and b'{"content": "shot"}' in body
    assert b'name="files[0]"; filename="shot.png"' in body and b"\x89PNGdata" in body
    assert body.endswith(b"--BOUNDARY--\r\n")


# ------------------------------------------------------------------ sending with backoff
class Http:
    def __init__(self, outcomes):
        self.outcomes, self.posts = list(outcomes), []

    def post(self, url, body, content_type, timeout=10.0):
        self.posts.append((url, json.loads(body) if content_type == "application/json" else body))
        outcome = self.outcomes.pop(0) if self.outcomes else ""
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def send(outcomes, **kwargs):
    http, slept = Http(outcomes), []
    result = wh.send(http, GOOD, {"content": "x"}, sleep=slept.append, **kwargs)
    return result, http, slept


def test_a_normal_send_succeeds_once():
    result, http, slept = send([""])
    assert result.ok and len(http.posts) == 1 and slept == []


def test_a_rate_limit_waits_the_time_discord_asks_for_then_tries_once_more():
    result, http, slept = send([HttpError(429, retry_after=2.5), ""])
    assert result.ok and slept == [2.5] and len(http.posts) == 2


def test_a_second_rate_limit_gives_up():
    result, http, slept = send([HttpError(429, retry_after=1), HttpError(429, retry_after=1)])
    assert not result.ok and len(http.posts) == 2 and "rate" in result.text.lower()


def test_a_server_error_backs_off_5_then_30_seconds_then_drops():
    result, http, slept = send([HttpError(500), HttpError(502), HttpError(503)])
    assert not result.ok and slept == [5, 30] and len(http.posts) == 3


def test_a_server_error_that_clears_is_a_success():
    result, _http, slept = send([HttpError(500), ""])
    assert result.ok and slept == [5]


def test_other_client_errors_are_dropped_with_a_hint_and_not_retried():
    result, http, slept = send([HttpError(404)])
    assert not result.ok and len(http.posts) == 1 and slept == [] and "deleted" in result.text.lower()


def test_a_network_failure_is_reported_not_raised():
    result, _http, _slept = send([OSError("no route"), OSError("no route"), OSError("no route")])
    assert not result.ok


def test_the_result_text_never_contains_the_secret_part():
    result, _http, _slept = send([HttpError(404)])
    assert "AbC_dEf" not in result.text


def test_an_invalid_address_is_refused_before_any_request():
    http = Http([""])
    result = wh.send(http, "https://evil.com/x", {"content": "x"}, sleep=lambda s: None)
    assert not result.ok and http.posts == []


def test_a_file_goes_out_as_multipart():
    http = Http([""])
    wh.send(http, GOOD, {"content": "x"}, file=("a.png", b"PNG"), sleep=lambda s: None)
    assert b"files[0]" in http.posts[0][1]
