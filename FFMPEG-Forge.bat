@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "VENV=%~dp0.venv"

if exist "%VENV%\Scripts\pythonw.exe" goto launch

echo.
echo   Setting up FFMPEG-Forge. This happens once and takes a minute or two.
echo.

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY goto nopython

%PY% -m venv "%VENV%"
if errorlevel 1 goto nopython

"%VENV%\Scripts\python.exe" -m pip install --upgrade pip --quiet
"%VENV%\Scripts\python.exe" -m pip install --quiet PySide6-Essentials
if errorlevel 1 goto pipfail

echo   Setup finished.

:launch
start "" "%VENV%\Scripts\pythonw.exe" "%~dp0ffmpeg_forge.py"
exit /b 0

:nopython
echo.
echo   Python was not found on this PC.
echo   Install it from https://www.python.org/downloads/ or run:
echo       winget install Python.Python.3.12
echo   Tick "Add python.exe to PATH" during install, then run this file again.
echo.
pause
exit /b 1

:pipfail
echo.
echo   Could not install PySide6. Check your internet connection and retry.
echo.
pause
exit /b 1
