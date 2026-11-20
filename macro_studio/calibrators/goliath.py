"""
🎯 Visual Coordinate Calibrator & Interactive Target Pinpointer
A modern, Apple HIG-inspired visual GUI tool for pixel-perfect macro coordinate calibration.
- Displays live 800x600 Roblox game screen on an interactive canvas.
- Click directly on the game screenshot to pinpoint and calibrate any button.
- 4x Magnifier Loupe for sub-pixel precision.
- Built-in [Test Click] to verify buttons on the actual game in real time.
- One-click [Save & Sync] to config.json, calibrated profiles, and RobloxPetMacro.ahk.
- ZERO blind global click interception - 100% contained within the GUI.
"""

import os
import json
import time
from ctypes import wintypes
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk, ImageDraw
import cv2
import numpy as np

from macro_studio.core import scaling
from macro_studio.core.win32 import attach_input_desktop
from macro_studio.core.fileio import write_json_atomic

# User data lives next to the exe (or the project when run from source). Not
# next to this file: in the one-file exe that is PyInstaller's temp folder,
# which is deleted on exit and took every saved calibration with it.
BASE_DIR = scaling.get_base_dir()
os.chdir(BASE_DIR)

# Attach to desktop BEFORE creating any GUI handles
def ensure_desktop_access():
    attach_input_desktop()

ensure_desktop_access()

from macro_studio.core.window_manager import WindowManager
from macro_studio.core.vision_scanner import VisionScanner
from macro_studio.core.input_controller import InputController

from macro_studio.core import ui_theme as ui
from macro_studio.core.ui_theme import (
    CANVAS, SURFACE, SURFACE_2, SURFACE_3, HAIRLINE,
    INK, BODY, MUTE, LINK, ERROR, WARNING, CYAN, VIOLET, PINK,
    SP_XS, SP_SM, SP_MD, SP_LG, SP_XL,
    R_SM, R_MD, R_LG,
    Card, Button, Badge, Toggle,
)

# --- Palette aliases (Vercel design system, dark polarity) ---
BG_ROOT = CANVAS
BG_PANEL = SURFACE
BG_CARD = SURFACE_2
BG_CARD_ACTIVE = SURFACE_3
BORDER_ACTIVE = LINK
BORDER_NORMAL = HAIRLINE
TEXT_MAIN = INK
TEXT_MUTED = MUTE
TEXT_SUB = BODY

ACCENT_BLUE = LINK
ACCENT_GREEN = CYAN
ACCENT_ORANGE = WARNING
ACCENT_RED = ERROR
ACCENT_PURPLE = VIOLET
ACCENT_YELLOW = WARNING

# Crosshair / reticle colour for the active pin on the screenshot.
RETICLE = CYAN


def _rgb(hex_color):
    """#rrggbb -> (r, g, b) for PIL draw calls."""
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

