@echo off
rem Creates a Windows scheduled task that runs the scanner every day at 9:00 am.
rem If the laptop is off or asleep at 9:00, it runs as soon as it is next on.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install_daily_task.ps1"
pause
