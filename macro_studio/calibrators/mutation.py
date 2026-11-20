"""
Mutation Calibrator - one-time coordinate capture for the Mutation macro mode.

The mutation loop per account is:
    pet list is already on screen -> click a pet -> click Mutate -> wheel spins
    -> wait -> spin ends, pet list is back -> repeat

So only two click points are needed, plus one region the scanner watches to
tell "wheel still spinning" apart from "pet list is back".

Everything is captured against the reference rig (see scaling.py):
1920x1080 @ 100% scaling, Roblox client snapped to 800x600. The saved file
records that reference so CoordinateMapper can rescale it on any other machine.

Run once:  python calibrate_mutation.py
"""

import os
import json
import time
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk, ImageDraw
import cv2


from macro_studio.core import scaling
from macro_studio.core.win32 import attach_input_desktop
from macro_studio.core.fileio import write_json_atomic

# User data lives next to the exe (or the project when run from source), never
# next to this file - in the one-file exe that is a temp folder deleted on exit.
BASE_DIR = scaling.get_base_dir()
os.chdir(BASE_DIR)
# DPI awareness must be declared before any window or Tk handle exists.
scaling.set_dpi_awareness()


def ensure_desktop_access():
    attach_input_desktop()


ensure_desktop_access()

from macro_studio.core.window_manager import WindowManager
from macro_studio.core.vision_scanner import VisionScanner
from macro_studio.core.input_controller import InputController

from macro_studio.core import ui_theme as ui
from macro_studio.core.ui_theme import (
    CANVAS, SURFACE, SURFACE_2, HAIRLINE,
    INK, BODY, MUTE, LINK, ERROR, CYAN, WARNING,
    SP_XS, SP_SM, SP_MD, SP_LG, SP_XL,
    R_MD, R_LG,
    Card, Button, Badge, Toggle,
)

def mutation_config_path():
    """Where calibrations are saved: beside the exe, or the project from source."""
    return os.path.join(scaling.get_base_dir(), "config_mutation.json")

VIEW_W, VIEW_H = 800, 600

# Two click points, then two corners that define the scan region.
TARGETS = [
    {
        "id": "pet_select",
        "name": "Pet Select",
        "tag": "PET",
        "color": "#0070f3",
        "desc": "The pet in the list to select",
        "kind": "point",
    },
    {
        "id": "mutate",
        "name": "Mutate Button",
        "tag": "MUT",
        "color": "#7928ca",
        "desc": "The Mutate / Mutation button",
        "kind": "point",
    },
    {
        "id": "scan_tl",
        "name": "Scan Region - Top Left",
        "tag": "TL",
        "color": "#50e3c2",
        "desc": "Top-left corner of the wheel / pet-list area",
        "kind": "corner",
    },
    {
        "id": "scan_br",
        "name": "Scan Region - Bottom Right",
        "tag": "BR",
        "color": "#f5a623",
        "desc": "Bottom-right corner of that same area",
        "kind": "corner",
    },
]

DEFAULTS = {
    "pet_select": (243, 268),
    "mutate": (400, 450),
    "scan_tl": (200, 180),
    "scan_br": (600, 430),
}


def load_mutation_config():
    path = scaling.get_resource_path("config_mutation.json")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


