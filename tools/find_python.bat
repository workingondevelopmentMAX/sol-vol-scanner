@echo off
rem Sets PY to a working Python 3.11+ command, or exits with code 1.
set PY=
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1 && set "PY=py -3" && exit /b 0
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1 && set "PY=python" && exit /b 0
exit /b 1
