# Roblox Macro Studio

A Windows desktop app that runs two vision-based macros for Arm Wrestle Simulator across several
Roblox clients at once:

- **Goliath daycare**: claims finished pets and refills the three slots, with a 10-minute claim
  timer per client.
- **Pet mutation**: re-rolls a pet and watches the spin wheel.

The studio shows a live view of the client being worked, with the scan regions and click points
drawn on it, and a Tabs picker to choose which clients the macro works on.

## Run it

From source (Python 3.11 or 3.12):

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements-dev.txt
.venv\Scripts\python.exe -m macro_studio
```

`start_gui.bat` does the same with the Python on your PATH. The standalone build needs no Python:
run `RobloxMacroStudio.exe` from `dist\`.

Command-line runners (no window): `python main.py` (Goliath) and `python mutation_runner.py`.
Calibrators open from the studio's Settings page, or with `python calibrate.py` and
`python calibrate_mutation.py`.

## Keys

| Key | Action |
|---|---|
| F6 or Esc | Stop everything now (works from any window) |
| F7 | Pause / resume |
| F8 | End a recording (Recorder page) |

The macro clicks only at points you calibrated. It never guesses a position near a pop-up,
because a wrong click could confirm a Robux purchase.

## Where your data lives

Beside the exe, or in the project folder when run from source:

| File | What |
|---|---|
| `profiles\*.json` | One profile per macro setup (Goliath, Mutation, your own) |
| `config.json`, `config_mutation.json` | Calibrated points written by the calibrators |
| `account_schedule.json` | Claim timers per client |
| `studio_settings.json` | Per-machine settings (Discord webhook is stored encrypted) |
| `debug_log.txt`, `proof_captures\` | Logs and before/after screenshots |

Tests never touch these: they run on a temporary copy (see below).

## Develop

```powershell
scripts\check.ps1              # ruff, pyright, bandit, pytest; exits non-zero on any failure
scripts\check.ps1 -SkipTests   # static checks only
.venv\Scripts\python.exe -m pytest -q tests\unit
.venv\Scripts\python.exe build_exe.py   # one-file exe and release zip in dist\
```

- Tests that open a window run in their own process (`tests/conftest.py: run_in_fresh_python`)
  and use the `tmp_base_dir` fixture, which sets `MACRO_STUDIO_DATA_DIR` so profiles and
  configs are copied to a temp folder. A session check fails the run if the real files change.
- Fakes for the window manager, input, vision and network are in `tests/conftest.py`.

## Layout

```text
macro_studio/
  app.py               shell: window, navigation, scheduler, hotkeys
  registry.py          the list of tools (add a tool = one module + one line)
  core/                window manager, input, vision, live feed, theme, paths
  services/            hotkeys, scheduler, automation lock, events
  runners/             Goliath and Mutation runners, claim scheduler
  calibrators/         point calibration windows
  tools/macro/         the macro tool's pages
  tools/recorder/      record your actions in Roblox, save them, replay them (plus the island pill)
specs/                 Spec Kit specs: 001 (multi-tool port), 002 (audit hardening), 003 (recorder)
tests/                 unit, integration, legacy (script-style checks run through pytest)
```

Plans: `specs/001-modular-multitool` ports the features of the old AutoHotkey multi-tool
(client dashboard, RAM trim, layouts, anti-AFK, Discord alerts, tray). `specs/002-audit-hardening`
fixes the leaks, stalls and safety gaps found in the audit and comes first.

## Account risk

Running several clients at once and automating clicks can break Roblox's Terms of Use and may
lead to account action. You use this at your own risk.
