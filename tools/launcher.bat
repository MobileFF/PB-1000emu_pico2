@echo off
rem PB-1000 Tools Launcher - Windows
rem Double-click this file, or run it from a command prompt.

cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 launcher.py
    goto :done
)

where python >nul 2>nul
if %errorlevel%==0 (
    python launcher.py
    goto :done
)

echo Python was not found on PATH. Install Python 3 from https://www.python.org/
pause
goto :eof

:done
if errorlevel 1 pause
