@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

where py >nul 2>&1
if errorlevel 1 (
  echo [OnlyBridge] Python launcher "py" not found in PATH.
  echo Install Python 3.11+ from https://www.python.org/downloads/ and re-run.
  pause
  exit /b 1
)

set NEEDS_INSTALL=0
for %%P in (fastapi uvicorn aiohttp psutil pydantic tiktoken) do (
  py -m pip show %%P >nul 2>&1
  if errorlevel 1 set NEEDS_INSTALL=1
)

if "%NEEDS_INSTALL%"=="1" (
  echo [OnlyBridge] Installing Python dependencies from requirements.txt...
  py -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo [OnlyBridge] pip install failed. See messages above.
    pause
    exit /b 1
  )
)

if not exist "frontend\dist\index.html" (
  echo [OnlyBridge] frontend\dist not found - the dashboard UI will not load.
  echo Run "cd frontend ^&^& npm install ^&^& npm run build" once, or pull a release that ships dist/.
)

start "" http://localhost:8800
py -m uvicorn backend.app:app --host 127.0.0.1 --port 8800
pause
