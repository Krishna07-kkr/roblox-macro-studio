"""Roblox processes as the studio sees them: listing, identity checks, trim and close.

Shared by the Clients and Memory tools (tools never import each other; they share `core/`).
Wraps psutil and Win32; the logic that uses these lives in the tools' services, which receive
them as plain functions so they can be tested with fakes.
"""

from dataclasses import dataclass

import psutil

from macro_studio.core import win32

ROBLOX_EXES = frozenset({"robloxplayerbeta.exe"})
#: A process start time must match this closely for two sightings to be "the same process".
IDENTITY_TOLERANCE_S = 2.0


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str
    rss_bytes: int
    started: float             # epoch seconds


def _memory_bytes(memory):
    if memory is None:
        return 0
    return getattr(memory, "wset", None) or memory.rss


def list_processes():
    """Every running Roblox client, with its working-set memory and start time."""
    found = []
    # Only the name is asked for here: fetching memory and start time for all ~350 processes
    # took ~500 ms (measured), the name alone ~1 ms. Details follow for Roblox clients only.
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if name not in ROBLOX_EXES:
            continue
        try:
            found.append(ProcessInfo(pid=proc.pid, name=name,
                                     rss_bytes=_memory_bytes(proc.memory_info()),
                                     started=proc.create_time()))
        except psutil.Error:
            continue                    # closed between the listing and the question
    return found


def process_info(pid):
    """The same facts for one pid, or None if it is gone."""
    try:
        proc = psutil.Process(pid)
        return ProcessInfo(pid=pid, name=proc.name().lower(),
                           rss_bytes=_memory_bytes(proc.memory_info()),
                           started=proc.create_time())
    except psutil.Error:
        return None


def same_process(listed, current):
    """True if `current` is still the very process `listed` described.

    Process ids are reused: after a client closes, a new program can get its number. Closing
    or trimming by pid alone could hit the wrong one, so the program name and start time
    must still match (`listed` is anything with .process_name and .started).
    """
    return (current is not None and current.name.lower() == listed.process_name
            and abs(current.started - listed.started) <= IDENTITY_TOLERANCE_S)


def trim_process(pid):
    """Move a process's idle memory out of RAM. Raises PermissionError if Windows refuses."""
    if not win32.empty_working_set(pid):
        raise PermissionError(f"Windows would not trim process {pid}.")


def terminate_process(pid):
    try:
        psutil.Process(pid).terminate()
    except psutil.Error as err:
        raise PermissionError(str(err)) from err
