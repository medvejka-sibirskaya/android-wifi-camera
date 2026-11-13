@echo off
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
if exist "work\runtime\Scripts\python.exe" (
  "work\runtime\Scripts\python.exe" -B desktop.py
) else (
  py -3 -B desktop.py
)
pause
