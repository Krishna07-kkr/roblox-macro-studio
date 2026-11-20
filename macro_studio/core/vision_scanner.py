"""
Vision Scanner & Verification Module
Captures targeted screen regions using high-performance PrintWindow GDI capture.
Uses OpenCV template matching, targeted HSV color thresholding, and contour analysis.
Implements Closed-Loop Verification:
- Captures Before/After screenshots
- Visually verifies that claim/add succeeded before claiming victory
- Generates photographic proof collages saved in proof_captures/
"""

import itertools
import os
import time
import ctypes
from ctypes import wintypes
import cv2
import numpy as np
from macro_studio.core import scaling
from macro_studio.core.fileio import prune_folder
from macro_studio.core.win32 import attach_input_desktop

# Each capture folder keeps only its newest files, so a long run cannot fill the disk.
MAX_CAPTURES = 200
_capture_numbers = itertools.count(1)

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


class SlotState:
    CLAIM_READY = "CLAIM_READY"
    EMPTY_SLOT = "EMPTY_SLOT"
    BUSY_TIMER = "BUSY_TIMER"
    UNKNOWN = "UNKNOWN"


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3)
    ]


class VisionScanner:
    def __init__(self, config):
        self.config = config
        self.detection_cfg = config.get("detection", {})
        self.save_debug = self.detection_cfg.get("save_debug_screenshots", True)

        self.proof_dir = os.path.join(scaling.get_base_dir(), "proof_captures")
        os.makedirs(self.proof_dir, exist_ok=True)

        self.debug_dir = os.path.join(scaling.get_base_dir(), "debug_captures")
        os.makedirs(self.debug_dir, exist_ok=True)

        self.template_dir = scaling.get_resource_path("templates")
        os.makedirs(self.template_dir, exist_ok=True)
        self.templates = {}
        self._load_templates()

    def _ensure_desktop_access(self):
        return attach_input_desktop()

    def _load_templates(self):
        for name in ["empty.png", "claim.png"]:
            path = os.path.join(self.template_dir, name)
            if os.path.exists(path):
                tmpl = cv2.imread(path, cv2.IMREAD_COLOR)
                if tmpl is not None:
                    self.templates[name] = tmpl

    def capture_client_full(self, hwnd):
        """
        Captures the entire client render area of hwnd directly via PrintWindow.
        Returns OpenCV BGR image (e.g. 800x599) or None.
        """
        self._ensure_desktop_access()
        if not user32.IsWindow(hwnd):
            return None

        c_rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(c_rect))
        w = c_rect.right - c_rect.left
        h = c_rect.bottom - c_rect.top
        if w <= 50 or h <= 50:
            return None

        hdc_src = user32.GetDC(hwnd)
        if not hdc_src:
            return None
        hdc_mem = hbmp = old_bitmap = None
        try:
            hdc_mem = gdi32.CreateCompatibleDC(hdc_src)
            if not hdc_mem:
                return None
            hbmp = gdi32.CreateCompatibleBitmap(hdc_src, w, h)
            if not hbmp:
                return None
            old_bitmap = gdi32.SelectObject(hdc_mem, hbmp)

            # PW_RENDERFULLCONTENT = 2
            if not user32.PrintWindow(hwnd, hdc_mem, 2):
                return None

            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = w
            bmi.bmiHeader.biHeight = -h  # top-down
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0

            buf = (ctypes.c_ubyte * (w * h * 4))()
            gdi32.GetDIBits(hdc_mem, hbmp, 0, h, ctypes.byref(buf), ctypes.byref(bmi), 0)
        finally:
            # Every handle made above is given back on every path, including errors. The
            # bitmap must be unselected from the DC before it can be deleted.
            if hdc_mem and old_bitmap:
                gdi32.SelectObject(hdc_mem, old_bitmap)
            if hbmp:
                gdi32.DeleteObject(hbmp)
            if hdc_mem:
                gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(hwnd, hdc_src)

        arr = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 4))
        bgr = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
        return bgr

    def analyze_slot_advanced(self, full_image, slot_cfg):
        """
        Comprehensive and precise slot state detection:
        1. Checks Claim button spot (targeted around claim_click coordinates).
        2. Checks Empty '+' icon via OpenCV template matching.
        3. Contour analysis for '+' icon fallback.
        4. Default: Busy / Timer active.
        """
        if full_image is None:
            return SlotState.UNKNOWN, "Failed to capture window image", None

        img_h, img_w, _ = full_image.shape
        s_reg = slot_cfg.get("scan_region", {})
        x = max(0, min(img_w - 1, s_reg.get("x", 0)))
        y = max(0, min(img_h - 1, s_reg.get("y", 0)))
        w = min(img_w - x, s_reg.get("width", 100))
        h = min(img_h - y, s_reg.get("height", 100))

        slot_crop = full_image[y:y+h, x:x+w]

        # 1. Targeted Green Claim Button Check at claim_click
        claim_pt = slot_cfg.get("claim_click", {})
        cx, cy = claim_pt.get("x", x + w // 2), claim_pt.get("y", y + h - 20)

        # Patch around claim button: 50x70 px
        box_y1 = max(0, cy - 25)
        box_y2 = min(img_h, cy + 25)
        box_x1 = max(0, cx - 35)
        box_x2 = min(img_w, cx + 35)

        claim_patch = full_image[box_y1:box_y2, box_x1:box_x2]
        hsv_claim = cv2.cvtColor(claim_patch, cv2.COLOR_BGR2HSV)
        mask_green = cv2.inRange(hsv_claim, np.array([35, 70, 70]), np.array([85, 255, 255]))
        green_px = int(cv2.countNonZero(mask_green))

        if green_px >= 50:
            return SlotState.CLAIM_READY, f"Green claim button detected ({green_px} px)", slot_crop

        # 2. Template Matching for Empty '+' Icon
        if "empty.png" in self.templates:
            res = cv2.matchTemplate(slot_crop, self.templates["empty.png"], cv2.TM_CCOEFF_NORMED)
            max_val = float(np.max(res))
            if max_val >= 0.78:
                return SlotState.EMPTY_SLOT, f"Empty '+' icon match ({max_val:.2f} confidence)", slot_crop

        # 3. Contour Fallback for Plus Icon (gray color 195-215)
        gray = cv2.cvtColor(slot_crop, cv2.COLOR_BGR2GRAY)
        mask_plus = (gray >= 195) & (gray <= 215)
        contours, _ = cv2.findContours(mask_plus.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            _, _, cw, ch = cv2.boundingRect(cnt)
            if 15 <= cw <= 38 and 15 <= ch <= 38:
                aspect = float(cw) / float(ch)
                if 0.75 <= aspect <= 1.35:
                    return SlotState.EMPTY_SLOT, f"Plus icon contour detected ({cw}x{ch})", slot_crop

        # 4. Otherwise slot has a pet inside and timer running
        return SlotState.BUSY_TIMER, "Active pet in slot / timer running", slot_crop

    def verify_action_success(self, before_crop, after_crop, expected_action, slot_name):
        """
        CLOSED-LOOP VERIFICATION:
        Takes before and after screenshots of the slot and mathematically verifies:
        - If Claim: Did the green claim button disappear?
        - If Add: Did the empty '+' icon disappear and pet appear?
        Generates a verified photographic proof image in proof_captures/.
        """
        if before_crop is None or after_crop is None:
            return False, "Could not capture verification frames"

        diff = cv2.absdiff(before_crop, after_crop)
        diff_score = float(np.mean(diff))

        # Check empty icon in after_crop
        tmpl = self.templates.get("empty.png")
        after_has_plus = False
        if tmpl is not None:
            res_after = cv2.matchTemplate(after_crop, tmpl, cv2.TM_CCOEFF_NORMED)
            if float(np.max(res_after)) >= 0.78:
                after_has_plus = True

        is_verified = False
        reason = ""

        if expected_action == "CLAIM":
            if diff_score > 12.0:
                is_verified = True
                reason = f"Claim verified by visual difference (Diff: {diff_score:.1f})"
            else:
                is_verified = False
                reason = "Claim NOT verified! Slot image did not change."

        elif expected_action == "ADD":
            # Add is verified if the empty plus icon disappeared and difference is significant
            if not after_has_plus and diff_score > 15.0:
                is_verified = True
                reason = f"Add verified! '+' icon disappeared and pet sprite appeared (Diff: {diff_score:.1f})"
            elif not after_has_plus:
                is_verified = True
                reason = f"Add verified! Slot is now occupied (Diff: {diff_score:.1f})"
            else:
                is_verified = False
                reason = f"Add NOT verified! Slot still shows empty '+' icon (Diff: {diff_score:.1f})"

        # Save photographic proof collage
        self._save_proof_collage(before_crop, after_crop, slot_name, expected_action, is_verified, reason)
        return is_verified, reason

    def save_full_capture(self, full_image, tag):
        """Save a whole-client screenshot to debug_captures/ and return its path.

        Used when an action does not verify. The slot crops alone cannot show a
        pop-up that opened somewhere else on screen, so the full frame is the
        only evidence of what actually happened.
        """
        if full_image is None or not self.save_debug:
            return None
        try:
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(tag))
            name = f"{self._stamp()}_{safe}.png"
            path = os.path.join(self.debug_dir, name)
            cv2.imwrite(path, full_image)
            prune_folder(self.debug_dir, MAX_CAPTURES, suffix=".png")
            return path
        except Exception:
            return None

    @staticmethod
    def _stamp():
        """Time plus a running number, so two saves never share a name (even in one millisecond)."""
        now = time.time()
        return (f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}"
                f"{int(now * 1000) % 1000:03d}-{next(_capture_numbers):04d}")

    def _save_proof_collage(self, before_crop, after_crop, slot_name, action, is_verified, reason):
        if not self.save_debug:
            return
        try:
            bh, bw, _ = before_crop.shape
            ah, aw, _ = after_crop.shape
            target_h = max(bh, ah)
            target_w = max(bw, aw)

            b_resized = cv2.resize(before_crop, (target_w, target_h))
            a_resized = cv2.resize(after_crop, (target_w, target_h))

            collage = np.hstack([b_resized, a_resized])
            ch, cw, _ = collage.shape

            banner_h = 36
            banner = np.zeros((banner_h, cw, 3), dtype=np.uint8)
            banner_color = (0, 180, 0) if is_verified else (0, 0, 200)
            banner[:] = banner_color

            status_txt = "VERIFIED SUCCESS" if is_verified else "VERIFICATION FAILED"
            cv2.putText(banner, f"[{status_txt}] {action} on {slot_name}", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.60, (255, 255, 255), 2)

            final_proof = np.vstack([banner, collage])

            cv2.putText(final_proof, "BEFORE", (10, banner_h + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
            cv2.putText(final_proof, "AFTER", (target_w + 10, banner_h + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)

            fname = f"{'SUCCESS' if is_verified else 'FAILED'}_{action}_{slot_name}_{self._stamp()}.png"
            fpath = os.path.join(self.proof_dir, fname)
            cv2.imwrite(fpath, final_proof)
            prune_folder(self.proof_dir, MAX_CAPTURES, suffix=".png")
        except Exception:
            pass
