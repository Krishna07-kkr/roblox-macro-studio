"""Shared Win32 helpers with typed prototypes and no leaked handles (spec 002, A1).

`ctypes` guesses every argument and result as a C int unless told otherwise. A 64-bit handle
passed or returned that way can be cut to 32 bits, so every function used here declares its
`argtypes` and `restype`. The library object is private to this module, so declaring them
cannot affect other code that uses `ctypes.windll`.
"""

import ctypes
import threading
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)

_user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_user32.OpenInputDesktop.restype = wintypes.HANDLE
_user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
_user32.SetThreadDesktop.restype = wintypes.BOOL
_user32.CloseDesktop.argtypes = [wintypes.HANDLE]
_user32.CloseDesktop.restype = wintypes.BOOL
_user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
_user32.GetThreadDesktop.restype = wintypes.HANDLE

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.GetCurrentThreadId.argtypes = []
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD

DESKTOP_ALL_ACCESS = 0x01FF


class _Attached:
    """The desktop handle a thread is currently attached to; closed when the thread ends."""

    def __init__(self):
        self.handle = None
        self.thread_id = _kernel32.GetCurrentThreadId()
        # The desktop the thread started on. It is shared, never ours to close.
        self.original = _user32.GetThreadDesktop(self.thread_id)

    def swap(self, new_handle):
        """Make `new_handle` current and close the one it replaces (no longer in use)."""
        old, self.handle = self.handle, new_handle
        if old:
            _user32.CloseDesktop(old)

    def __del__(self):
        # Python clears a thread's locals while that thread is still attached to our desktop,
        # and Windows will not close a desktop that a thread is using. Step back onto the
        # original desktop first (only possible from the owning thread), then close ours.
        if self.handle:
            try:
                if self.original and _kernel32.GetCurrentThreadId() == self.thread_id:
                    _user32.SetThreadDesktop(self.original)
                _user32.CloseDesktop(self.handle)
            except (AttributeError, OSError):
                pass            # interpreter shutting down: Windows frees the handle at exit
            self.handle = None


_local = threading.local()


def attach_input_desktop():
    """Attach the calling thread to the desktop that currently receives input.

    Input and capture only work on the active desktop, which changes when the screen locks or
    a UAC prompt appears. Returns True if the thread is attached, False if Windows refused
    (a thread that already owns windows, such as the Tk thread, cannot switch; that is normal)
    or on any error. Never raises. Leaves at most one open handle per thread.
    """
    try:
        attached = getattr(_local, "attached", None)
        if attached is None:
            # Created before the first switch so it records the thread's original desktop.
            attached = _local.attached = _Attached()
        handle = _user32.OpenInputDesktop(0, False, DESKTOP_ALL_ACCESS)
        if not handle:
            return False
        if not _user32.SetThreadDesktop(handle):
            _user32.CloseDesktop(handle)       # not used by the thread: safe to close now
            return False
        attached.swap(handle)
        return True
    except (OSError, ctypes.ArgumentError):
        return False


# ---------------------------------------------------------------------- trimming memory
_psapi = ctypes.WinDLL("psapi", use_last_error=True)
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
_psapi.EmptyWorkingSet.restype = wintypes.BOOL

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_SET_QUOTA = 0x0100


def empty_working_set(pid):
    """Ask Windows to move a process's idle memory out of RAM. True if it was done.

    The process keeps running and pages memory back in as it needs it. The handle opened for
    the call is always closed. A process that is gone, or that we may not touch, gives False.
    """
    handle = _kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_QUERY_LIMITED_INFORMATION,
                                   False, int(pid))
    if not handle:
        return False
    try:
        return bool(_psapi.EmptyWorkingSet(handle))
    finally:
        _kernel32.CloseHandle(handle)


# ---------------------------------------------------------------------- named lock (mutex)
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
ERROR_ALREADY_EXISTS = 183
_mutex_state = threading.local()


def create_named_mutex(name):
    """Create (or open) a named mutex and return its handle, or 0 on failure.

    Roblox uses a mutex called ROBLOX_singletonEvent to allow only one client; a program that
    holds it first lets more clients start. The caller keeps the handle for as long as the lock
    must last and gives it to close_handle().
    """
    handle = _kernel32.CreateMutexW(None, False, name)
    _mutex_state.existed = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    return handle or 0


def last_mutex_existed():
    """True if the most recent create_named_mutex on this thread found it already there."""
    return bool(getattr(_mutex_state, "existed", False))


def close_handle(handle):
    """Close a Win32 handle. Zero (nothing to close) is fine."""
    if handle:
        _kernel32.CloseHandle(handle)
