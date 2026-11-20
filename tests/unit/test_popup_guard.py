"""Robux purchase popup guard (spec 002, story A7).

The guard looks at the client before a macro click. If the purchase popup is on screen it
closes it through the calibrated close point only, checks it is gone, and otherwise
refuses to click (pausing or stopping the macro). It never sends a key.
"""

import os

import cv2
import numpy as np
import pytest

from macro_studio.core import popup_guard as pg
from macro_studio.core.popup_guard import PopupGuard

TEMPLATE_NAME = "purchase_popup.png"
POPUP_W, POPUP_H = 160, 100
CLOSE_REL = (150, 12)          # the close button, relative to the popup's top-left corner
ORIGIN = (300, 200)            # where the popup was when the template was captured


def draw_popup(frame, x, y, scale=1.0):
    """Draw a distinctive popup (title bar, body, green Buy button) at (x, y)."""
    def box(x0, y0, x1, y1, color):
        cv2.rectangle(frame, (int(x + x0 * scale), int(y + y0 * scale)),
                      (int(x + x1 * scale) - 1, int(y + y1 * scale) - 1), color, -1)
    box(0, 0, POPUP_W, POPUP_H, (40, 40, 200))        # body
    box(0, 0, POPUP_W, 24, (255, 255, 255))           # title bar
    box(135, 2, 158, 22, (0, 0, 255))                 # close button
    box(20, 60, 140, 90, (60, 200, 60))               # Buy button
    cv2.circle(frame, (int(x + 40 * scale), int(y + 40 * scale)), int(14 * scale),
               (0, 255, 255), -1)


def noise_frame(w=800, h=600, seed=1):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (h, w, 3), dtype=np.uint8)


def frame_with_popup(at=ORIGIN, w=800, h=600, scale=1.0):
    frame = noise_frame(w, h)
    draw_popup(frame, at[0], at[1], scale)
    return frame


@pytest.fixture
def template_dir(tmp_path):
    folder = tmp_path / "templates"
    folder.mkdir()
    patch = np.zeros((POPUP_H, POPUP_W, 3), dtype=np.uint8)
    draw_popup(patch, 0, 0)
    cv2.imwrite(str(folder / TEMPLATE_NAME), patch)
    return str(folder)


def config(**overrides):
    cfg = {"enabled": True, "template": TEMPLATE_NAME, "threshold": 0.85,
           "origin": {"x": ORIGIN[0], "y": ORIGIN[1]},
           "close_rel": {"x": CLOSE_REL[0], "y": CLOSE_REL[1]},
           "reference_client": {"width": 800, "height": 600}}
    cfg.update(overrides)
    return {"popup_guard": cfg}


class Rig:
    """Everything the guard talks to, recorded."""

    def __init__(self, frames, wait_ok=True):
        self.frames = list(frames)
        self.captures = 0
        self.clicks = []
        self.logs = []
        self.pauses = 0
        self.stops = 0
        self.wait_ok = wait_ok

    def capture(self, hwnd):
        self.captures += 1
        frame = self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]
        return frame

    def click(self, hwnd, x, y):
        self.clicks.append((x, y))
        return True

    def wait(self, seconds):
        return self.wait_ok

    def guard(self, template_dir, cfg=None, fallback_close=None):
        return PopupGuard(cfg if cfg is not None else config(), template_dir,
                          capture=self.capture, click=self.click, log=self.logs.append,
                          wait=self.wait, pause=lambda: setattr(self, "pauses", self.pauses + 1),
                          stop=lambda: setattr(self, "stops", self.stops + 1),
                          fallback_close=fallback_close)


# ------------------------------------------------------------------ not active
def test_guard_is_off_without_a_popup_picture(tmp_path):
    empty = tmp_path / "none"
    empty.mkdir()
    rig = Rig([frame_with_popup()])
    guard = rig.guard(str(empty))
    assert guard.active is False
    assert guard.ensure_clear(1) is True
    assert rig.captures == 0 and rig.clicks == []
    assert any("popup" in m.lower() for m in rig.logs)       # tells the player once


