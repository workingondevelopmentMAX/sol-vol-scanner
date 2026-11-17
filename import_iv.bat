@echo off
rem Drag a CSV of daily SOL implied vol (columns: date, atm_iv) onto this file to import it.
cd /d "%~dp0"
call tools\find_python.bat || (echo Python not found. & pause & exit /b 1)
if "%~1"=="" (%PY% import_iv.py) else (%PY% import_iv.py "%~1")
pause
