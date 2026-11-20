"""
Profile & Multi-Macro Manager
Handles saving, loading, creating, and switching between multiple macro modes,
different machine resolutions, and custom slot setups.
"""

import os
import json
from macro_studio.core import scaling
from macro_studio.core.fileio import copy_atomic, write_json_atomic

PROFILES_DIR = os.path.join(scaling.get_base_dir(), "profiles")

# ---------------------------------------------------------------------------
# Which config keys belong to which macro.
#
# The two macros click completely different things, so a key that only one of
# them understands must never survive in the other's profile. A Goliath
# "claim_click" sitting in a mutation profile is not harmless clutter: it is a
# coordinate that some future code path can pick up and click blindly.
# ---------------------------------------------------------------------------
GOLIATH_ONLY_KEYS = {
    "slots",              # 3 daycare slots, each with claim/slot clicks
    "common_actions",     # shared pop-up buttons (pet_select / confirm_yes)
    "overall_scan_region",
    "detection",          # HSV/OCR rules for CLAIM / EMPTY / BUSY
    "claim_schedule",     # per-account claim timers
    "slot_fill",          # "are all slots filled" verification sweep
}

MUTATION_ONLY_KEYS = {
    "targets",            # pet_select / mutate click points
    "scan_region",        # the spin-wheel watch box
    "reference_client",
    "reference_screen",
    "reference_dpi",
    "captured_client",
}


def profile_mode(cfg):
    """The macro a config belongs to, from its own metadata."""
    mode = (cfg or {}).get("meta", {}).get("macro_mode")
    return mode if mode in ("goliath", "mutation") else "goliath"


def sanitize_profile(cfg):
    """Strip keys belonging to the *other* macro. Returns (cfg, removed_keys)."""
    mode = profile_mode(cfg)
    foreign = MUTATION_ONLY_KEYS if mode == "goliath" else GOLIATH_ONLY_KEYS
    removed = sorted(k for k in foreign if k in cfg)
    for key in removed:
        del cfg[key]
    return cfg, removed