def test_guard_can_be_switched_off(template_dir):
    rig = Rig([frame_with_popup()])
    guard = rig.guard(template_dir, config(enabled=False))
    assert guard.active is False
    assert guard.ensure_clear(1) is True and rig.clicks == []


# ------------------------------------------------------------------ nothing on screen
def test_clear_screen_means_go_ahead(template_dir):
    rig = Rig([noise_frame()])
    assert rig.guard(template_dir).ensure_clear(1) is True
    assert rig.clicks == [] and rig.pauses == 0 and rig.stops == 0


def test_an_unreadable_frame_does_not_block_the_macro(template_dir):
    rig = Rig([None])
    assert rig.guard(template_dir).ensure_clear(1) is True
    assert rig.clicks == []


# ------------------------------------------------------------------ closing it
def test_popup_is_closed_through_the_calibrated_point_only(template_dir):
    rig = Rig([frame_with_popup(), noise_frame()])
    assert rig.guard(template_dir).ensure_clear(1) is True
    assert rig.clicks == [(ORIGIN[0] + CLOSE_REL[0], ORIGIN[1] + CLOSE_REL[1])]
    assert rig.pauses == 0 and rig.stops == 0


def test_close_point_follows_a_popup_that_moved_a_little(template_dir):
    moved = (ORIGIN[0] + 20, ORIGIN[1] - 15)
    rig = Rig([frame_with_popup(at=moved), noise_frame()])
    assert rig.guard(template_dir).ensure_clear(1) is True
    assert rig.clicks == [(moved[0] + CLOSE_REL[0], moved[1] + CLOSE_REL[1])]


def test_popup_far_from_where_it_was_captured_is_not_clicked(template_dir):
    far = (ORIGIN[0] + 250, ORIGIN[1] + 150)
    rig = Rig([frame_with_popup(at=far)])
    assert rig.guard(template_dir).ensure_clear(1) is False
    assert rig.clicks == []
    assert rig.pauses == 1


def test_scaled_client_scales_the_template_and_the_click(template_dir):
    rig = Rig([frame_with_popup(at=(600, 400), w=1600, h=1200, scale=2.0), noise_frame(1600, 1200)])
    assert rig.guard(template_dir).ensure_clear(1) is True
    assert rig.clicks == [(600 + CLOSE_REL[0] * 2, 400 + CLOSE_REL[1] * 2)]


# ------------------------------------------------------------------ cannot close it safely
def test_without_a_close_point_nothing_is_clicked_and_the_macro_pauses(template_dir):
    cfg = config()
    del cfg["popup_guard"]["close_rel"]
    rig = Rig([frame_with_popup()])
    assert rig.guard(template_dir, cfg).ensure_clear(1) is False
    assert rig.clicks == [] and rig.pauses == 1
    assert any("close" in m.lower() for m in rig.logs)


def test_a_zero_zero_fallback_counts_as_not_calibrated(template_dir):
    cfg = config()
    del cfg["popup_guard"]["close_rel"]
    rig = Rig([frame_with_popup()])
    assert rig.guard(template_dir, cfg, fallback_close=lambda: (0, 0)).ensure_clear(1) is False
    assert rig.clicks == []


def test_goliath_close_point_is_used_when_the_guard_has_none(template_dir):
    cfg = config()
    del cfg["popup_guard"]["close_rel"]
    rig = Rig([frame_with_popup(), noise_frame()])
    assert rig.guard(template_dir, cfg, fallback_close=lambda: (534, 236)).ensure_clear(1) is True
    assert rig.clicks == [(534, 236)]


def test_popup_that_will_not_close_stops_the_macro_after_three_tries(template_dir):
    rig = Rig([frame_with_popup()] * 4)
    assert rig.guard(template_dir).ensure_clear(1) is False
    assert len(rig.clicks) == 3 and rig.stops == 1


