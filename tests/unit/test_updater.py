"""Auto-update: version check, release parsing, a download that must match, a safe swap."""

import hashlib
import json

import pytest

from macro_studio.core import updater

EXE = b"new exe bytes"
SHA = hashlib.sha256(EXE).hexdigest()


def release_json(tag="v2.7", digest=f"sha256:{SHA}", size=len(EXE), name=updater.ASSET_NAME,
                 **extra):
    return json.dumps({"tag_name": tag, "body": "notes", **extra, "assets": [
        {"name": "other.zip", "browser_download_url": "https://github.com/x.zip", "size": 1},
        {"name": name, "browser_download_url": "https://github.com/a/b/new.exe", "size": size,
         "digest": digest}]})


class FakeHttp:
    def __init__(self, reply="", body=EXE):
        self.reply, self.body = reply, body

    def get(self, url, timeout=0):
        assert url == updater.LATEST_URL
        return self.reply

    def download(self, url, path, max_bytes, timeout=0):
        with open(path, "wb") as f:
            f.write(self.body)
        return len(self.body), hashlib.sha256(self.body).hexdigest()


def test_versions_compare_as_numbers():
    assert updater.parse_version("v2.10") == (2, 10, 0) > updater.parse_version("2.9.5")
    assert updater.parse_version("latest") is None


def test_the_latest_release_and_its_exe_are_read():
    rel = updater.latest_release(FakeHttp(release_json()))
    assert (rel.tag, rel.version, rel.size, rel.sha256) == ("v2.7", (2, 7, 0), len(EXE), SHA)
    assert updater.is_newer(rel, "2.6") and not updater.is_newer(rel, "2.7")


@pytest.mark.parametrize("reply", [release_json(tag="nightly"), release_json(name="x.exe"),
                                   release_json(prerelease=True)])
def test_a_release_without_a_usable_exe_or_version_is_ignored(reply):
    assert updater.latest_release(FakeHttp(reply)) is None


def test_a_download_that_does_not_match_is_refused_and_deleted(tmp_path):
    rel = updater.latest_release(FakeHttp(release_json()))
    path = updater.download(FakeHttp(), rel, str(tmp_path))
    with open(path, "rb") as f:
        assert f.read() == EXE
    with pytest.raises(ValueError):
        updater.download(FakeHttp(body=b"tampered bytes"), rel, str(tmp_path))
    assert not (tmp_path / (updater.ASSET_NAME + ".new")).exists()


def test_install_swaps_the_exe_and_keeps_the_old_one_until_next_start(tmp_path):
    exe, new = tmp_path / "app.exe", tmp_path / "app.exe.new"
    exe.write_bytes(b"old")
    new.write_bytes(b"new")
    updater.install(str(new), str(exe))
    assert exe.read_bytes() == b"new" and (tmp_path / "app.exe.old").read_bytes() == b"old"
    updater.remove_leftover(str(exe))
    assert not (tmp_path / "app.exe.old").exists()


def test_a_failed_install_puts_the_old_exe_back(tmp_path):
    exe = tmp_path / "app.exe"
    exe.write_bytes(b"old")
    with pytest.raises(OSError):
        updater.install(str(tmp_path / "missing.new"), str(exe))
    assert exe.read_bytes() == b"old"
