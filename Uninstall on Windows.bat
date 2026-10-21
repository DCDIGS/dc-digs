@echo off
rem Double-click to stop DC Digs and remove it from start-up.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install\windows\uninstall.ps1"
pause