def test_stop_pressed_while_waiting_ends_the_attempt(template_dir):
    rig = Rig([frame_with_popup()], wait_ok=False)
    assert rig.guard(template_dir).ensure_clear(1) is False
    assert len(rig.clicks) == 1 and rig.stops == 0       # the player stopped it; no second click


def test_cannot_check_after_the_click_means_no_further_click(template_dir):
    rig = Rig([frame_with_popup(), None])
    assert rig.guard(template_dir).ensure_clear(1) is False
    assert len(rig.clicks) == 1 and rig.pauses == 1


# ------------------------------------------------------------------ never a key press
def test_guard_has_no_way_to_send_a_key():
    with open(pg.__file__, encoding="utf-8") as handle:
        source = handle.read()
    for word in ("keybd_event", "SendInput", "VK_RETURN", "VK_SPACE", "press(", "typewrite"):
        assert word not in source, word


# ------------------------------------------------------------------ making the template
def test_saving_a_template_records_where_the_close_button_is(tmp_path):
    frame = frame_with_popup()
    saved = pg.save_template(str(tmp_path), frame, box=(ORIGIN[0], ORIGIN[1], POPUP_W, POPUP_H),
                             close=(ORIGIN[0] + CLOSE_REL[0], ORIGIN[1] + CLOSE_REL[1]))
    assert os.path.isfile(tmp_path / "templates" / TEMPLATE_NAME)
    assert saved["close_rel"] == {"x": CLOSE_REL[0], "y": CLOSE_REL[1]}
    assert saved["origin"] == {"x": ORIGIN[0], "y": ORIGIN[1]}
    assert saved["reference_client"] == {"width": 800, "height": 600}
    assert saved["enabled"] is True


def test_a_close_point_outside_the_popup_is_refused(tmp_path):
    with pytest.raises(ValueError, match="inside"):
        pg.save_template(str(tmp_path), frame_with_popup(),
                         box=(ORIGIN[0], ORIGIN[1], POPUP_W, POPUP_H), close=(10, 10))


def test_a_tiny_selection_is_refused(tmp_path):
    with pytest.raises(ValueError, match="small"):
        pg.save_template(str(tmp_path), frame_with_popup(), box=(300, 200, 10, 10),
                         close=(305, 205))


def test_each_profile_can_keep_its_own_picture(tmp_path):
    frame = frame_with_popup()
    saved = pg.save_template(str(tmp_path), frame, box=(ORIGIN[0], ORIGIN[1], POPUP_W, POPUP_H),
                             close=(ORIGIN[0] + CLOSE_REL[0], ORIGIN[1] + CLOSE_REL[1]),
                             name="purchase_popup_Mutation.png")
    assert saved["template"] == "purchase_popup_Mutation.png"
    assert os.path.isfile(tmp_path / "templates" / "purchase_popup_Mutation.png")
    assert not os.path.exists(tmp_path / "templates" / TEMPLATE_NAME)


@pytest.mark.parametrize("bad", ["../outside.png", "a/b.png", "C:x.png", "popup.txt", ".png",
                                 r"..\x.png"])
def test_picture_name_cannot_escape_the_templates_folder(tmp_path, bad):
    with pytest.raises(ValueError, match="plain"):
        pg.save_template(str(tmp_path), frame_with_popup(),
                         box=(ORIGIN[0], ORIGIN[1], POPUP_W, POPUP_H),
                         close=(ORIGIN[0] + CLOSE_REL[0], ORIGIN[1] + CLOSE_REL[1]), name=bad)
    assert not os.path.exists(tmp_path.parent / "outside.png")


def test_a_picture_name_pointing_outside_the_templates_folder_is_ignored(template_dir, tmp_path):
    outside = tmp_path / "outside.png"
    patch = np.zeros((POPUP_H, POPUP_W, 3), dtype=np.uint8)
    draw_popup(patch, 0, 0)
    cv2.imwrite(str(outside), patch)
    rig = Rig([frame_with_popup()])
    guard = rig.guard(template_dir, config(template=r"..\outside.png"))
    assert guard.active is False
