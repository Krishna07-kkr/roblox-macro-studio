import os
import sys
import shutil
import zipfile
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(BASE_DIR, "dist")
BUILD_DIR = os.path.join(BASE_DIR, "build")
RELEASE_DIR = os.path.join(DIST_DIR, "RobloxMacroStudio_Release")
from macro_studio import __version__  # noqa: E402

ZIP_PATH = os.path.join(DIST_DIR, f"RobloxMacroStudio_v{__version__}.zip")

README_TEXT = """========================================================================
ROBLOX MACRO STUDIO (STANDALONE V{version})
ARM WRESTLE SIMULATOR - GOLIATH DAYCARE & PET MUTATION
========================================================================

ZERO CONFIGURATION NEEDED (Bina kisi setup ya calibration ke chalta hai):
- All 1080p sub-pixel calibrated coordinates are already bundled!
- Windows are automatically snapped to 800x600 minimum size.
- 100% Standalone: Python ya koi library install karne ki zaroorat nahi hai.

------------------------------------------------------------------------
HOW TO RUN (Kaise chalayein):
------------------------------------------------------------------------
1. Open Roblox and enter Arm Wrestle Simulator.
2. Open either:
   - Goliath Daycare (3-Slots) window on screen, OR
   - Pet Mutation window on screen.
3. Double-click "RobloxMacroStudio.exe".
4. Select Mode in top-right if needed:
   - "Goliath 3-Slot Daycare" (Default)
   - "Pet Mutation (Multi-Account Loop)"
5. Click "Start Macro" button!

------------------------------------------------------------------------
HOTKEYS (Controls):
------------------------------------------------------------------------
- Emergency Stop: [F6] or [ESC] or Red On-Screen Floating Button
- Pause / Resume: [F7]

------------------------------------------------------------------------
WHAT IS IN THIS FOLDER:
------------------------------------------------------------------------
- RobloxMacroStudio.exe  : The main application (Single Executable).
- config.json            : Goliath Daycare coordinates & window settings.
- config_mutation.json   : Pet Mutation calibrated coordinates.
- profiles/              : Saved machine profiles (Goliath, Mutation).

Enjoy auto-farming!
========================================================================
"""

# Per-machine files that must never ship: studio_settings.json holds the DPAPI-encrypted
# Discord webhook (FR-053), and the others are one user's runtime state.
PRIVATE_FILES = ("studio_settings.json", "account_schedule.json", "debug_log.txt")


def assert_no_private_files(release_dir):
    """Stop the build if a private file ended up anywhere in the release folder."""
    found = [os.path.join(root, f) for root, _, files in os.walk(release_dir)
             for f in files if f in PRIVATE_FILES]
    if found:
        print("[!] Private files must not ship in a release:")
        for path in found:
            print("    " + path)
        sys.exit(1)


def clean_previous():
    print("[*] Cleaning previous build artifacts...")
    try:
        subprocess.run(["taskkill", "/F", "/IM", "RobloxMacroStudio.exe"], capture_output=True)
    except Exception:
        pass
    exe_target = os.path.join(DIST_DIR, "RobloxMacroStudio.exe")
    for p in [RELEASE_DIR, ZIP_PATH, exe_target]:
        if os.path.isfile(p):
            try:
                os.remove(p)
            except Exception:
                pass
        elif os.path.isdir(p):
            try:
                shutil.rmtree(p)
            except Exception:
                pass

def run_pyinstaller():
    print("[*] Running PyInstaller build with optimized spec...")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        os.path.join(BASE_DIR, "RobloxMacroStudio.spec")
    ]
    res = subprocess.run(cmd, cwd=BASE_DIR)
    if res.returncode != 0:
        print("[!] PyInstaller failed with exit code:", res.returncode)
        sys.exit(res.returncode)
    print("[+] PyInstaller completed successfully!")

def package_release():
    exe_path = os.path.join(DIST_DIR, "RobloxMacroStudio.exe")
    if not os.path.isfile(exe_path):
        print(f"[!] Target exe not found at: {exe_path}")
        sys.exit(1)

    print(f"[+] Executable created: {exe_path} ({os.path.getsize(exe_path) / (1024*1024):.1f} MB)")
    print("[*] Creating release folder...")
    os.makedirs(RELEASE_DIR, exist_ok=True)

    shutil.copy2(exe_path, os.path.join(RELEASE_DIR, "RobloxMacroStudio.exe"))
    shutil.copy2(os.path.join(BASE_DIR, "config.json"), os.path.join(RELEASE_DIR, "config.json"))
    shutil.copy2(os.path.join(BASE_DIR, "config_mutation.json"), os.path.join(RELEASE_DIR, "config_mutation.json"))
    ahk_file = os.path.join(BASE_DIR, "RobloxPetMacro.ahk")
    if os.path.isfile(ahk_file):
        shutil.copy2(ahk_file, os.path.join(RELEASE_DIR, "RobloxPetMacro.ahk"))
    
    release_profiles = os.path.join(RELEASE_DIR, "profiles")
    os.makedirs(release_profiles, exist_ok=True)
    for f in os.listdir(os.path.join(BASE_DIR, "profiles")):
        if f.endswith(".json"):
            shutil.copy2(os.path.join(BASE_DIR, "profiles", f), os.path.join(release_profiles, f))

    with open(os.path.join(RELEASE_DIR, "README.txt"), "w", encoding="utf-8") as f:
        f.write(README_TEXT.replace("{version}", __version__))

    assert_no_private_files(RELEASE_DIR)

    print(f"[*] Compressing into ZIP: {ZIP_PATH}...")
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(RELEASE_DIR):
            for file in files:
                abs_f = os.path.join(root, file)
                rel_f = os.path.relpath(abs_f, RELEASE_DIR)
                zf.write(abs_f, rel_f)

    print(f"[SUCCESS] Release ZIP created: {ZIP_PATH} ({os.path.getsize(ZIP_PATH) / (1024*1024):.1f} MB)")

if __name__ == "__main__":
    clean_previous()
    run_pyinstaller()
    package_release()
