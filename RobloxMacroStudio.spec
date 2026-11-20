# -*- mode: python ; coding: utf-8 -*-
# One-file build of the studio. Paths are relative to this spec (SPECPATH), so the build
# works on any machine. Entry is the package's __main__: the studio, or a calibrator when
# relaunched with --calibrate (macro_studio/calibrators/launch.py).
#
# Never add per-machine files here (the settings file, schedules, logs): see
# build_exe.PRIVATE_FILES and FR-053.
import os

ROOT = SPECPATH

a = Analysis(
    [os.path.join(ROOT, 'macro_studio', '__main__.py')],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, 'templates'), 'templates'),
        (os.path.join(ROOT, 'profiles'), 'profiles'),
        (os.path.join(ROOT, 'config.json'), '.'),
        (os.path.join(ROOT, 'config_mutation.json'), '.'),
    ],
    hiddenimports=[
        'win32gui', 'win32con', 'win32api', 'win32process', 'win32crypt', 'win32event',
        'PIL', 'PIL.Image', 'PIL.ImageTk', 'PIL.ImageDraw',
        'cv2', 'numpy', 'psutil',
        # Loaded lazily (registry factories, --calibrate dispatch).
        'macro_studio.tools.macro.tool',
        'macro_studio.tools.clients.tool',
        'macro_studio.tools.memory.tool',
        'macro_studio.tools.layout.tool',
        'macro_studio.tools.afk.tool',
        'macro_studio.tools.notify.tool',
        'macro_studio.tools.system.tool',
        'macro_studio.tools.logs.tool',
        'macro_studio.tools.recorder.tool',
        'macro_studio.core.popup_guard',
        'macro_studio.core.popup_capture',
        'macro_studio.calibrators.goliath',
        'macro_studio.calibrators.mutation',
        'winreg',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='RobloxMacroStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
