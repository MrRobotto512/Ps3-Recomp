@echo off
rem PS3 Recomp Studio launcher: sets up a local Python environment on first run.
setlocal
cd /d "%~dp0"

if not exist "ps3recomp\tools\ppu_lifter.py" (
    echo Fetching the ps3recomp toolkit...
    git submodule update --init || goto :fail
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating a Python environment in .venv ...
    py -3 -m venv .venv 2>nul || python -m venv .venv || goto :nopython
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements-studio.txt || goto :fail
)

start "" ".venv\Scripts\pythonw.exe" -m studio
exit /b 0

:nopython
echo Python 3.10 or newer is required: https://www.python.org/downloads/
pause
exit /b 1

:fail
echo Setup failed; see the messages above.
pause
exit /b 1