CALIBRATION_TARGETS = [
    {
        "id": "slot_1_plus",
        "name": "Slot 1 [+] Button",
        "tag": "S1+",
        "color": "#007cf0",
        "desc": "Plus icon [+] inside 1st pet slot",
        "type": "slot",
        "slot_idx": 0,
        "field": "slot_click",
        "default": (287, 300)
    },
    {
        "id": "slot_1_claim",
        "name": "Slot 1 [CLAIM] Button",
        "tag": "S1-C",
        "color": "#00dfd8",
        "desc": "Green [CLAIM] button on 1st slot",
        "type": "slot",
        "slot_idx": 0,
        "field": "claim_click",
        "default": (275, 348)
    },
    {
        "id": "slot_2_plus",
        "name": "Slot 2 [+] Button",
        "tag": "S2+",
        "color": "#7928ca",
        "desc": "Plus icon [+] inside 2nd pet slot",
        "type": "slot",
        "slot_idx": 1,
        "field": "slot_click",
        "default": (406, 300)
    },
    {
        "id": "slot_2_claim",
        "name": "Slot 2 [CLAIM] Button",
        "tag": "S2-C",
        "color": "#ff0080",
        "desc": "Green [CLAIM] button on 2nd slot",
        "type": "slot",
        "slot_idx": 1,
        "field": "claim_click",
        "default": (394, 348)
    },
    {
        "id": "slot_3_plus",
        "name": "Slot 3 [+] Button",
        "tag": "S3+",
        "color": "#ff4d4d",
        "desc": "Plus icon [+] inside 3rd pet slot",
        "type": "slot",
        "slot_idx": 2,
        "field": "slot_click",
        "default": (531, 300)
    },
    {
        "id": "slot_3_claim",
        "name": "Slot 3 [CLAIM] Button",
        "tag": "S3-C",
        "color": "#f9cb28",
        "desc": "Green [CLAIM] button on 3rd slot",
        "type": "slot",
        "slot_idx": 2,
        "field": "claim_click",
        "default": (509, 348)
    },
    {
        "id": "pet_select",
        "name": "Pet Selection (First Pet)",
        "tag": "PET",
        "color": "#50e3c2",
        "desc": "Top-left pet icon inside pet modal",
        "type": "common",
        "action_key": "pet_select",
        "default": (243, 268)
    },
    {
        "id": "confirm_yes",
        "name": "Confirm [✔ Yes!] Button",
        "tag": "YES",
        "color": "#0070f3",
        "desc": "Green [✔ Yes!] confirmation button",
        "type": "common",
        "action_key": "confirm_yes",
        "default": (342, 381)
    },
    {
        "id": "cancel_no",
        "name": "Cancel [🚫 No!] Button",
        "tag": "NO",
        "color": "#ee0000",
        "desc": "Red [🚫 No!] button on modal",
        "type": "common",
        "action_key": "cancel_no",
        "default": (473, 386)
    },
    {
        "id": "modal_x",
        "name": "Modal Close [✕] Button",
        "tag": "✕",
        "color": "#f5a623",
        "desc": "Red [✕] close button on modal",
        "type": "common",
        "action_key": "modal_x",
        "default": (534, 236)
    },
    {
        "id": "popup_close",
        "name": "Stray Pop-up Close [✕]",
        "tag": "POP",
        "color": "#a970ff",
        "desc": "Close [✕] on an unexpected pop-up (Robux / shop prompt). "
                "Leave this alone unless you have that pop-up on screen - the "
                "macro clicks it to get rid of the pop-up, so it must never "
                "sit on a Buy button.",
        "type": "common",
        "action_key": "popup_close",
        "default": (534, 236)
    }
]


def load_config():
    path = scaling.get_resource_path("config.json")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(cfg):
    # The studio app imports these points into the active profile when this
    # window closes (MacroStudioApp._import_goliath_calibration).
    path = os.path.join(scaling.get_base_dir(), "config.json")
    write_json_atomic(path, cfg)


