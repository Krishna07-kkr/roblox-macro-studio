"""Which Roblox client tabs the macros work on (Overview > Tabs picker).

One `TabSelection` is shared by the tab picker (Tk thread) and the running macro
(worker thread), so a change in the picker applies on the runner's next pass.
Tabs are identified by PID, the same key the claim timers use: a PID lives as long
as the client, while the window list order changes from pass to pass.

`None` means "all tabs", including clients opened later. A set means exactly those
clients; an empty set means none.
"""

import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import cv2
from PIL import Image


class TabSelection:
    def __init__(self):
        self._lock = threading.Lock()
        self._pids: frozenset | None = None

    @property
    def all_tabs(self):
        with self._lock:
            return self._pids is None

    @property
    def pids(self):
        with self._lock:
            return self._pids

    def set_all(self):
        with self._lock:
            self._pids = None

    def set_pids(self, pids: Iterable[int]):
        with self._lock:
            self._pids = frozenset(int(p) for p in pids)

    def includes(self, pid):
        with self._lock:
            return self._pids is None or pid in self._pids

    def toggle(self, pid, open_pids: Iterable[int]):
        """Tick or untick one tab. Ticking every open tab returns to "all tabs"."""
        open_pids = frozenset(open_pids)
        with self._lock:
            current = open_pids if self._pids is None else self._pids
            new = current - {pid} if pid in current else current | {pid}
            self._pids = None if open_pids and open_pids <= new else frozenset(new)

    def select_only(self, pids: Iterable[int], open_pids: Iterable[int]):
        """Tick exactly `pids`. Ticking every open tab returns to "all tabs"."""
        open_pids = frozenset(open_pids)
        chosen = frozenset(int(p) for p in pids)
        with self._lock:
            self._pids = None if open_pids and open_pids <= chosen else chosen

    def invert(self, open_pids: Iterable[int]):
        """Swap ticked and unticked among the open tabs."""
        open_pids = frozenset(open_pids)
        if not open_pids:
            return
        with self._lock:
            current = open_pids if self._pids is None else self._pids
            self._pids = frozenset(open_pids - current)

    @staticmethod
    def range_pids(ordered_pids: Sequence[int], clicked: int, anchor: int) -> list:
        """The tabs from `anchor` to `clicked` in the order they are shown (Shift+click)."""
        order = list(ordered_pids)
        if clicked not in order:
            return []
        if anchor not in order:
            return [clicked]
        a, b = sorted((order.index(anchor), order.index(clicked)))
        return order[a:b + 1]

    def filter(self, hwnds, get_pid: Callable[[int], int]) -> list:
        """The windows to work on, in their original order."""
        with self._lock:
            pids = self._pids
        if pids is None:
            return list(hwnds)
        return [h for h in hwnds if get_pid(h) in pids]

    def describe(self, open_count):
        with self._lock:
            pids = self._pids
        if pids is None:
            return f"All tabs ({open_count})"
        if not pids:
            return "No tabs"
        return f"{len(pids)} of {open_count} tabs"


@dataclass
class TabInfo:
    hwnd: int
    pid: int
    title: str
    image: Image.Image | None   # None when the client cannot be captured
    minimized: bool = False     # then `image` is the last picture seen, if any


def snapshot_tabs(find_windows: Callable[[], list], get_pid: Callable[[int], int],
                  get_title: Callable[[int], str], capture: Callable,
                  size: tuple[int, int] = (192, 144),
                  is_minimized: Callable[[int], bool] | None = None,
                  cache: dict | None = None) -> list[TabInfo]:
    """Every open client (minimized ones too) with a thumbnail of its screen.

    A minimized window cannot be captured, so it shows the last picture taken while it was
    visible (kept in `cache`, one entry per open window; closed ones are dropped).
    Not for the Tk thread.
    """
    cache = {} if cache is None else cache
    tabs = []
    for hwnd in find_windows():
        minimized = bool(is_minimized and is_minimized(hwnd))
        if minimized:
            image = cache.get(hwnd)
        else:
            frame = capture(hwnd)
            image = None
            if frame is not None and getattr(frame, "size", 0):
                image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                image = image.resize(size, Image.Resampling.LANCZOS)
                cache[hwnd] = image
        tabs.append(TabInfo(hwnd, get_pid(hwnd), get_title(hwnd) or "Roblox", image, minimized))
    for gone in set(cache) - {t.hwnd for t in tabs}:
        del cache[gone]
    return tabs
