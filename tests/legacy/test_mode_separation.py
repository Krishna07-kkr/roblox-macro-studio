"""Guards the wall between the Goliath and Mutation macros.

Every check here exists because a leak in that wall makes the macro click a
coordinate the user never calibrated for the screen that is actually open.
"""

import copy
import json
import os
import tempfile

import profile_manager
from profile_manager import (GOLIATH_ONLY_KEYS, MUTATION_ONLY_KEYS,
                             ProfileManager, profile_mode, sanitize_profile)

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


def run():
    # 1. Key ownership is exclusive - nothing belongs to both macros.
    overlap = GOLIATH_ONLY_KEYS & MUTATION_ONLY_KEYS
    check("key sets are disjoint", not overlap, overlap)

    # 2. Foreign keys are stripped in both directions.
    goliath = {"meta": {"macro_mode": "goliath"}, "slots": [1],
               "targets": {"mutate": {"x": 1, "y": 2}}, "scan_region": {}}
    _, removed = sanitize_profile(goliath)
    check("mutation keys stripped from goliath",
          "targets" not in goliath and "scan_region" not in goliath, goliath)
    check("goliath keeps its own keys", goliath["slots"] == [1])
    check("removal is reported", set(removed) == {"targets", "scan_region"}, removed)

    mutation = {"meta": {"macro_mode": "mutation"},
                "targets": {"mutate": {"x": 1, "y": 2}},
                "slots": [1], "common_actions": {"confirm_yes": {}},
                "claim_schedule": {"enabled": True}}
    sanitize_profile(mutation)
    check("goliath keys stripped from mutation",
          not {"slots", "common_actions", "claim_schedule"} & set(mutation), mutation)
    check("mutation keeps its own keys", "targets" in mutation)

    # 3. Mode comes from metadata, never from the profile name.
    check("mode read from meta", profile_mode({"meta": {"macro_mode": "mutation"}}) == "mutation")
    check("missing meta defaults to goliath", profile_mode({}) == "goliath")
    check("junk meta defaults to goliath",
          profile_mode({"meta": {"macro_mode": "banana"}}) == "goliath")

    # 4. Shipped profiles are clean.
    pm = ProfileManager()
    for name in pm.list_profiles():
        cfg = pm.load_profile(name)
        mode = profile_mode(cfg)
        foreign = MUTATION_ONLY_KEYS if mode == "goliath" else GOLIATH_ONLY_KEYS
        leaked = sorted(foreign & set(cfg))
        check(f"profile '{name}' ({mode}) has no foreign keys", not leaked, leaked)

    # 4b. Every shipped coordinate must sit inside the reference client area.
    #     A point outside it can only be a typo or a leftover test value - and
    #     it clicks the edge of the window instead of the button.
    for name in pm.list_profiles():
        cfg = pm.load_profile(name)
        ref = cfg.get("reference_client", {})
        rw, rh = ref.get("width", 800), ref.get("height", 600)
        bad = []

        def bounds_check(label, pt):
            if not isinstance(pt, dict) or "x" not in pt:
                return
            if not (0 <= pt["x"] <= rw and 0 <= pt["y"] <= rh):
                bad.append(f"{label}={pt['x']},{pt['y']}")

        for slot in cfg.get("slots", []):
            for key in ("slot_click", "claim_click"):
                bounds_check(f"{slot.get('name', slot.get('id'))}.{key}", slot.get(key))
        for action, pt in (cfg.get("common_actions") or {}).items():
            bounds_check(action, pt)
        for target, pt in (cfg.get("targets") or {}).items():
            bounds_check(target, pt)
        check(f"profile '{name}' coords fit in {rw}x{rh}", not bad, bad)

    # 5. The default mutation profile carries only mutation data.
    mut_default = pm._get_default_mutation_data()
    check("default mutation profile is tagged",
          mut_default["meta"]["macro_mode"] == "mutation")
    check("default mutation profile has targets", "targets" in mut_default)
    check("default mutation profile has no slots",
          not GOLIATH_ONLY_KEYS & set(mut_default), sorted(GOLIATH_ONLY_KEYS & set(mut_default)))

    # 6. save_profile sanitises even when the caller does not.
    tmp_dir = tempfile.mkdtemp()
    real_dir = profile_manager.PROFILES_DIR
    profile_manager.PROFILES_DIR = tmp_dir
    try:
        dirty = {"meta": {"macro_mode": "mutation"},
                 "targets": {"pet_select": {"x": 1, "y": 1}},
                 "slots": [{"claim_click": {"x": 9, "y": 9}}]}
        pm.save_profile("Dirty", dirty)
        with open(os.path.join(tmp_dir, "Dirty.json"), encoding="utf-8") as f:
            on_disk = json.load(f)
        check("save_profile strips foreign keys", "slots" not in on_disk, on_disk)
    finally:
        profile_manager.PROFILES_DIR = real_dir

    # 7. MutationRunner refuses a Goliath profile instead of silently swapping
    #    in config_mutation.json's coordinates.
    from mutation_runner import MutationRunner
    try:
        MutationRunner(config_data={"meta": {"macro_mode": "goliath"},
                                    "slots": [], "common_actions": {}})
        check("mutation runner refuses goliath config", False, "no error raised")
    except ValueError as e:
        check("mutation runner refuses goliath config", "Goliath" in str(e), e)
    except Exception as e:
        check("mutation runner refuses goliath config", False, repr(e))

    # 8. Neither runner module reaches into the other's config keys.
    import importlib
    # The real module's source: the top-level names become aliases after the move.
    mut_src = open(importlib.import_module("mutation_runner").__file__, encoding="utf-8").read()
    for key in ("claim_click", "confirm_yes", "claim_schedule", "ClaimScheduler"):
        check(f"mutation_runner free of '{key}'", key not in mut_src)

    main_src = open(importlib.import_module("main").__file__, encoding="utf-8").read()
    for key in ('"targets"', "config_mutation", "MutationRunner("):
        check(f"MacroRunner free of '{key}'", key not in main_src.split("if __name__")[0])

    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())


def test_legacy_checks():
    """Run every check in this script under pytest; a recorded failure fails the test."""
    assert run() == 0, failures
