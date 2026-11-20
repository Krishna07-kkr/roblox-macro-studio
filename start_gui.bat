@echo off
title AWS Vision Macro Studio
cd /d "%~dp0"
python -m macro_studio
if errorlevel 1 (
    echo.
    echo [ERROR] Application closed with an error.
    pause
)