def update_ahk_script(recorded):
    ahk_path = os.path.join(BASE_DIR, "RobloxPetMacro.ahk")
    if not os.path.isfile(ahk_path):
        return

    try:
        with open(ahk_path, "r", encoding="utf-8") as f:
            content = f.read()

        s1_s = recorded.get("slot_1_plus", (287, 300))
        s1_c = recorded.get("slot_1_claim", (275, 348))
        s2_s = recorded.get("slot_2_plus", (406, 300))
        s2_c = recorded.get("slot_2_claim", (394, 348))
        s3_s = recorded.get("slot_3_plus", (531, 300))
        s3_c = recorded.get("slot_3_claim", (509, 348))
        pet = recorded.get("pet_select", (243, 268))
        yes = recorded.get("confirm_yes", (342, 381))

        new_slots = f"""global Slots := [
    {{
        name: "Slot 1",
        slotClick: {{x: {s1_s[0]}, y: {s1_s[1]}}},
        claimClick: {{x: {s1_c[0]}, y: {s1_c[1]}}},
        scanBox: {{x1: 196, y1: 178, x2: 338, y2: 424}}
    }},
    {{
        name: "Slot 2",
        slotClick: {{x: {s2_s[0]}, y: {s2_s[1]}}},
        claimClick: {{x: {s2_c[0]}, y: {s2_c[1]}}},
        scanBox: {{x1: 338, y1: 178, x2: 480, y2: 424}}
    }},
    {{
        name: "Slot 3",
        slotClick: {{x: {s3_s[0]}, y: {s3_s[1]}}},
        claimClick: {{x: {s3_c[0]}, y: {s3_c[1]}}},
        scanBox: {{x1: 480, y1: 178, x2: 622, y2: 424}}
    }}
]

global PetSelectClick := {{x: {pet[0]}, y: {pet[1]}}}
global ConfirmYesClick := {{x: {yes[0]}, y: {yes[1]}}}"""

        import re
        content = re.sub(
            r"global Slots := \[.*?global ConfirmYesClick := \{[^\}]+\}",
            new_slots,
            content,
            flags=re.DOTALL
        )

        with open(ahk_path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        print(f"[!] Warning updating AHK script: {e}")


class CalibrateGUI:
    def __init__(self, root=None, standalone=True):
        ensure_desktop_access()
        self.standalone = standalone
        if root is None:
            self.root = tk.Tk()
            self.owns_root = True
        else:
            self.root = tk.Toplevel(root)
            self.owns_root = False

        self.root.title("Coordinate Calibrator")
        self.root.geometry("1360x900")
        self.root.minsize(1330, 820)
        self.root.configure(bg=BG_ROOT)

        # Managers
        self.wm = WindowManager(width=800, height=600)
        self.vs = VisionScanner({})
        self.ic = InputController(click_delay=0.35, engine="auto", hold_time=0.15)

        # State
        self.cfg = load_config()
        self.target_hwnd = None
        self.active_target_idx = 0
        self.coords = {}
        self._load_coords_from_config()

        self.raw_image = None       # Full BGR cv2 image (800x600)
        self.display_photo = None   # ImageTk.PhotoImage for Canvas
        self.loupe_photo = None
        self.auto_advance = tk.BooleanVar(value=True)
        self.show_all_pins = tk.BooleanVar(value=True)

        self._build_ui()
        self._locate_and_capture_roblox()

    def _load_coords_from_config(self):
        slots = self.cfg.get("slots", [])
        common = self.cfg.get("common_actions", {})

        for t in CALIBRATION_TARGETS:
            t_id = t["id"]
            default_val = t["default"]

            if t["type"] == "slot":
                s_idx = t["slot_idx"]
                field = t["field"]
                if len(slots) > s_idx and field in slots[s_idx]:
                    val = slots[s_idx][field]
                    self.coords[t_id] = (int(val.get("x", default_val[0])), int(val.get("y", default_val[1])))
                else:
                    self.coords[t_id] = default_val
            elif t["type"] == "common":
                key = t["action_key"]
                if key in common and "x" in common[key] and "y" in common[key]:
                    self.coords[t_id] = (int(common[key]["x"]), int(common[key]["y"]))
                else:
                    self.coords[t_id] = default_val

    def _build_ui(self):
        ui.init_fonts()

        # ---- Topbar ----------------------------------------------------
        topbar = tk.Frame(self.root, bg=CANVAS, height=64)
        topbar.pack(fill="x", side="top")
        topbar.pack_propagate(False)

        tb = tk.Frame(topbar, bg=CANVAS)
        tb.pack(fill="both", expand=True, padx=SP_XL, pady=SP_MD)

        tk.Label(tb, text="▲", font=ui.sans(15, "bold"),
                 bg=CANVAS, fg=INK).pack(side="left", padx=(0, SP_XS))
        tk.Label(tb, text="Coordinate Calibrator", font=ui.sans(20, "bold"),
                 bg=CANVAS, fg=INK).pack(side="left")

        self.status_badge = Badge(tb, "Initializing", MUTE, page_bg=CANVAS)
        self.status_badge.pack(side="left", padx=SP_MD)

        Button(tb, "Save & Sync All", command=self._save_all, kind="primary",
               height=32, page_bg=CANVAS).pack(side="right", padx=(SP_XS, 0))
        Button(tb, "Capture / Refresh", command=self._refresh_screen, kind="secondary",
               height=32, page_bg=CANVAS).pack(side="right", padx=SP_XS)
        Button(tb, "Snap 800x600", command=self._snap_roblox_window, kind="ghost",
               height=32, page_bg=CANVAS).pack(side="right")

        tk.Frame(self.root, bg=HAIRLINE, height=1).pack(fill="x")

        # ---- Main split ------------------------------------------------
        main_container = tk.Frame(self.root, bg=CANVAS)
        main_container.pack(fill="both", expand=True, padx=SP_XL, pady=SP_LG)

        # Left: viewport
        left_box = Card(main_container, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        left_box.pack(side="left", fill="both", expand=True, padx=(0, SP_SM))
        lb = left_box.body

        bar_inst = tk.Frame(lb, bg=SURFACE)
        bar_inst.pack(fill="x", pady=(0, SP_SM))

        self.lbl_target_prompt = tk.Label(
            bar_inst,
            text="Pick a target on the right, then click the game screen to set it",
            font=ui.F_BODY_STRONG(), bg=SURFACE, fg=INK, anchor="w")
        self.lbl_target_prompt.pack(side="left")

        self.lbl_cursor_pos = tk.Label(
            bar_inst, text="MOUSE  ---, ---", font=ui.F_EYEBROW(),
            bg=SURFACE, fg=CYAN)
        self.lbl_cursor_pos.pack(side="right")

        canvas_container = tk.Frame(lb, bg=HAIRLINE, padx=1, pady=1)
        canvas_container.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(
            canvas_container, width=800, height=600, bg=SURFACE_2,
            highlightthickness=0, bd=0, cursor="crosshair")
        self.canvas.pack(anchor="center", expand=True)

        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<Leave>", self._on_canvas_leave)

        canvas_bottom = tk.Frame(lb, bg=SURFACE)
        canvas_bottom.pack(fill="x", pady=(SP_SM, 0))

        def option(parent, label, var, command=None):
            row = tk.Frame(parent, bg=SURFACE)
            row.pack(side="left", padx=(0, SP_LG))
            Toggle(row, var, page_bg=SURFACE, command=command).pack(side="left")
            tk.Label(row, text=label, font=ui.F_CAPTION(), bg=SURFACE,
                     fg=BODY).pack(side="left", padx=SP_XS)

        option(canvas_bottom, "Auto-advance after each click", self.auto_advance)
        option(canvas_bottom, "Show all calibrated pins", self.show_all_pins,
               command=self._redraw_canvas)

        # Right: target list + fine-tuning
        right_panel = tk.Frame(main_container, bg=CANVAS, width=390)
        right_panel.pack(side="right", fill="y")
        right_panel.pack_propagate(False)

        head = tk.Frame(right_panel, bg=CANVAS)
        head.pack(fill="x", pady=(0, SP_SM))
        tk.Label(head, text="Calibration targets", font=ui.sans(16, "bold"),
                 bg=CANVAS, fg=INK, anchor="w").pack(fill="x")
        ui.eyebrow(head, "select a target, then click the screenshot",
                   bg=CANVAS).pack(fill="x", pady=(2, 0))

        # Scrollable target list
        list_canvas = tk.Canvas(right_panel, bg=CANVAS, highlightthickness=0, bd=0)
        self.targets_scroll_frame = tk.Frame(list_canvas, bg=CANVAS)
        win = list_canvas.create_window((0, 0), window=self.targets_scroll_frame,
                                        anchor="nw", width=374)

        self.targets_scroll_frame.bind(
            "<Configure>",
            lambda e: list_canvas.configure(scrollregion=list_canvas.bbox("all")))

        def _wheel(event):
            list_canvas.yview_scroll(int(-event.delta / 120), "units")

        list_canvas.bind("<Enter>", lambda e: list_canvas.bind_all("<MouseWheel>", _wheel))
        list_canvas.bind("<Leave>", lambda e: list_canvas.unbind_all("<MouseWheel>"))
        list_canvas.pack(fill="both", expand=True, side="top")

        self.target_cards = []
        self._populate_target_cards()

        # Fine-tuning / live test panel
        self.tool_box = Card(right_panel, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        self.tool_box.pack(fill="x", side="bottom", pady=(SP_SM, 0))
        tbx = self.tool_box.body

        ui.eyebrow(tbx, "fine-tuning & live test").pack(fill="x", pady=(0, SP_XS))

        coord_row = tk.Frame(tbx, bg=SURFACE)
        coord_row.pack(fill="x", pady=(0, SP_SM))

        self.lbl_selected_title = tk.Label(
            coord_row, text="Slot 1 [+] Button", font=ui.F_BODY_STRONG(),
            bg=SURFACE, fg=INK)
        self.lbl_selected_title.pack(side="left")

        self.lbl_selected_coord = tk.Label(
            coord_row, text="(287, 300)", font=ui.mono(13), bg=SURFACE, fg=CYAN)
        self.lbl_selected_coord.pack(side="right")

        nudge_frame = tk.Frame(tbx, bg=SURFACE)
        nudge_frame.pack(fill="x", pady=(0, SP_SM))
        tk.Label(nudge_frame, text="Nudge 1px", font=ui.F_CAPTION(),
                 bg=SURFACE, fg=MUTE).pack(side="left", padx=(0, SP_XS))
        for glyph, (dx, dy) in (("←", (-1, 0)), ("→", (1, 0)),
                                ("↑", (0, -1)), ("↓", (0, 1))):
            Button(nudge_frame, glyph,
                   command=lambda x=dx, y=dy: self._nudge_active(x, y),
                   kind="secondary", width=34, height=28, page_bg=SURFACE,
                   font=ui.F_BODY()).pack(side="left", padx=2)

        Button(tbx, "Test Click on Roblox", command=self._test_click_active,
               kind="primary", width=330, height=34, page_bg=SURFACE).pack(fill="x")
        tk.Label(tbx, text="Sends a real click, then refreshes the screenshot",
                 font=ui.F_CAPTION(), bg=SURFACE, fg=MUTE).pack(pady=(SP_XS, 0))

    def _populate_target_cards(self):
        for widget in self.targets_scroll_frame.winfo_children():
            widget.destroy()
        self.target_cards.clear()

        for idx, t in enumerate(CALIBRATION_TARGETS):
            is_active = (idx == self.active_target_idx)
            card = Card(self.targets_scroll_frame, radius=R_MD,
                        fill=SURFACE_2 if is_active else SURFACE,
                        border=LINK if is_active else HAIRLINE,
                        padding=(SP_SM, SP_XS), page_bg=CANVAS)
            card.pack(fill="x", pady=3)
            cb = card.body

            dot = tk.Label(cb, text="●", font=ui.sans(13, "bold"),
                           bg=cb["bg"], fg=t["color"])
            dot.pack(side="left", padx=(0, SP_XS))

            info_box = tk.Frame(cb, bg=cb["bg"])
            info_box.pack(side="left", fill="both", expand=True)

            lbl_name = tk.Label(info_box, text=t["name"], font=ui.F_BODY_STRONG(),
                                bg=cb["bg"], fg=INK, anchor="w")
            lbl_name.pack(fill="x")

            t_coord = self.coords.get(t["id"], t["default"])
            lbl_sub = tk.Label(
                info_box, text=f"{t_coord[0]}, {t_coord[1]}   {t['desc'][:26]}",
                font=ui.mono(10), bg=cb["bg"], fg=MUTE, anchor="w")
            lbl_sub.pack(fill="x")

            for widget in (card, cb, dot, info_box, lbl_name, lbl_sub):
                widget.bind("<Button-1>", lambda e, i=idx: self._select_target(i))
                widget.configure(cursor="hand2")

            self.target_cards.append({
                "card": card, "body": cb, "dot": dot, "info_box": info_box,
                "lbl_name": lbl_name, "lbl_sub": lbl_sub,
            })

    def _select_target(self, idx):
        self.active_target_idx = idx
        t = CALIBRATION_TARGETS[idx]

        for i, c in enumerate(self.target_cards):
            is_active = (i == idx)
            fill = SURFACE_2 if is_active else SURFACE
            c["card"].restyle(fill=fill, border=LINK if is_active else HAIRLINE)
            for key in ("dot", "info_box", "lbl_name", "lbl_sub"):
                c[key].configure(bg=fill)
            c["lbl_name"].configure(fg=INK if is_active else BODY)

        self.lbl_selected_title.config(text=t["name"], fg=t["color"])
        pt = self.coords.get(t["id"], t["default"])
        self.lbl_selected_coord.config(text=f"({pt[0]}, {pt[1]})")

        self.lbl_target_prompt.config(
            text=f"Click the game screen to set [{t['name']}]  ·  now {pt[0]}, {pt[1]}",
            fg=t["color"])

        self._redraw_canvas()

    def _nudge_active(self, dx, dy):
        t = CALIBRATION_TARGETS[self.active_target_idx]
        cur = self.coords.get(t["id"], t["default"])
        nx = max(0, min(800, cur[0] + dx))
        ny = max(0, min(600, cur[1] + dy))
        self.coords[t["id"]] = (nx, ny)

        self.lbl_selected_coord.config(text=f"({nx}, {ny})")
        card = self.target_cards[self.active_target_idx]
        card["lbl_sub"].config(text=f"{nx}, {ny}   {t['desc'][:26]}")

        self._redraw_canvas()

    def _locate_and_capture_roblox(self):
        ensure_desktop_access()
        windows = self.wm.find_windows()
        if not windows:
            self.status_badge.set("Roblox not detected", ERROR)
            self._render_placeholder_canvas("Roblox window not found.\nLaunch Roblox and click [Capture / Refresh].")
            return

        self.target_hwnd = windows[0]
        title = self.wm.get_window_title(self.target_hwnd)
        self.status_badge.set(f"Connected  {title[:20]}", CYAN)

        self._refresh_screen()

    def _snap_roblox_window(self):
        ensure_desktop_access()
        if not self.target_hwnd:
            self._locate_and_capture_roblox()
        if self.target_hwnd:
            self.wm.shrink_to_minimum(self.target_hwnd)
            self.wm.force_activate_window(self.target_hwnd)
            time.sleep(0.3)
            self._refresh_screen()

    def _refresh_screen(self):
        ensure_desktop_access()
        if not self.target_hwnd:
            windows = self.wm.find_windows()
            if windows:
                self.target_hwnd = windows[0]
            else:
                self._render_placeholder_canvas("Roblox window not found.\nLaunch Roblox and click [Capture / Refresh].")
                return

        img = self.vs.capture_client_full(self.target_hwnd)
        if img is None:
            self._render_placeholder_canvas("Failed to capture game screen.\nMake sure Roblox is not minimized.")
            return

        h, w = img.shape[:2]
        if (w, h) != (800, 600):
            img = cv2.resize(img, (800, 600), interpolation=cv2.INTER_AREA)

        self.raw_image = img
        self._redraw_canvas()

    def _render_placeholder_canvas(self, message):
        self.canvas.delete("all")
        self.canvas.create_rectangle(0, 0, 800, 600, fill=SURFACE_2, outline="")
        self.canvas.create_text(
            400, 300,
            text=message,
            fill=MUTE,
            font=ui.sans(15, "bold"),
            justify="center"
        )

    def _redraw_canvas(self):
        if self.raw_image is None:
            return

        rgb = cv2.cvtColor(self.raw_image, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        draw = ImageDraw.Draw(pil_img, "RGBA")

        active_t = CALIBRATION_TARGETS[self.active_target_idx]

        for idx, t in enumerate(CALIBRATION_TARGETS):
            is_active = (idx == self.active_target_idx)
            if not is_active and not self.show_all_pins.get():
                continue

            pt = self.coords.get(t["id"], t["default"])
            px, py = pt

            color_hex = t["color"]
            h_rgb = tuple(int(color_hex.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))

            if is_active:
                # Glowing outer ring
                draw.ellipse(
                    [(px - 16, py - 16), (px + 16, py + 16)],
                    outline=(*_rgb(RETICLE), 235),
                    width=3
                )
                # Crosshair lines
                draw.line([(px - 22, py), (px + 22, py)], fill=(*_rgb(RETICLE), 205), width=1)
                draw.line([(px, py - 22), (px, py + 22)], fill=(*_rgb(RETICLE), 205), width=1)

                # Center marker
                draw.ellipse(
                    [(px - 7, py - 7), (px + 7, py + 7)],
                    fill=(*h_rgb, 240),
                    outline=(255, 255, 255, 255),
                    width=2
                )
                # Badge tag
                draw.rectangle(
                    [(px + 10, py - 18), (px + 52, py - 2)],
                    fill=(0, 0, 0, 220),
                    outline=(*_rgb(RETICLE), 255),
                    width=1
                )
                draw.text((px + 14, py - 17), t["tag"], fill=(255, 255, 255, 255))
            else:
                # Dimmer normal pin
                draw.ellipse(
                    [(px - 6, py - 6), (px + 6, py + 6)],
                    fill=(*h_rgb, 180),
                    outline=(255, 255, 255, 180),
                    width=1
                )
                draw.rectangle(
                    [(px + 8, py - 14), (px + 40, py - 1)],
                    fill=(0, 0, 0, 180),
                    outline=(*h_rgb, 180),
                    width=1
                )
                draw.text((px + 11, py - 14), t["tag"], fill=(240, 240, 240, 220))

        self.display_photo = ImageTk.PhotoImage(pil_img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.display_photo)

    def _on_canvas_click(self, event):
        """Handle direct click on the game screenshot."""
        cx = event.x
        cy = event.y

        if cx < 0 or cx >= 800 or cy < 0 or cy >= 600:
            return

        active_t = CALIBRATION_TARGETS[self.active_target_idx]
        self.coords[active_t["id"]] = (cx, cy)

        try:
            import winsound
            winsound.Beep(1400, 80)
        except Exception:
            pass

        card = self.target_cards[self.active_target_idx]
        card["lbl_sub"].config(text=f"{cx}, {cy}   {active_t['desc'][:26]}")
        self.lbl_selected_coord.config(text=f"({cx}, {cy})")

        if self.auto_advance.get():
            next_idx = (self.active_target_idx + 1) % len(CALIBRATION_TARGETS)
            self._select_target(next_idx)
        else:
            self._redraw_canvas()

    def _on_canvas_motion(self, event):
        cx = max(0, min(799, event.x))
        cy = max(0, min(599, event.y))
        self.lbl_cursor_pos.config(text=f"Mouse: ({cx}, {cy})")

    def _on_canvas_leave(self, event):
        self.lbl_cursor_pos.config(text="Mouse: (---, ---)")

    def _test_click_active(self):
        """Executes test click on Roblox client and captures screenshot afterwards."""
        ensure_desktop_access()
        if not self.target_hwnd:
            self._locate_and_capture_roblox()
        if not self.target_hwnd:
            messagebox.showwarning("Test Click", "Roblox client window not detected! Please launch Roblox first.")
            return

        active_t = CALIBRATION_TARGETS[self.active_target_idx]
        pt = self.coords.get(active_t["id"], active_t["default"])

        self.wm.force_activate_window(self.target_hwnd)
        time.sleep(0.08)

        client_ox, client_oy = self.wm.get_client_offset(self.target_hwnd)
        self.ic.click(pt[0], pt[1], client_offset=(client_ox, client_oy), relative=True, hwnd=self.target_hwnd)

        self.root.after(350, self._refresh_screen)

    def _save_all(self):
        """Writes all calibrated coordinates to config.json, profiles, and AHK."""
        slots = self.cfg.setdefault("slots", [{}, {}, {}])
        common = self.cfg.setdefault("common_actions", {})

        for t in CALIBRATION_TARGETS:
            pt = self.coords.get(t["id"], t["default"])
            if t["type"] == "slot":
                s_idx = t["slot_idx"]
                field = t["field"]
                while len(slots) <= s_idx:
                    slots.append({})
                slots[s_idx][field] = {"x": pt[0], "y": pt[1]}
            elif t["type"] == "common":
                k = t["action_key"]
                if k not in common:
                    common[k] = {}
                common[k]["x"] = pt[0]
                common[k]["y"] = pt[1]

        save_config(self.cfg)
        update_ahk_script(self.coords)

        try:
            import winsound
            winsound.Beep(1800, 150)
        except Exception:
            pass

        messagebox.showinfo(
            "Calibration Saved",
            "✅ Successfully calibrated all targets!\n\n"
            "• Saved to config.json\n"
            "• Close this window to load the points into the active profile\n\n"
            "You can now start the macro with maximum accuracy!"
        )

    def run(self):
        if self.owns_root:
            self.root.mainloop()


def launch_calibration_gui(parent=None):
    app = CalibrateGUI(root=parent, standalone=(parent is None))
    if parent is None:
        app.run()
    return app


if __name__ == "__main__":
    launch_calibration_gui()
