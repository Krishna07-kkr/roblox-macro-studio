"""Sending keys and mouse input to Windows (shared by Anti-AFK and the Recorder).

Keys go out as hardware scan codes with SendInput, as games expect. The mouse uses the same
win32api calls the rest of the studio uses. Never called on the Tk thread. Whoever presses a
key or button must release it, on every path; these helpers do not remember anything.
"""

import ctypes
from ctypes import wintypes

import win32api

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC = 0
EXTENDED_VKS = {0x25, 0x26, 0x27, 0x28, 0xA3, 0xA5}       # arrows, right ctrl, right alt
INPUT_KEYBOARD = 1
MOUSEEVENTF_WHEEL = 0x0800
MOUSE_FLAGS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}


class KeyboardInput(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MouseInput(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class InputUnion(ctypes.Union):
    _fields_ = [("ki", KeyboardInput), ("mi", MouseInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", InputUnion)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT
_user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
_user32.MapVirtualKeyW.restype = wintypes.UINT


def scan_and_flags(vk, up=False):
    """The scan code and SendInput flags for a virtual key."""
    scan = _user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    flags = KEYEVENTF_SCANCODE
    if vk in EXTENDED_VKS:
        flags |= KEYEVENTF_EXTENDEDKEY
    if up:
        flags |= KEYEVENTF_KEYUP
    return scan, flags


def send_key(vk, up):
    scan, flags = scan_and_flags(vk, up)
    event = Input(type=INPUT_KEYBOARD, union=InputUnion(ki=KeyboardInput(0, scan, flags, 0, 0)))
    _user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input))


def move_cursor(x, y):
    win32api.SetCursorPos((int(x), int(y)))


def mouse_button(name, down):
    """Press or release "left", "right" or "middle" where the cursor is."""
    flags = MOUSE_FLAGS[name]
    win32api.mouse_event(flags[0] if down else flags[1], 0, 0, 0, 0)


def mouse_wheel(delta):
    win32api.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, int(delta), 0)
