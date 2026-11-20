"""Roblox Macro Studio: macros and multi-account tools for Roblox on Windows.

Layout (see specs/001-modular-multitool/plan.md):
    core/         window, input, vision, scaling, paths - shared mechanics
    services/     hotkeys, scheduler, automation lock, events - shared runtime
    runners/      the macro loops (Goliath, Mutation) on a shared RunnerBase
    calibrators/  coordinate pickers, each runnable as its own process
    tools/        one folder per feature; registered in registry.py
"""

__version__ = "2.6"
