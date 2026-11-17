@echo off
rem Change max loss per trade and across all spreads, then rescan.
cd /d "%~dp0"
call tools\find_python.bat || (echo Python not found. & pause & exit /b 1)
%PY% set_limits.py
pause
