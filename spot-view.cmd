@echo off
setlocal
set "PYTHONPATH=%~dp0src"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Project Python environment not found. See README.md.
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -X utf8 -m medication_video_annotation --data-root "%~dp0local" spot-view %*
exit /b %errorlevel%
