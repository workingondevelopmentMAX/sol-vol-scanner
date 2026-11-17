@echo off
rem Lets your phone open the dashboard over your home Wi-Fi while this window stays open.
cd /d "%~dp0"
call tools\find_python.bat || (echo Python not found. & pause & exit /b 1)
echo.
echo On your phone (same Wi-Fi), open one of these addresses:
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /R /C:"IPv4"') do (
  for /f "tokens=* delims= " %%b in ("%%a") do echo     http://%%b:8765/
)
echo.
echo If Windows asks about the firewall, allow access on PRIVATE networks only.
echo Close this window to stop sharing.
echo.
%PY% -m http.server 8765 --bind 0.0.0.0 --directory site
