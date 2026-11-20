"""A small, careful HTTP client for the studio's few network calls (spec 001, US2 and US6).

The app talks to a handful of known places (Roblox name lookups, a Discord webhook the
player typed). So the client only goes to hosts it was told about, only over https, never
follows redirects to somewhere else, and refuses a reply bigger than a limit. A bad or
hostile URL fails here, before anything is sent.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

ROBLOX_HOSTS = frozenset({"users.roblox.com", "apis.roblox.com", "games.roblox.com"})
DISCORD_HOSTS = frozenset({"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com",
                           "ptb.discordapp.com", "canary.discordapp.com"})
USER_AGENT = "MacroStudio/2.5"
DEFAULT_MAX_BYTES = 1_000_000


class HttpError(OSError):
    """The server answered with an error status. `retry_after` is seconds, when it said so."""

    def __init__(self, status, retry_after=None):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.retry_after = retry_after


def _retry_after(error):
    """Seconds the server asked us to wait (header or Discord's JSON body), else None."""
    try:
        header = error.headers.get("Retry-After") if error.headers else None
        if header:
            return float(header)
        return float(json.loads(error.read(2000).decode("utf-8", errors="replace"))["retry_after"])
    except (ValueError, KeyError, TypeError, OSError):
        return None


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect could send us to a host that was never allowed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Http:
    def __init__(self, allowed_hosts, allow_http_for=(), max_bytes=DEFAULT_MAX_BYTES):
        self._allowed = {host.lower() for host in allowed_hosts}
        self._http_ok = {host.lower() for host in allow_http_for}     # for tests on localhost
        self._max_bytes = max_bytes
        self._opener = urllib.request.build_opener(_NoRedirects)

    def _check(self, url):
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("https", "http") or not host:
            raise ValueError("Only https addresses are allowed.")
        if parts.username or parts.password:
            raise ValueError("Addresses with a user name are not allowed.")
        if host not in self._allowed:
            raise ValueError(f"The host '{host}' is not allowed.")
        if parts.scheme == "http" and host not in self._http_ok:
            raise ValueError("Only https addresses are allowed (plain http is refused).")
        return parts

    def get(self, url, timeout=5.0):
        """The reply body as text. Raises ValueError for a refused address or a too-large
        reply, and OSError (including HTTP errors and timeouts) for network trouble."""
        self._check(url)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                       "Accept": "application/json"})
        try:
            with self._opener.open(request, timeout=timeout) as reply:  # noqa: S310 - _check() allowed only https/known hosts
                body = reply.read(self._max_bytes + 1)
        except urllib.error.HTTPError as err:
            raise HttpError(err.code, _retry_after(err)) from err
        except urllib.error.URLError as err:
            raise OSError(str(err.reason)) from err
        if len(body) > self._max_bytes:
            raise ValueError("The reply is too large.")
        return body.decode("utf-8", errors="replace")

    def post(self, url, body, content_type, timeout=10.0):
        """POST `body` (bytes) and return the reply text. Same rules and errors as get()."""
        self._check(url)
        request = urllib.request.Request(url, data=body, method="POST", headers={
            "User-Agent": USER_AGENT, "Content-Type": content_type})
        try:
            with self._opener.open(request, timeout=timeout) as reply:  # noqa: S310 - _check() allowed only https/known hosts
                answer = reply.read(self._max_bytes + 1)
        except urllib.error.HTTPError as err:
            raise HttpError(err.code, _retry_after(err)) from err
        except urllib.error.URLError as err:
            raise OSError(str(err.reason)) from err
        return answer[:self._max_bytes].decode("utf-8", errors="replace")
