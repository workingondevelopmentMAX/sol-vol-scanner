@echo off
cd /d "%~dp0"
call tools\find_python.bat || (echo Python not found. & pause & exit /b 1)
%PY% -m unittest discover tests -v
%PY% main.py --sample
pause
