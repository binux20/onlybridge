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

if exist "frontend\dist\index.html" goto :run

where npm >nul 2>&1
if errorlevel 1 goto :no_npm

echo [OnlyBridge] Building frontend (first run, may take ~30s)...
pushd frontend
call npm install --silent
if errorlevel 1 goto :npm_fail
call npm run build
if errorlevel 1 goto :npm_fail
popd
goto :run

:no_npm
echo [OnlyBridge] frontend\dist not found and npm is not in PATH - the dashboard UI will not load.
echo Install Node.js from https://nodejs.org/ then re-run, or build the frontend manually.
goto :run

:npm_fail
echo [OnlyBridge] frontend build failed. See messages above.
popd
pause
exit /b 1

:run
start "" http://localhost:8800
%PYEXE% -m uvicorn backend.app:app --host 127.0.0.1 --port 8800
pause
