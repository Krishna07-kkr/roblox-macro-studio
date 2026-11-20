"""A system tray icon with a menu (spec 001, US7).

One persistent thread owns a hidden message-only window and the icon. Clicks and menu choices
call plain Python callbacks on that thread, so callers hand them to the Tk thread themselves
(`ctx.ui_call`). Everything is typed (`argtypes`/`restype`) and the window procedure is kept
alive for as long as the window exists. The icon is removed when the thread ends and is put
back if Explorer restarts.
"""

import ctypes
import itertools
import threading
from ctypes import wintypes

WM_DESTROY = 0x0002
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
CALLBACK_MESSAGE = WM_APP + 1        # the shell sends clicks on the icon as this
WM_SET_TIP = WM_APP + 2
WM_STOP_TRAY = WM_APP + 3
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4
IDI_APPLICATION = 32512
TPM_RIGHTBUTTON, TPM_NONOTIFY, TPM_RETURNCMD = 0x0002, 0x0080, 0x0100
HWND_MESSAGE = -3
TIP_MAX = 127
_names = itertools.count(1)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                             wintypes.LPARAM)


class _WndClass(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


class _NotifyIcon(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE
_user32.RegisterClassW.argtypes = [ctypes.POINTER(_WndClass)]
_user32.RegisterClassW.restype = wintypes.ATOM
_user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
_user32.UnregisterClassW.restype = wintypes.BOOL
_user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                    wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                    wintypes.HINSTANCE, wintypes.LPVOID]
_user32.CreateWindowExW.restype = wintypes.HWND
_user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.DefWindowProcW.restype = LRESULT
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                wintypes.UINT]
_user32.GetMessageW.restype = wintypes.BOOL
_user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.DispatchMessageW.restype = LRESULT
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostMessageW.restype = wintypes.BOOL
_user32.DestroyWindow.argtypes = [wintypes.HWND]
_user32.DestroyWindow.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_user32.PostQuitMessage.argtypes = [ctypes.c_int]
_user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
_user32.RegisterWindowMessageW.restype = wintypes.UINT
_user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
_user32.LoadIconW.restype = wintypes.HICON
_user32.CreatePopupMenu.restype = wintypes.HMENU
_user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
_user32.AppendMenuW.restype = wintypes.BOOL
_user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
_user32.TrackPopupMenu.restype = wintypes.BOOL
_user32.DestroyMenu.argtypes = [wintypes.HMENU]
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(_NotifyIcon)]
_shell32.Shell_NotifyIconW.restype = wintypes.BOOL


class TrayIcon:
    def __init__(self, tooltip, on_show, menu_items, on_error=None):
        if not callable(on_show):
            raise TypeError("on_show must be a function")
        self.tooltip = tooltip[:TIP_MAX]
        self.on_show, self._items = on_show, list(menu_items)
        self._on_error = on_error or (lambda err: None)
        self.hwnd = 0
        self.thread = None
        self._ready = threading.Event()
        self._proc = None                         # created per start; must outlive the window
        self._taskbar_created = 0
        self._pending_tip = self.tooltip

    # ------------------------------------------------------------ public
    def menu_labels(self):
        return [label for label, _callback in self._items]

    def run_command(self, command_id):
        """Run menu item `command_id` (1-based). Unknown ids do nothing."""
        if 1 <= command_id <= len(self._items):
            self._fire(self._items[command_id - 1][1])

    def start(self):
        """Create the icon. True if it is on screen."""
        if self.thread is not None:
            return bool(self.hwnd)
        self._proc = WNDPROC(self._wndproc)
        self.thread = threading.Thread(target=self._run, name="studio-tray", daemon=True)
        self.thread.start()
        self._ready.wait(5)
        return bool(self.hwnd) and bool(_user32.IsWindow(self.hwnd))

    def stop(self):
        thread, self.thread = self.thread, None
        if thread is None:
            return
        if self.hwnd and _user32.IsWindow(self.hwnd):
            _user32.PostMessageW(self.hwnd, WM_STOP_TRAY, 0, 0)
        thread.join(3)
        if not thread.is_alive():
            # The window is gone, so the procedure can be released. Holding it forever would
            # keep a cycle (icon -> procedure -> icon) alive until the garbage collector runs.
            self._proc = None

    def set_tooltip(self, text):
        """Safe from any thread."""
        self._pending_tip = text[:TIP_MAX]
        if self.hwnd and _user32.IsWindow(self.hwnd):
            _user32.PostMessageW(self.hwnd, WM_SET_TIP, 0, 0)

    # ------------------------------------------------------------ the tray thread
    def _fire(self, callback):
        try:
            callback()
        except Exception as err:  # noqa: BLE001 - a bad callback must not stop the icon
            self._on_error(err)

    def _notify(self, action):
        data = _NotifyIcon()
        data.cbSize = ctypes.sizeof(_NotifyIcon)
        data.hWnd = self.hwnd
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = CALLBACK_MESSAGE
        data.hIcon = _user32.LoadIconW(None, IDI_APPLICATION)
        data.szTip = self.tooltip
        return bool(_shell32.Shell_NotifyIconW(action, ctypes.byref(data)))

    def _wndproc(self, hwnd, message, wparam, lparam):
        if message == CALLBACK_MESSAGE:
            if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self._fire(self.on_show)
            elif lparam == WM_RBUTTONUP:
                self._popup(hwnd)
            return 0
        if message == WM_SET_TIP:
            self.tooltip = self._pending_tip
            self._notify(NIM_MODIFY)
            return 0
        if message == WM_STOP_TRAY:
            _user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            self._notify(NIM_DELETE)
            _user32.PostQuitMessage(0)
            return 0
        if self._taskbar_created and message == self._taskbar_created:
            self._notify(NIM_ADD)                 # Explorer restarted: put the icon back
            return 0
        return _user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _popup(self, hwnd):
        menu = _user32.CreatePopupMenu()
        try:
            for number, (label, _callback) in enumerate(self._items, start=1):
                _user32.AppendMenuW(menu, 0, number, label)
            where = wintypes.POINT()
            _user32.GetCursorPos(ctypes.byref(where))
            _user32.SetForegroundWindow(hwnd)     # lets the menu close when you click away
            chosen = _user32.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_NONOTIFY | TPM_RIGHTBUTTON,
                                            where.x, where.y, 0, hwnd, None)
        finally:
            _user32.DestroyMenu(menu)
        if chosen:
            self.run_command(int(chosen))

    def _run(self):
        instance = _kernel32.GetModuleHandleW(None)
        name = f"MacroStudioTray{next(_names)}"
        registered = False
        try:
            window_class = _WndClass()
            window_class.lpfnWndProc = self._proc
            window_class.hInstance = instance
            window_class.lpszClassName = name
            registered = bool(_user32.RegisterClassW(ctypes.byref(window_class)))
            if not registered:
                return
            hwnd = _user32.CreateWindowExW(0, name, name, 0, 0, 0, 0, 0, HWND_MESSAGE, None,
                                           instance, None)
            if not hwnd:
                return
            self.hwnd = hwnd
            self._taskbar_created = _user32.RegisterWindowMessageW("TaskbarCreated")
            if not self._notify(NIM_ADD):
                _user32.DestroyWindow(hwnd)
                self.hwnd = 0
            self._ready.set()
            message = wintypes.MSG()
            while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(message))
                _user32.DispatchMessageW(ctypes.byref(message))
        finally:
            self._ready.set()
            if registered:
                _user32.UnregisterClassW(name, instance)
