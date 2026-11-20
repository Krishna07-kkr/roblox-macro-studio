"""A low-level mouse and keyboard hook that only queues events (spec 003, C005).

Used by the Recorder while, and only while, a recording is running. The hook thread does nothing
but turn each event into a `RawEvent` and put it in a capped queue, then passes the event on
untouched; everything else happens on another thread through `drain`. Input sent by programs
(including the studio's own replay) is ignored. Nothing is written anywhere from here.
"""

import ctypes
import queue
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_QUIT = 0x0012
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
LLMHF_INJECTED, LLKHF_INJECTED = 0x01, 0x10
_BUTTONS = {0x201: ("down", "left"), 0x202: ("up", "left"), 0x204: ("down", "right"),
            0x205: ("up", "right"), 0x207: ("down", "middle"), 0x208: ("up", "middle")}
WM_MOUSEMOVE, WM_MOUSEWHEEL = 0x200, 0x20A
DEFAULT_QUEUE = 100_000


@dataclass(frozen=True)
class RawEvent:
    kind: str                       # move, down, up, wheel, key_down, key_up
    t: float                        # seconds, time.monotonic()
    x: int | None = None            # screen pixels
    y: int | None = None
    button: str | None = None
    delta: int | None = None
    vk: int | None = None


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.CallNextHookEx.restype = ctypes.c_ssize_t
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                wintypes.UINT]
_user32.GetMessageW.restype = ctypes.c_int
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM,
                                       wintypes.LPARAM]
_user32.PostThreadMessageW.restype = wintypes.BOOL
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class InputHook:
    def __init__(self, accept_injected=False, max_queue=DEFAULT_QUEUE):
        self._accept_injected = accept_injected
        self._max = max_queue
        self._events = queue.SimpleQueue()
        self._size = 0
        self._size_lock = threading.Lock()
        self.dropped = 0
        self.error = ""
        self._thread = None
        self._thread_id = 0
        self._ready = threading.Event()
        # Created in start() and dropped in stop(): a callback holds a bound method of this
        # object, which would keep it (and its locks' Windows handles) alive until a GC pass.
        self._mouse_cb = self._key_cb = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------ the callbacks (hook thread)
    def _push(self, event):
        with self._size_lock:
            if self._size >= self._max:
                self.dropped += 1
                return
            self._size += 1
        self._events.put(event)

    def _mouse_proc(self, code, message, info_address):
        if code >= 0:
            info = MSLLHOOKSTRUCT.from_address(info_address)
            if self._accept_injected or not info.flags & LLMHF_INJECTED:
                now = time.monotonic()
                if message in _BUTTONS:
                    kind, button = _BUTTONS[message]
                    self._push(RawEvent(kind, now, info.pt.x, info.pt.y, button=button))
                elif message == WM_MOUSEMOVE:
                    self._push(RawEvent("move", now, info.pt.x, info.pt.y))
                elif message == WM_MOUSEWHEEL:
                    delta = ctypes.c_short((info.mouseData >> 16) & 0xFFFF).value
                    self._push(RawEvent("wheel", now, info.pt.x, info.pt.y, delta=delta))
        return _user32.CallNextHookEx(None, code, message, info_address)

    def _key_proc(self, code, message, info_address):
        if code >= 0:
            info = KBDLLHOOKSTRUCT.from_address(info_address)
            if self._accept_injected or not info.flags & LLKHF_INJECTED:
                if message in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    self._push(RawEvent("key_down", time.monotonic(), vk=int(info.vkCode)))
                elif message in (WM_KEYUP, WM_SYSKEYUP):
                    self._push(RawEvent("key_up", time.monotonic(), vk=int(info.vkCode)))
        return _user32.CallNextHookEx(None, code, message, info_address)

    # ------------------------------------------------------------ the consumer side
    def drain(self, handle, limit=500):
        """Give up to `limit` queued events to `handle(event)`, oldest first. Returns how many."""
        count = 0
        while count < limit:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            with self._size_lock:
                self._size -= 1
            handle(event)
            count += 1
        return count

    def drain_all(self):
        out = []
        self.drain(out.append, limit=self._max + 1)
        return out

    # ------------------------------------------------------------ life cycle
    def start(self):
        """Install the hooks on their own thread. False if already running or Windows refused."""
        if self.running:
            return False
        self.error = ""
        self._ready.clear()
        self._mouse_cb = _HOOKPROC(self._mouse_proc)        # kept alive: Windows calls these
        self._key_cb = _HOOKPROC(self._key_proc)
        self._thread = threading.Thread(target=self._run, name="input-hook", daemon=True)
        self._thread.start()
        self._ready.wait(5)
        if self.error:
            self.stop()
            return False
        return self.running

    def _run(self):
        mouse = key = None
        try:
            self._thread_id = _kernel32.GetCurrentThreadId()
            module = _kernel32.GetModuleHandleW(None)
            mouse = _user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_cb, module, 0)
            key = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._key_cb, module, 0)
            if not mouse or not key:
                self.error = f"Windows would not install the input hook ({ctypes.get_last_error()})."
                return
            self._ready.set()
            message = wintypes.MSG()
            while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                pass
        finally:
            for handle in (mouse, key):
                if handle:
                    _user32.UnhookWindowsHookEx(handle)
            self._ready.set()

    def stop(self):
        thread = self._thread
        if thread is None:
            self._mouse_cb = self._key_cb = None
            return
        if thread.is_alive() and self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        thread.join(timeout=5)
        self._thread = None
        self._mouse_cb = self._key_cb = None
