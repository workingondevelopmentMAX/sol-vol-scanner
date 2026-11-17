@echo off
rem Used by the daily scheduled task: no window prompts, output saved to logs\last_run.txt
cd /d "%~dp0"
if not exist logs mkdir logs
call tools\find_python.bat || (echo Python not found > logs\last_run.txt & exit /b 1)
%PY% main.py > logs\last_run.txt 2>&1
