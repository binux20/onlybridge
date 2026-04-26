@echo off
setlocal
cd /d %~dp0

set PYEXE=
where py >nul 2>nul
if not errorlevel 1 (
    set PYEXE=py
) else (
    where python >nul 2>nul
    if not errorlevel 1 (
        set PYEXE=python
    )
)

if "%PYEXE%"=="" (
    echo [ERROR] Neither 'py' nor 'python' found in PATH. Install Python from https://www.python.org/
    pause
    exit /b 1
)

if not exist .venv (
    %PYEXE% -m venv .venv
)

call .venv\Scripts\activate.bat

%PYEXE% -m pip install --quiet --upgrade pip
%PYEXE% -m pip install --quiet -r requirements.txt

if not exist frontend\dist (
    where npm >nul 2>nul
    if errorlevel 1 (
        echo [WARN] npm not found - skipping frontend build. Install Node.js from https://nodejs.org/
    ) else (
        pushd frontend
        npm install --silent
        npm run build
        popd
    )
)

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

start "" http://localhost:8800
%PYEXE% -m uvicorn backend.app:app --host 127.0.0.1 --port 8800
