@echo off
cd /d "%~dp0"
echo Refreshing Research Daily...
python fetch.py
if errorlevel 1 (
  echo.
  echo Fetch failed. Check the messages above.
  pause
  exit /b 1
)
start "" "%~dp0index.html"
