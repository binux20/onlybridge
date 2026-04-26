@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

set PYEXE=
where py >nul 2>&1
if not errorlevel 1 (
  set PYEXE=py
) else (
  where python >nul 2>&1
  if not errorlevel 1 set PYEXE=python
)

if "%PYEXE%"=="" (
  echo [OnlyBridge] Neither "py" nor "python" found in PATH.
  echo Install Python 3.11+ from https://www.python.org/downloads/ and re-run.
  pause
  exit /b 1
)

set NEEDS_INSTALL=0
for %%P in (fastapi uvicorn aiohttp psutil pydantic tiktoken) do (
  %PYEXE% -m pip show %%P >nul 2>&1
  if errorlevel 1 set NEEDS_INSTALL=1
)

if "%NEEDS_INSTALL%"=="1" (
  echo [OnlyBridge] Installing Python dependencies from requirements.txt...
  %PYEXE% -m pip install --disable-pip-version-check -r requirements.txt
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
%PYEXE% -m uvicorn backend.app:app --host 127.0.0.1 --port 8800
pause
