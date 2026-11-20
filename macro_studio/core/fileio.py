"""Safe file helpers: atomic writes, log rotation, folder caps (spec 002, A4).

A profile that is half written because the app crashed or the disk filled up means
recalibrating every point, so important files are written to a temporary file in the same
folder and swapped in with os.replace, which is all-or-nothing. Logs and screenshot folders
get a size limit so a long run cannot fill the disk.
"""

import contextlib
import json
import os
import shutil
import tempfile


def _temp_in(folder, suffix):
    descriptor, path = tempfile.mkstemp(prefix=".tmp-", suffix=suffix, dir=folder)
    return descriptor, path


def _discard(path):
    with contextlib.suppress(OSError):
        os.remove(path)


def write_json_atomic(path, data, indent=2):
    """Write `data` as JSON to `path`. The old file survives any failure."""
    text = json.dumps(data, indent=indent)          # fails here, before anything is touched
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    descriptor, temp = _temp_in(folder, ".json")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        _discard(temp)
        raise


def copy_atomic(src, dst):
    """Copy a file so `dst` is either the whole new file or untouched."""
    folder = os.path.dirname(os.path.abspath(dst))
    os.makedirs(folder, exist_ok=True)
    descriptor, temp = _temp_in(folder, ".copy")
    os.close(descriptor)
    try:
        shutil.copyfile(src, temp)
        os.replace(temp, dst)
    except BaseException:
        _discard(temp)
        raise


def rotate_log(path, max_bytes, backups=1):
    """When the log is over `max_bytes`, move it to `path.1` (replacing the old backup)."""
    try:
        if os.path.getsize(path) <= max_bytes:
            return
    except OSError:
        return                                       # no log yet
    if backups < 1:
        _discard(path)
        return
    with contextlib.suppress(OSError):               # in use or locked: try again next time
        os.replace(path, f"{path}.1")


def prune_folder(folder, keep, suffix=None):
    """Delete the oldest files in `folder` beyond the newest `keep`. Returns how many went."""
    try:
        entries = [entry for entry in os.scandir(folder)
                   if entry.is_file() and (suffix is None or entry.name.lower().endswith(suffix))]
    except OSError:
        return 0
    entries.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
    removed = 0
    for entry in entries[max(0, keep):]:
        try:
            os.remove(entry.path)
            removed += 1
        except OSError:
            pass
    return removed


def write_bytes_atomic(path, data):
    """Write `data` (bytes) so `path` is either the whole new file or untouched."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    descriptor, temp = _temp_in(folder, ".bin")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        _discard(temp)
        raise


def write_text_atomic(path, text):
    """Write `text` (UTF-8) so `path` is either the whole new file or untouched."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    descriptor, temp = _temp_in(folder, ".txt")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        _discard(temp)
        raise
