#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

if command -v python3 >/dev/null 2>&1; then
  PYEXE=python3
elif command -v python >/dev/null 2>&1; then
  PYEXE=python
else
  echo "[OnlyBridge] Neither 'python3' nor 'python' found in PATH."
  echo "Install Python 3.11+ from https://www.python.org/downloads/ and re-run."
  exit 1
fi

NEEDS_INSTALL=0
for pkg in fastapi uvicorn aiohttp psutil pydantic tiktoken; do
  if ! "$PYEXE" -m pip show "$pkg" >/dev/null 2>&1; then
    NEEDS_INSTALL=1
    break
  fi
done

if [ "$NEEDS_INSTALL" = "1" ]; then
  echo "[OnlyBridge] Installing Python dependencies from requirements.txt..."
  if ! "$PYEXE" -m pip install --disable-pip-version-check -r requirements.txt; then
    echo "[OnlyBridge] pip install failed. See messages above."
    exit 1
  fi
fi

if [ ! -f "frontend/dist/index.html" ]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "[OnlyBridge] frontend/dist not found and npm is not in PATH - the dashboard UI will not load."
    echo "Install Node.js from https://nodejs.org/ then re-run, or run 'cd frontend && npm install && npm run build' manually."
  else
    echo "[OnlyBridge] Building frontend (first run, may take ~30s)..."
    (cd frontend && npm install --silent && npm run build) || {
      echo "[OnlyBridge] frontend build failed. See messages above."
      exit 1
    }
  fi
fi

(sleep 1 && (xdg-open http://localhost:8800 >/dev/null 2>&1 || open http://localhost:8800 >/dev/null 2>&1)) &
exec "$PYEXE" -m uvicorn backend.app:app --host 127.0.0.1 --port 8800
