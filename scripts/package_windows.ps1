# Build a standalone "PS3 Recomp Studio" folder with PyInstaller.
#   powershell -ExecutionPolicy Bypass -File scripts\package_windows.ps1
# The packaged app still runs the ps3recomp tools with a Python on PATH and
# builds with CMake + Visual Studio, so those must be installed on the PC.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py -m pip install -r requirements-studio.txt pyinstaller
& $py -m PyInstaller --noconfirm --windowed --name "PS3 Recomp Studio" `
    --collect-data qfluentwidgets --workpath pyinstaller --distpath dist `
    --paths . scripts\studio_entry.py
Write-Host "Done: dist\PS3 Recomp Studio\PS3 Recomp Studio.exe (keep it inside this folder: it finds the repository by searching upward)"
