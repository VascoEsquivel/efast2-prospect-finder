@echo off
rem Windows: double-click this file. Sets everything up on the first run, then opens the app.
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (py -3 start.py %*) else (python start.py %*)
if errorlevel 1 (
  echo.
  echo If you see an error above: Python 3.11 or newer is required, from https://www.python.org/downloads/
  echo When installing Python on Windows, tick "Add python.exe to PATH".
)
pause