class ProfileManager:
    def __init__(self):
        os.makedirs(PROFILES_DIR, exist_ok=True)
        self._ensure_default_profiles()

    def _get_default_3slot_data(self):
        return {
            "meta": {
                "name": "Roblox 3-Slot Pet Claim",
                "description": "Standard 3-slot pet claim and auto-add for Roblox Daycare",
                "machine_tag": "Local PC (800x600)"
            },
            "window_settings": {
                "target_process_names": ["robloxplayerbeta.exe", "roblox.exe"],
                "ignore_process_names": ["roblox account manager.exe", "chrome.exe", "msedge.exe", "brave.exe", "firefox.exe"],
                "auto_arrange_windows": True,
                "auto_shrink_to_minimum": True,
                "layout_style": "diagonal",
                "diagonal_gap": 10,
                "diagonal_offset_x": 10,
                "diagonal_offset_y": 10,
                "grid_columns": 2,
                "standard_width": 800,
                "standard_height": 600,
                "use_window_relative_coords": True
            },
            "overall_scan_region": {
                "x": 196,
                "y": 178,
                "width": 426,
                "height": 246
            },
            "slots": [
                {
                    "id": 1,
                    "name": "Slot 1",
                    "slot_click": {"x": 273, "y": 288},
                    "claim_click": {"x": 275, "y": 348},
                    "scan_region": {"x": 196, "y": 178, "width": 142, "height": 246}
                },
                {
                    "id": 2,
                    "name": "Slot 2",
                    "slot_click": {"x": 394, "y": 288},
                    "claim_click": {"x": 394, "y": 348},
                    "scan_region": {"x": 338, "y": 178, "width": 142, "height": 246}
                },
                {
                    "id": 3,
                    "name": "Slot 3",
                    "slot_click": {"x": 509, "y": 273},
                    "claim_click": {"x": 509, "y": 348},
                    "scan_region": {"x": 480, "y": 178, "width": 142, "height": 246}
                }
            ],
            "common_actions": {
                "pet_select": {"x": 237, "y": 234},
                "confirm_yes": {"x": 326, "y": 338}
            },
            "detection": {
                "mode": "color_and_template",
                "save_debug_screenshots": True,
                "claim_green_hsv": {
                    "lower": [35, 70, 70],
                    "upper": [85, 255, 255],
                    "min_matching_pixels": 80
                },
                "empty_slot_hsv": {
                    "lower": [0, 0, 50],
                    "upper": [180, 50, 180],
                    "min_matching_pixels": 120
                },
                "ocr_claim_keywords": ["CLAIM", "COLLECT", "READY", "00:00", "0S"],
                "ocr_empty_keywords": ["ADD", "EMPTY", "SELECT", "+", "EQUIP"]
            },
            "delays": {
                "calm_mode": True,
                "window_focus_delay": 0.6,
                "click_delay": 0.35,
                "after_claim_delay": 1.0,
                "after_pet_select_delay": 0.5,
                "after_confirm_delay": 0.6,
                "between_slots_delay": 0.5,
                "cycle_delay": 6.0
            },
            "hotkeys": {
                "emergency_stop": "f6",
                "fallback_stop": "escape",
                "toggle_pause": "f7"
            },
            "ui": {
                "show_floating_stop_button": True
            }
        }

    def _ensure_default_profiles(self):
        # 1. Goliath Macro Profile
        goliath_file = os.path.join(PROFILES_DIR, "Goliath.json")
        if not os.path.exists(goliath_file):
            bundle_goliath = scaling.get_resource_path(os.path.join("profiles", "Goliath.json"))
            if os.path.exists(bundle_goliath) and bundle_goliath != goliath_file:
                copy_atomic(bundle_goliath, goliath_file)
            else:
                c_data = self._get_default_3slot_data()
                c_data["meta"]["name"] = "Goliath"
                c_data["meta"]["macro_mode"] = "goliath"
                c_data["slots"][0]["slot_click"] = {"x": 493, "y": 388}
                c_data["slots"][0]["claim_click"] = {"x": 294, "y": 379}
                c_data["slots"][1]["slot_click"] = {"x": 406, "y": 299}
                c_data["slots"][1]["claim_click"] = {"x": 407, "y": 380}
                c_data["slots"][2]["slot_click"] = {"x": 522, "y": 317}
                c_data["slots"][2]["claim_click"] = {"x": 532, "y": 379}
                c_data["common_actions"]["pet_select"] = {"x": 252, "y": 269}
                c_data["common_actions"]["confirm_yes"] = {"x": 341, "y": 382}
                write_json_atomic(goliath_file, c_data)

        # 2. Mutation Macro Profile
        mutation_file = os.path.join(PROFILES_DIR, "Mutation.json")
        if not os.path.exists(mutation_file):
            bundle_mutation = scaling.get_resource_path(os.path.join("profiles", "Mutation.json"))
            if os.path.exists(bundle_mutation) and bundle_mutation != mutation_file:
                copy_atomic(bundle_mutation, mutation_file)
            else:
                mut_cfg_path = scaling.get_resource_path("config_mutation.json")
                if os.path.exists(mut_cfg_path):
                    with open(mut_cfg_path, "r", encoding="utf-8-sig") as f:
                        mut_data = json.load(f)
                else:
                    mut_data = {}
                mut_data["meta"] = {
                    "name": "Mutation",
                    "description": "Multi-account automatic pet mutation loop",
                    "macro_mode": "mutation",
                    "machine_tag": "Local PC (800x600)"
                }
                write_json_atomic(mutation_file, mut_data)

        # Clean up legacy obsolete profiles
        legacy_files = [
            "Goliath_Daycare_Macro.json", "Pet_Mutation_Macro.json",
            "Roblox_Goliath_Calibrated.json", "Roblox_Daycare_3Slots.json",
            "Machine2_Custom_Resolution.json"
        ]
        for leg in legacy_files:
            leg_path = os.path.join(PROFILES_DIR, leg)
            if os.path.exists(leg_path):
                try:
                    os.remove(leg_path)
                except Exception:
                    pass

    def list_profiles(self):
        files = [f for f in os.listdir(PROFILES_DIR) if f.endswith(".json")]
        profiles = []
        for f in sorted(files):
            name = f[:-5] # remove .json
            profiles.append(name)
        return profiles

    def load_profile(self, name):
        filename = f"{name}.json" if not name.endswith(".json") else name
        filepath = os.path.join(PROFILES_DIR, filename)
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Profile {name} does not exist.")
        with open(filepath, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        # Old profiles may still carry the other macro's keys; drop them here so
        # nothing downstream can read a coordinate that belongs to a different UI.
        sanitize_profile(data)
        return data

    def _get_default_mutation_data(self):
        """A mutation profile carries only what MutationRunner reads."""
        base = self._get_default_3slot_data()
        shared = {k: base[k] for k in ("window_settings", "hotkeys", "ui")}
        return {
            "meta": {
                "name": "Pet Mutation Macro",
                "description": "Multi-account automatic pet mutation loop",
                "macro_mode": "mutation",
                "machine_tag": "Local PC (800x600)",
            },
            "reference_client": {"width": 800, "height": 600},
            "targets": {
                "pet_select": {"x": 208, "y": 240},
                "mutate": {"x": 555, "y": 414},
            },
            "scan_region": {"x": 176, "y": 114, "width": 426, "height": 412},
            "delays": {
                "calm_mode": True,
                "window_focus_delay": 0.6,
                "click_delay": 0.35,
                "after_pet_select_delay": 0.35,
                "after_mutate_delay": 0.6,
                "mutation_poll_delay": 2.0,
                "between_slots_delay": 0.35,
                "cycle_delay": 2.0,
            },
            **shared,
        }

    def save_profile(self, name, data):
        # Last line of defence: a profile on disk never carries the other
        # macro's coordinates, whatever the caller handed us.
        sanitize_profile(data)
        filename = f"{name}.json" if not name.endswith(".json") else name
        filepath = os.path.join(PROFILES_DIR, filename)
        write_json_atomic(filepath, data)
        return True

    def duplicate_profile(self, source_name, new_name):
        src = os.path.join(PROFILES_DIR, f"{source_name}.json")
        dst = os.path.join(PROFILES_DIR, f"{new_name}.json")
        if os.path.exists(dst):
            raise FileExistsError(f"Profile {new_name} already exists.")
        copy_atomic(src, dst)
        return True

    def delete_profile(self, name):
        filepath = os.path.join(PROFILES_DIR, f"{name}.json")
        if os.path.exists(filepath):
            os.remove(filepath)
            return True
        return False
