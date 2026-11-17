@echo off
rem Run the SOL Vol Scanner now, show the summary, then open the dashboard.
rem Extra options can be passed through, e.g.  run_scan.bat --sample   or   run_scan.bat --backfill
cd /d "%~dp0"
call tools\find_python.bat || goto :nopython
%PY% main.py %*
if "%~1"=="--json" goto :end
start "" "%~dp0site\index.html"
:end
echo.
pause
exit /b

:nopython
echo Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH" during setup), then run this again.
pause
