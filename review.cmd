@echo off
setlocal
set "PYTHONPATH=%~dp0src"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Project Python environment not found. See README.md.
  exit /b 1
)
if /I "%~1"=="stop" goto stop
"%~dp0.venv\Scripts\python.exe" -X utf8 -m medication_video_annotation --data-root "%~dp0local" review --port 8768 %*
exit /b %errorlevel%
:stop
"%~dp0.venv\Scripts\python.exe" -X utf8 -m medication_video_annotation --data-root "%~dp0local" stop --port 8768 %2 %3 %4 %5 %6 %7 %8 %9
exit /b %errorlevel%
