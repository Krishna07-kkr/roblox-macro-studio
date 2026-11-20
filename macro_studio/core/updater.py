"""Auto-update from this project's GitHub Releases.

Check: the latest release's tag (for example v2.7) against `__version__`. Download: its
RobloxMacroStudio.exe, over https from GitHub only, checked against the size and the sha256
digest GitHub publishes for the file. Install: Windows lets a running exe be renamed but not
overwritten, so the running exe becomes `.old`, the download takes its name, and the new exe
is started; the next start deletes the `.old` file. Network and files only, no Tk.
"""

import contextlib
import json
import os
import re
import subprocess
from dataclasses import dataclass

from macro_studio import __version__
from macro_studio.core.net import Http

REPO = "Krishna07-kkr/roblox-macro-studio"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
ASSET_NAME = "RobloxMacroStudio.exe"
#: GitHub answers the API on one host and serves release files from the others.
HOSTS = frozenset({"api.github.com", "github.com", "objects.githubusercontent.com",
                   "release-assets.githubusercontent.com"})
MAX_EXE_BYTES = 300 * 1024 * 1024
_VERSION = re.compile(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def parse_version(text):
    """'v2.6' -> (2, 6, 0); None when it is not a plain version number."""
    found = _VERSION.fullmatch(str(text or "").strip())
    return tuple(int(part or 0) for part in found.groups()) if found else None


@dataclass(frozen=True)
class Release:
    tag: str
    version: tuple
    url: str
    size: int
    sha256: str          # "" when GitHub did not publish a digest
    notes: str = ""


def http_client():
    return Http(allowed_hosts=HOSTS)


def latest_release(http):
    """The newest published release that carries the exe, or None. Raises OSError/ValueError."""
    data = json.loads(http.get(LATEST_URL, timeout=8.0))
    version = parse_version(data.get("tag_name"))
    if version is None or data.get("draft") or data.get("prerelease"):
        return None
    for asset in data.get("assets") or []:
        if asset.get("name") == ASSET_NAME and asset.get("browser_download_url"):
            digest = str(asset.get("digest") or "")
            return Release(tag=data["tag_name"], version=version,
                           url=asset["browser_download_url"], size=int(asset.get("size") or 0),
                           sha256=digest[7:].lower() if digest.startswith("sha256:") else "",
                           notes=str(data.get("body") or "")[:2000])
    return None


def is_newer(release, current=__version__):
    return release is not None and release.version > (parse_version(current) or (0, 0, 0))


def download(http, release, folder):
    """Download the new exe beside the running one; returns its path. A file that does not
    match the published size and digest is deleted and refused (ValueError)."""
    path = os.path.join(folder, ASSET_NAME + ".new")
    size, sha256 = http.download(release.url, path, max_bytes=MAX_EXE_BYTES, timeout=60.0)
    if size != release.size or (release.sha256 and sha256 != release.sha256):
        with contextlib.suppress(OSError):
            os.remove(path)
        raise ValueError("The downloaded file does not match the release, so it was not used.")
    return path


def install(new_path, exe_path):
    """Swap the new exe in. On failure the old exe is put back and the error raised."""
    old = exe_path + ".old"
    with contextlib.suppress(FileNotFoundError):
        os.remove(old)
    os.replace(exe_path, old)
    try:
        os.replace(new_path, exe_path)
    except OSError:
        os.replace(old, exe_path)
        raise


def remove_leftover(exe_path):
    """Delete the previous version's `.old` file after an update (it may still be closing)."""
    with contextlib.suppress(OSError):
        os.remove(exe_path + ".old")


def restart(exe_path):
    """Start the new exe as a fresh program (not as a child of this one's unpacked files)."""
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    subprocess.Popen([exe_path], cwd=os.path.dirname(exe_path), env=env,  # noqa: S603 - our own exe
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
