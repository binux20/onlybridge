#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1
(sleep 1 && xdg-open http://localhost:8800 2>/dev/null || open http://localhost:8800 2>/dev/null) &
exec python -m uvicorn backend.app:app --host 127.0.0.1 --port 8800
