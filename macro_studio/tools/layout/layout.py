"""Where each window goes (spec 001, US4, FR-030). Pure maths: no Win32, no Tk.

Given a work area and a count it returns one rectangle per window, always inside the area.
Every style handles any area and any count; a size bigger than the area is shrunk to fit.
"""

import math

from macro_studio.core.monitors import Rect

STYLES = ("cascade", "grid", "columns", "stack")
GROUP_SHIFT = 10           # when a cascade runs off the screen it restarts this far to the right


def compute_layout(style, count, area, size=(800, 600), offset=(30, 30)):
    if count <= 0:
        return []
    if style == "grid":
        return _grid(count, area)
    if style == "columns":
        return _columns(count, area)
    w, h = max(1, min(size[0], area.w)), max(1, min(size[1], area.h))
    if style == "stack":
        x = area.x + (area.w - w) // 2
        y = area.y + (area.h - h) // 2
        return [Rect(x, y, w, h) for _ in range(count)]
    if style == "cascade":
        return _cascade(count, area, w, h, offset)
    raise ValueError(f"Unknown layout '{style}'.")


def _cascade(count, area, w, h, offset):
    ox, oy = max(0, offset[0]), max(0, offset[1])
    room_x = (area.w - w) // ox + 1 if ox else count
    room_y = (area.h - h) // oy + 1 if oy else count
    steps = max(1, min(room_x, room_y))
    rects = []
    for i in range(count):
        step, group = i % steps, i // steps
        x = min(area.x + step * ox + group * GROUP_SHIFT, area.x + area.w - w)
        y = min(area.y + step * oy, area.y + area.h - h)
        rects.append(Rect(x, y, w, h))
    return rects


def _grid(count, area):
    cols = math.ceil(math.sqrt(count))
    rows = math.ceil(count / cols)
    cell_w, cell_h = max(1, area.w // cols), max(1, area.h // rows)
    return [Rect(area.x + (i % cols) * cell_w, area.y + (i // cols) * cell_h, cell_w, cell_h)
            for i in range(count)]


def _columns(count, area):
    width = max(1, area.w // count)
    return [Rect(area.x + i * width, area.y, width, area.h) for i in range(count)]