class MutationCalibrator:
    def __init__(self, root=None):
        ensure_desktop_access()
        if root is None:
            self.root = tk.Tk()
            self.owns_root = True
        else:
            self.root = tk.Toplevel(root)
            self.owns_root = False

        self.root.title("Mutation Calibrator")
        self.root.geometry("1360x900")
        self.root.minsize(1330, 820)
        self.root.configure(bg=CANVAS)

        self.wm = WindowManager(width=800, height=600)
        self.vs = VisionScanner({})
        self.ic = InputController(click_delay=0.35, engine="auto", hold_time=0.15)

        self.target_hwnd = None
        self.active_idx = 0
        self.raw_image = None
        self.display_photo = None
        self.client_size = scaling.REFERENCE_CLIENT
        self.show_all_pins = tk.BooleanVar(value=True)
        self.auto_advance = tk.BooleanVar(value=True)

        self.coords = {}
        self._load_coords()

        self._build_ui()
        self._locate_and_capture()

    # ---------------- config ----------------
    def _load_coords(self):
        cfg = load_mutation_config()
        saved = cfg.get("targets", {})
        region = cfg.get("scan_region")
        for t in TARGETS:
            tid = t["id"]
            if tid in saved:
                self.coords[tid] = (int(saved[tid]["x"]), int(saved[tid]["y"]))
            else:
                self.coords[tid] = DEFAULTS[tid]
        if region:
            self.coords["scan_tl"] = (int(region["x"]), int(region["y"]))
            self.coords["scan_br"] = (int(region["x"] + region["width"]),
                                      int(region["y"] + region["height"]))

    def _scan_region(self):
        tl = self.coords["scan_tl"]
        br = self.coords["scan_br"]
        x, y = min(tl[0], br[0]), min(tl[1], br[1])
        w, h = abs(br[0] - tl[0]), abs(br[1] - tl[1])
        return {"x": x, "y": y, "width": w, "height": h}

    # ---------------- UI ----------------
    def _build_ui(self):
        ui.init_fonts()

        topbar = tk.Frame(self.root, bg=CANVAS, height=64)
        topbar.pack(fill="x", side="top")
        topbar.pack_propagate(False)
        tb = tk.Frame(topbar, bg=CANVAS)
        tb.pack(fill="both", expand=True, padx=SP_XL, pady=SP_MD)

        tk.Label(tb, text="▲", font=ui.sans(15, "bold"),
                 bg=CANVAS, fg=INK).pack(side="left", padx=(0, SP_XS))
        tk.Label(tb, text="Mutation Calibrator", font=ui.sans(20, "bold"),
                 bg=CANVAS, fg=INK).pack(side="left")

        self.status_badge = Badge(tb, "Initializing", MUTE, page_bg=CANVAS)
        self.status_badge.pack(side="left", padx=SP_MD)

        Button(tb, "Save Calibration", command=self._save_all, kind="primary",
               height=32, page_bg=CANVAS).pack(side="right", padx=(SP_XS, 0))
        Button(tb, "Capture / Refresh", command=self._refresh_screen, kind="secondary",
               height=32, page_bg=CANVAS).pack(side="right", padx=SP_XS)
        Button(tb, "Snap 800x600", command=self._snap_window, kind="ghost",
               height=32, page_bg=CANVAS).pack(side="right")

        tk.Frame(self.root, bg=HAIRLINE, height=1).pack(fill="x")

        main = tk.Frame(self.root, bg=CANVAS)
        main.pack(fill="both", expand=True, padx=SP_XL, pady=SP_LG)

        # Viewport
        left = Card(main, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        left.pack(side="left", fill="both", expand=True, padx=(0, SP_SM))
        lb = left.body

        bar = tk.Frame(lb, bg=SURFACE)
        bar.pack(fill="x", pady=(0, SP_SM))
        self.lbl_prompt = tk.Label(bar, text="", font=ui.F_BODY_STRONG(),
                                   bg=SURFACE, fg=INK, anchor="w")
        self.lbl_prompt.pack(side="left")
        self.lbl_cursor = tk.Label(bar, text="MOUSE  ---, ---", font=ui.F_EYEBROW(),
                                   bg=SURFACE, fg=CYAN)
        self.lbl_cursor.pack(side="right")

        holder = tk.Frame(lb, bg=HAIRLINE, padx=1, pady=1)
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(holder, width=VIEW_W, height=VIEW_H, bg=SURFACE_2,
                                highlightthickness=0, bd=0, cursor="crosshair")
        self.canvas.pack(anchor="center", expand=True)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda e: self.lbl_cursor.config(text="MOUSE  ---, ---"))

        opts = tk.Frame(lb, bg=SURFACE)
        opts.pack(fill="x", pady=(SP_SM, 0))

        def option(label, var, command=None):
            row = tk.Frame(opts, bg=SURFACE)
            row.pack(side="left", padx=(0, SP_LG))
            Toggle(row, var, page_bg=SURFACE, command=command).pack(side="left")
            tk.Label(row, text=label, font=ui.F_CAPTION(),
                     bg=SURFACE, fg=BODY).pack(side="left", padx=SP_XS)

        option("Auto-advance after each click", self.auto_advance)
        option("Show all pins", self.show_all_pins, command=self._redraw)

        # Right panel
        right = tk.Frame(main, bg=CANVAS, width=390)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        head = tk.Frame(right, bg=CANVAS)
        head.pack(fill="x", pady=(0, SP_SM))
        tk.Label(head, text="Mutation targets", font=ui.sans(16, "bold"),
                 bg=CANVAS, fg=INK, anchor="w").pack(fill="x")
        ui.eyebrow(head, "select a target, then click the screenshot",
                   bg=CANVAS).pack(fill="x", pady=(2, 0))

        self.cards_frame = tk.Frame(right, bg=CANVAS)
        self.cards_frame.pack(fill="x")
        self.target_cards = []
        self._populate_cards()

        # Reference / environment info
        info = Card(right, radius=R_MD, padding=(SP_SM, SP_SM), page_bg=CANVAS)
        info.pack(fill="x", pady=(SP_SM, 0))
        ui.eyebrow(info.body, "capture reference").pack(fill="x")
        self.lbl_ref = tk.Label(info.body, text="", font=ui.mono(10), bg=SURFACE,
                                fg=BODY, anchor="w", justify="left")
        self.lbl_ref.pack(fill="x", pady=(SP_XS, 0))

        # Fine tune
        tool = Card(right, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        tool.pack(fill="x", side="bottom", pady=(SP_SM, 0))
        tb2 = tool.body
        ui.eyebrow(tb2, "fine-tuning & live test").pack(fill="x", pady=(0, SP_XS))

        row = tk.Frame(tb2, bg=SURFACE)
        row.pack(fill="x", pady=(0, SP_SM))
        self.lbl_sel_title = tk.Label(row, text="", font=ui.F_BODY_STRONG(),
                                      bg=SURFACE, fg=INK)
        self.lbl_sel_title.pack(side="left")
        self.lbl_sel_coord = tk.Label(row, text="", font=ui.mono(13),
                                      bg=SURFACE, fg=CYAN)
        self.lbl_sel_coord.pack(side="right")

        nudge = tk.Frame(tb2, bg=SURFACE)
        nudge.pack(fill="x", pady=(0, SP_SM))
        tk.Label(nudge, text="Nudge 1px", font=ui.F_CAPTION(),
                 bg=SURFACE, fg=MUTE).pack(side="left", padx=(0, SP_XS))
        for glyph, (dx, dy) in (("←", (-1, 0)), ("→", (1, 0)),
                                ("↑", (0, -1)), ("↓", (0, 1))):
            Button(nudge, glyph, command=lambda x=dx, y=dy: self._nudge(x, y),
                   kind="secondary", width=34, height=28, page_bg=SURFACE,
                   font=ui.F_BODY()).pack(side="left", padx=2)

        Button(tb2, "Test Click on Roblox", command=self._test_click,
               kind="primary", width=330, height=34, page_bg=SURFACE).pack(fill="x")
        tk.Label(tb2, text="Only works on point targets, not region corners",
                 font=ui.F_CAPTION(), bg=SURFACE, fg=MUTE).pack(pady=(SP_XS, 0))

        self._select(0)

    def _populate_cards(self):
        for w in self.cards_frame.winfo_children():
            w.destroy()
        self.target_cards.clear()

        for idx, t in enumerate(TARGETS):
            active = (idx == self.active_idx)
            card = Card(self.cards_frame, radius=R_MD,
                        fill=SURFACE_2 if active else SURFACE,
                        border=LINK if active else HAIRLINE,
                        padding=(SP_SM, SP_XS), page_bg=CANVAS)
            card.pack(fill="x", pady=3)
            cb = card.body

            dot = tk.Label(cb, text="●", font=ui.sans(13, "bold"),
                           bg=cb["bg"], fg=t["color"])
            dot.pack(side="left", padx=(0, SP_XS))
            info = tk.Frame(cb, bg=cb["bg"])
            info.pack(side="left", fill="both", expand=True)

            name = tk.Label(info, text=t["name"], font=ui.F_BODY_STRONG(),
                            bg=cb["bg"], fg=INK, anchor="w")
            name.pack(fill="x")
            pt = self.coords[t["id"]]
            sub = tk.Label(info, text=f"{pt[0]}, {pt[1]}   {t['desc'][:26]}",
                           font=ui.mono(10), bg=cb["bg"], fg=MUTE, anchor="w")
            sub.pack(fill="x")

            for w in (card, cb, dot, info, name, sub):
                w.bind("<Button-1>", lambda e, i=idx: self._select(i))
                w.configure(cursor="hand2")

            self.target_cards.append({"card": card, "dot": dot, "info": info,
                                      "name": name, "sub": sub})

    def _select(self, idx):
        self.active_idx = idx
        t = TARGETS[idx]
        for i, c in enumerate(self.target_cards):
            active = (i == idx)
            fill = SURFACE_2 if active else SURFACE
            c["card"].restyle(fill=fill, border=LINK if active else HAIRLINE)
            for k in ("dot", "info", "name", "sub"):
                c[k].configure(bg=fill)
            c["name"].configure(fg=INK if active else BODY)

        pt = self.coords[t["id"]]
        self.lbl_sel_title.config(text=t["name"], fg=t["color"])
        self.lbl_sel_coord.config(text=f"({pt[0]}, {pt[1]})")
        self.lbl_prompt.config(
            text=f"Click the game screen to set [{t['name']}]  ·  now {pt[0]}, {pt[1]}",
            fg=t["color"])
        self._redraw()

    def _nudge(self, dx, dy):
        t = TARGETS[self.active_idx]
        x, y = self.coords[t["id"]]
        x = max(0, min(VIEW_W, x + dx))
        y = max(0, min(VIEW_H, y + dy))
        self.coords[t["id"]] = (x, y)
        self.lbl_sel_coord.config(text=f"({x}, {y})")
        self.target_cards[self.active_idx]["sub"].config(
            text=f"{x}, {y}   {t['desc'][:26]}")
        self._redraw()

    # ---------------- capture ----------------
    def _locate_and_capture(self):
        ensure_desktop_access()
        windows = self.wm.find_windows()
        if not windows:
            self.status_badge.set("Roblox not detected", ERROR)
            self._placeholder("Roblox window not found.\nLaunch Roblox, then Capture / Refresh.")
            self._update_ref_label()
            return
        self.target_hwnd = windows[0]
        title = self.wm.get_window_title(self.target_hwnd)
        self.status_badge.set(f"Connected  {title[:20]}", CYAN)
        self._refresh_screen()

    def _snap_window(self):
        ensure_desktop_access()
        if not self.target_hwnd:
            self._locate_and_capture()
        if self.target_hwnd:
            self.wm.shrink_to_minimum(self.target_hwnd)
            self.wm.force_activate_window(self.target_hwnd)
            time.sleep(0.3)
            self._refresh_screen()

    def _refresh_screen(self):
        ensure_desktop_access()
        if not self.target_hwnd:
            windows = self.wm.find_windows()
            if not windows:
                self._placeholder("Roblox window not found.\nLaunch Roblox, then Capture / Refresh.")
                return
            self.target_hwnd = windows[0]

        self.client_size = scaling.get_client_size(self.target_hwnd)
        img = self.vs.capture_client_full(self.target_hwnd)
        if img is None:
            self._placeholder("Failed to capture the game screen.\nMake sure Roblox is not minimized.")
            return

        h, w = img.shape[:2]
        if (w, h) != (VIEW_W, VIEW_H):
            img = cv2.resize(img, (VIEW_W, VIEW_H), interpolation=cv2.INTER_AREA)
        self.raw_image = img
        self._update_ref_label()
        self._redraw()

    def _update_ref_label(self):
        cw, ch = self.client_size
        ref_w, ref_h = scaling.REFERENCE_CLIENT
        match = "matches reference" if (cw, ch) == (ref_w, ref_h) else "WILL BE RESCALED"
        self.lbl_ref.config(
            text=(f"client   {cw} x {ch}  ({match})\n"
                  f"ref      {ref_w} x {ref_h} @ 100%\n"
                  f"dpi      {scaling.get_system_dpi()}  "
                  f"aware={scaling.set_dpi_awareness()}"))

    def _placeholder(self, msg):
        self.canvas.delete("all")
        self.canvas.create_rectangle(0, 0, VIEW_W, VIEW_H, fill=SURFACE_2, outline="")
        self.canvas.create_text(VIEW_W // 2, VIEW_H // 2, text=msg, fill=MUTE,
                                font=ui.sans(15, "bold"), justify="center")

    # ---------------- drawing ----------------
    def _redraw(self):
        if self.raw_image is None:
            return
        rgb = cv2.cvtColor(self.raw_image, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        draw = ImageDraw.Draw(img, "RGBA")

        # Scan region rectangle
        r = self._scan_region()
        if r["width"] > 0 and r["height"] > 0:
            draw.rectangle(
                [(r["x"], r["y"]), (r["x"] + r["width"], r["y"] + r["height"])],
                outline=(*_rgb(CYAN), 200), width=2)
            draw.text((r["x"] + 4, r["y"] + 4),
                      f"SCAN {r['width']}x{r['height']}", fill=(*_rgb(CYAN), 255))

        for idx, t in enumerate(TARGETS):
            active = (idx == self.active_idx)
            if not active and not self.show_all_pins.get():
                continue
            px, py = self.coords[t["id"]]
            col = _rgb(t["color"])

            if active:
                draw.ellipse([(px - 16, py - 16), (px + 16, py + 16)],
                             outline=(*_rgb(CYAN), 235), width=3)
                draw.line([(px - 22, py), (px + 22, py)], fill=(*_rgb(CYAN), 205), width=1)
                draw.line([(px, py - 22), (px, py + 22)], fill=(*_rgb(CYAN), 205), width=1)
                draw.ellipse([(px - 7, py - 7), (px + 7, py + 7)],
                             fill=(*col, 240), outline=(255, 255, 255, 255), width=2)
                draw.rectangle([(px + 10, py - 18), (px + 54, py - 2)],
                               fill=(0, 0, 0, 220), outline=(*_rgb(CYAN), 255), width=1)
                draw.text((px + 14, py - 17), t["tag"], fill=(255, 255, 255, 255))
            else:
                draw.ellipse([(px - 6, py - 6), (px + 6, py + 6)],
                             fill=(*col, 180), outline=(255, 255, 255, 180), width=1)
                draw.rectangle([(px + 8, py - 14), (px + 44, py - 1)],
                               fill=(0, 0, 0, 180), outline=(*col, 180), width=1)
                draw.text((px + 11, py - 14), t["tag"], fill=(240, 240, 240, 220))

        self.display_photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.display_photo)

    # ---------------- events ----------------
    def _on_click(self, event):
        cx, cy = event.x, event.y
        if not (0 <= cx < VIEW_W and 0 <= cy < VIEW_H):
            return
        t = TARGETS[self.active_idx]
        self.coords[t["id"]] = (cx, cy)
        try:
            import winsound
            winsound.Beep(1400, 70)
        except Exception:
            pass
        self.target_cards[self.active_idx]["sub"].config(
            text=f"{cx}, {cy}   {t['desc'][:26]}")
        self.lbl_sel_coord.config(text=f"({cx}, {cy})")

        if self.auto_advance.get() and self.active_idx < len(TARGETS) - 1:
            self._select(self.active_idx + 1)
        else:
            self._redraw()

    def _on_motion(self, event):
        cx = max(0, min(VIEW_W - 1, event.x))
        cy = max(0, min(VIEW_H - 1, event.y))
        self.lbl_cursor.config(text=f"MOUSE  {cx}, {cy}")

    def _test_click(self):
        ensure_desktop_access()
        t = TARGETS[self.active_idx]
        if t["kind"] != "point":
            messagebox.showinfo("Test Click",
                                "This target is a scan-region corner, not a click point.")
            return
        if not self.target_hwnd:
            self._locate_and_capture()
        if not self.target_hwnd:
            messagebox.showwarning("Test Click", "Roblox client window not detected.")
            return

        pt = self.coords[t["id"]]
        self.wm.force_activate_window(self.target_hwnd)
        time.sleep(0.08)
        # Points are picked on a capture of the CLIENT area, so the test click
        # has to be measured from the client origin too. Using the window frame
        # made this test land a title bar higher than the point being tested,
        # which quietly "confirmed" the wrong pixel.
        ox, oy = self.wm.get_client_offset(self.target_hwnd)
        self.ic.click(pt[0], pt[1], client_offset=(ox, oy), relative=True,
                      hwnd=self.target_hwnd)
        self.root.after(350, self._refresh_screen)

    # ---------------- save ----------------
    def _save_all(self):
        region = self._scan_region()
        if region["width"] < 10 or region["height"] < 10:
            messagebox.showwarning(
                "Scan Region Too Small",
                "The scan region is smaller than 10x10 px.\n\n"
                "Set 'Scan Region - Top Left' and 'Scan Region - Bottom Right' "
                "around the wheel / pet-list area so the scanner has something to look at.")
            return

        data = {
            "_comment": ("Captured on the reference rig. CoordinateMapper in scaling.py "
                         "rescales these for any other client size or DPI."),
            "reference_client": {"width": scaling.REFERENCE_CLIENT[0],
                                 "height": scaling.REFERENCE_CLIENT[1]},
            "reference_screen": {"width": scaling.REFERENCE_SCREEN[0],
                                 "height": scaling.REFERENCE_SCREEN[1]},
            "reference_dpi": scaling.REFERENCE_DPI,
            "captured_client": {"width": self.client_size[0], "height": self.client_size[1]},
            "targets": {
                "pet_select": {"x": self.coords["pet_select"][0],
                               "y": self.coords["pet_select"][1]},
                "mutate": {"x": self.coords["mutate"][0],
                           "y": self.coords["mutate"][1]},
            },
            "scan_region": region,
        }

        write_json_atomic(mutation_config_path(), data)

        try:
            import winsound
            winsound.Beep(1800, 150)
        except Exception:
            pass

        messagebox.showinfo(
            "Mutation Calibration Saved",
            f"Saved to config_mutation.json\n\n"
            f"Pet select : {data['targets']['pet_select']['x']}, "
            f"{data['targets']['pet_select']['y']}\n"
            f"Mutate     : {data['targets']['mutate']['x']}, "
            f"{data['targets']['mutate']['y']}\n"
            f"Scan region: {region['width']} x {region['height']} "
            f"at {region['x']}, {region['y']}\n\n"
            "This is a one-time capture - the Mutation mode will use it from now on.")

    def run(self):
        if self.owns_root:
            self.root.mainloop()


def launch(parent=None):
    app = MutationCalibrator(root=parent)
    if parent is None:
        app.run()
    return app


if __name__ == "__main__":
    launch()
