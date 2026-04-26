from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

try:
    import tiktoken  # type: ignore
    _HAS_TIKTOKEN = True
except Exception:
    tiktoken = None  # type: ignore
    _HAS_TIKTOKEN = False

_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "onlybridge.db"
_lock = threading.Lock()
_inited = False
_enc = None


def has_tiktoken() -> bool:
    return _HAS_TIKTOKEN


def _get_enc():
    global _enc
    if not _HAS_TIKTOKEN:
        return None
    if _enc is None:
        try:
            _enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            return None
    return _enc


def count_tokens(text: str) -> int:
    if not text:
        return 0
    enc = _get_enc()
    if enc is None:
        return max(1, len(text) // 4)
    try:
        return len(enc.encode(text, disallowed_special=()))
    except Exception:
        return max(1, len(text) // 4)


def _ensure_db() -> None:
    global _inited
    if _inited:
        return
    with _lock:
        if _inited:
            return
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(_DB_PATH, timeout=5.0)
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    model TEXT,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER,
                    status TEXT NOT NULL DEFAULT 'ok',
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts);
                CREATE INDEX IF NOT EXISTS idx_requests_source_ts ON requests(source, ts);
                """
            )
            c.commit()
        finally:
            c.close()
        _inited = True


def log_request(
    *,
    source: str,
    model: Optional[str],
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: Optional[int] = None,
    status: str = "ok",
    error: Optional[str] = None,
) -> None:
    try:
        _ensure_db()
        c = sqlite3.connect(_DB_PATH, timeout=5.0)
        try:
            c.execute("PRAGMA busy_timeout=2000")
            c.execute(
                "INSERT INTO requests (ts, source, model, prompt_tokens, completion_tokens, latency_ms, status, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (int(time.time()), source, model, int(prompt_tokens), int(completion_tokens), latency_ms, status, error),
            )
            c.commit()
        finally:
            c.close()
    except Exception as e:
        import sys
        print(f"[stats_db] log_request failed: {e}", file=sys.stderr, flush=True)


def tokens_from_messages(messages) -> int:
    if not messages:
        return 0
    total = 0
    for m in messages:
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, str):
            total += count_tokens(c)
        elif isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    if b.get("type") == "text":
                        total += count_tokens(str(b.get("text") or ""))
                    elif b.get("type") == "tool_result":
                        inner = b.get("content")
                        if isinstance(inner, str):
                            total += count_tokens(inner)
        sys = m.get("system")
        if isinstance(sys, str):
            total += count_tokens(sys)
    return total
