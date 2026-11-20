"""Discord webhooks: checking addresses, building messages, sending with backoff (US6).

No Tk and no real network here: `send` takes any object with `post(url, body, content_type,
timeout)`, so every rule is unit-tested (tests/unit/test_webhook.py). A webhook address is a
secret (anyone holding it can post to the channel), so it is checked strictly, never logged in
full, and scrubbed out of every message that leaves this module.
"""

import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

WEBHOOK = re.compile(
    r"https://(?:(?:ptb|canary)\.)?(?:discord|discordapp)\.com/api/webhooks/(\d+)/([A-Za-z0-9_-]+)",
    re.IGNORECASE)
USERNAME = "Roblox Macro Studio"
BLUE, RED, AMBER, GREEN = 3447003, 15158332, 16776960, 3801364
EMBED_TEXT_MAX = 4000
CONTENT_MAX = 2000
TIMEOUT_S = 10.0
SERVER_BACKOFF_S = (5, 30)             # waits before the 2nd and 3rd attempt after a 5xx
SAFE_FILENAME = re.compile(r"[A-Za-z0-9._-]{1,80}")


@dataclass(frozen=True)
class Result:
    ok: bool
    text: str = ""


@dataclass(frozen=True)
class Ping:
    kind: str                # none, everyone, here, user, role
    id: str = ""


# ------------------------------------------------------------------ the address
def validate_webhook(url):
    """(True, "") for a real Discord webhook address, else (False, why)."""
    if not (url or "").strip():
        return False, "Paste a Discord webhook address."
    if WEBHOOK.fullmatch(url) is None:
        return False, ("That is not a Discord webhook address. It looks like "
                       "https://discord.com/api/webhooks/<number>/<token>.")
    return True, ""


def mask_webhook(url):
    """The address with its secret part hidden, safe for the screen and the log."""
    found = WEBHOOK.fullmatch(url or "")
    if found is None:
        return "(hidden)"
    return f"…/webhooks/{found.group(1)}/****"


def _scrub(text, url):
    found = WEBHOOK.fullmatch(url or "")
    return text.replace(found.group(2), "****") if found else text


# ------------------------------------------------------------------ messages
def _ping_parts(ping):
    """(text to put in the message, allowed_mentions) - exactly what it mentions, nothing more."""
    none = ("", {"parse": []})
    if ping is None or ping.kind in (None, "", "none"):
        return none
    if ping.kind == "everyone":
        return "@everyone", {"parse": ["everyone"]}
    if ping.kind == "here":
        return "@here", {"parse": ["everyone"]}
    if ping.kind in ("user", "role") and ping.id.isdigit() and len(ping.id) <= 25:
        if ping.kind == "user":
            return f"<@{ping.id}>", {"users": [ping.id]}
        return f"<@&{ping.id}>", {"roles": [ping.id]}
    return none                                  # an unusable id: send no ping rather than guess


def build_message(title, text, color=BLUE, ping=None, embeds=True, username=USERNAME):
    mention, allowed = _ping_parts(ping)
    body = {"username": username, "allowed_mentions": allowed}
    if embeds:
        body["embeds"] = [{"title": title[:256], "description": text[:EMBED_TEXT_MAX],
                           "color": color,
                           "timestamp": datetime.now(timezone.utc).isoformat()}]
        if mention:
            body["content"] = mention
    else:
        body["content"] = f"{mention} **{title}**\n{text}".strip()[:CONTENT_MAX]
    return body


def build_multipart(payload, data, filename, boundary=None):
    """(body bytes, content type) for a message with one file attached."""
    boundary = boundary or uuid.uuid4().hex
    safe = filename if SAFE_FILENAME.fullmatch(filename or "") else "file.png"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
            f"Content-Type: application/json\r\n\r\n{json.dumps(payload)}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; "
            f"filename=\"{safe}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return head + data + tail, f"multipart/form-data; boundary={boundary}"


# ------------------------------------------------------------------ sending
def send(http, url, message, file=None, sleep=time.sleep):
    """Post `message` (and optionally `file=(name, bytes)`). Never raises; says what happened.

    429: wait as long as Discord asks, then once more. 5xx or no network: wait 5 s, then 30 s,
    then give up. Other client errors (a deleted webhook) are not retried.
    """
    from macro_studio.core.net import HttpError
    ok, reason = validate_webhook(url)
    if not ok:
        return Result(False, reason)
    if file is None:
        body, content_type = json.dumps(message).encode("utf-8"), "application/json"
    else:
        body, content_type = build_multipart(message, file[1], file[0])
    waits = list(SERVER_BACKOFF_S)
    rate_limited = False
    while True:
        try:
            http.post(url, body, content_type, timeout=TIMEOUT_S)
            return Result(True, "Sent")
        except HttpError as err:
            if err.status == 429 and not rate_limited:
                rate_limited = True
                sleep(err.retry_after or 1.0)
                continue
            if err.status == 429:
                return Result(False, "Discord is rate limiting this webhook. Try again later.")
            if err.status >= 500 and waits:
                sleep(waits.pop(0))
                continue
            if err.status >= 500:
                return Result(False, f"Discord had a problem (HTTP {err.status}); gave up.")
            return Result(False, f"Discord refused it (HTTP {err.status}). The webhook may have "
                                 f"been deleted.")
        except OSError as err:
            if waits:
                sleep(waits.pop(0))
                continue
            return Result(False, _scrub(f"Could not reach Discord: {err}", url))
