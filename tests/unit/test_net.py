"""A small, careful HTTP client: allowlisted hosts, HTTPS, size cap, timeouts (US2, US6)."""

import http.server
import json
import threading

import pytest

from macro_studio.core.net import DISCORD_HOSTS, ROBLOX_HOSTS, Http, HttpError


class Handler(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_GET(self):
        Handler.seen.append((self.path, self.headers.get("User-Agent")))
        if self.path == "/big":
            body = b"x" * 5_000_000
        elif self.path.startswith("/go-to?"):            # redirect to the address after "?"
            self.send_response(302)
            self.send_header("Location", self.path.split("?", 1)[1])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif self.path == "/missing":
            self.send_response(404)
            self.end_headers()
            return
        else:
            body = json.dumps({"name": "Ann"}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        Handler.seen.append((self.path, self.headers.get("Content-Type"), body))
        if self.path == "/limited":
            payload = json.dumps({"retry_after": 1.5}).encode()
            self.send_response(429)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path == "/slow-down":
            self.send_response(429)
            self.send_header("Retry-After", "7")
            self.end_headers()
            return
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    Handler.seen = []
    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_a_redirect_is_followed_only_to_an_allowed_host(server):
    port = server.rsplit(":", 1)[1]
    client = Http(allowed_hosts={"127.0.0.1", "localhost"},
                  allow_http_for={"127.0.0.1", "localhost"})
    assert json.loads(client.get(f"{server}/go-to?http://localhost:{port}/ok")) == {"name": "Ann"}
    with pytest.raises(HttpError) as err:
        client.get(f"{server}/go-to?http://evil.example/steal")
    assert err.value.status == 302


def test_a_download_goes_to_a_file_with_its_digest_and_respects_the_cap(server, tmp_path):
    import hashlib
    client = Http(allowed_hosts={"127.0.0.1"}, allow_http_for={"127.0.0.1"})
    path = tmp_path / "file.bin"
    size, sha = client.download(f"{server}/big", str(path), max_bytes=10_000_000)
    assert size == 5_000_000 and sha == hashlib.sha256(b"x" * 5_000_000).hexdigest()
    with pytest.raises(ValueError):
        client.download(f"{server}/big", str(path), max_bytes=1000)
    assert not path.exists(), "a refused download must not leave a file behind"


def test_a_normal_reply_is_returned_as_text(server):
    client = Http(allowed_hosts={"127.0.0.1"}, allow_http_for={"127.0.0.1"})
    assert json.loads(client.get(f"{server}/user")) == {"name": "Ann"}
    assert Handler.seen[0][1].startswith("MacroStudio")


def test_a_host_that_is_not_allowed_is_refused_before_any_request():
    client = Http(allowed_hosts={"users.roblox.com"})
    with pytest.raises(ValueError, match="not allowed"):
        client.get("https://evil.example.com/steal")


def test_plain_http_is_refused_unless_explicitly_allowed():
    client = Http(allowed_hosts={"users.roblox.com"})
    with pytest.raises(ValueError, match="https"):
        client.get("http://users.roblox.com/v1/users/1")


def test_other_schemes_are_refused():
    client = Http(allowed_hosts={"users.roblox.com"})
    for url in ("file:///C:/Windows/win.ini", "ftp://users.roblox.com/x", "javascript:alert(1)"):
        with pytest.raises(ValueError):
            client.get(url)


def test_a_look_alike_host_is_refused():
    client = Http(allowed_hosts={"users.roblox.com"})
    for url in ("https://users.roblox.com.evil.com/x", "https://evil.com/users.roblox.com",
                "https://users.roblox.com@evil.com/x"):
        with pytest.raises(ValueError):
            client.get(url)


def test_a_huge_reply_is_cut_off_not_swallowed(server):
    client = Http(allowed_hosts={"127.0.0.1"}, allow_http_for={"127.0.0.1"}, max_bytes=1000)
    with pytest.raises(ValueError, match="too large"):
        client.get(f"{server}/big")


def test_an_error_status_is_an_os_error_the_lookup_can_retry(server):
    client = Http(allowed_hosts={"127.0.0.1"}, allow_http_for={"127.0.0.1"})
    with pytest.raises(OSError):
        client.get(f"{server}/missing")


def test_the_roblox_hosts_are_exactly_the_three_the_lookups_use():
    assert ROBLOX_HOSTS == {"users.roblox.com", "apis.roblox.com", "games.roblox.com"}


def test_a_post_sends_the_body_and_content_type(server):
    client = Http(allowed_hosts={"127.0.0.1"}, allow_http_for={"127.0.0.1"})
    client.post(f"{server}/hook", b'{"a": 1}', "application/json")
    path, ctype, body = Handler.seen[-1]
    assert (path, ctype, body) == ("/hook", "application/json", b'{"a": 1}')


def test_a_rate_limit_carries_the_wait_from_the_json_body(server):
    client = Http(allowed_hosts={"127.0.0.1"}, allow_http_for={"127.0.0.1"})
    with pytest.raises(HttpError) as err:
        client.post(f"{server}/limited", b"{}", "application/json")
    assert err.value.status == 429 and err.value.retry_after == 1.5


def test_a_rate_limit_carries_the_wait_from_the_header(server):
    client = Http(allowed_hosts={"127.0.0.1"}, allow_http_for={"127.0.0.1"})
    with pytest.raises(HttpError) as err:
        client.post(f"{server}/slow-down", b"{}", "application/json")
    assert err.value.retry_after == 7.0


def test_a_post_to_a_host_that_is_not_allowed_sends_nothing():
    client = Http(allowed_hosts=DISCORD_HOSTS)
    with pytest.raises(ValueError, match="not allowed"):
        client.post("https://evil.example.com/api/webhooks/1/x", b"{}", "application/json")
    assert not any(entry[0] == "/api/webhooks/1/x" for entry in Handler.seen)


def test_the_discord_hosts_are_the_six_real_ones():
    assert "discord.com" in DISCORD_HOSTS and "canary.discordapp.com" in DISCORD_HOSTS
    assert len(DISCORD_HOSTS) == 6
