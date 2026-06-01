from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from backend import config as cfg


_SCHEMA = """
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


class DataDirNotWritable(RuntimeError):
    """data/ exists but the current user cannot write to it (chmod / chown issue)."""


def _ensure_data_dir_writable() -> None:
    d = cfg.DATA_DIR
    try:
        d.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        raise DataDirNotWritable(
            f"Cannot create {d}: {e}. "
            f"Most likely the parent directory is owned by another user (e.g. cloned via sudo). "
            f"Fix: sudo chown -R $USER:$USER {d.parent}"
        ) from e
    probe = d / ".write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        raise DataDirNotWritable(
            f"Cannot write to {d}: {e}. "
            f"The folder exists but the current user has no write permission. "
            f"Fix on Linux/macOS: chmod -R u+w {d}  (or  sudo chown -R $USER:$USER {d.parent} if it belongs to root)"
        ) from e


def init_db() -> None:
    _ensure_data_dir_writable()
    try:
        with _conn() as c:
            c.executescript(_SCHEMA)
            c.commit()
    except sqlite3.OperationalError as e:
        raise DataDirNotWritable(
            f"sqlite3 cannot open {cfg.DATABASE_PATH}: {e}. "
            f"This usually means the data/ folder or the .db file is read-only or owned by another user. "
            f"Fix: chmod -R u+w {cfg.DATA_DIR}  (or  sudo chown -R $USER:$USER {cfg.DATA_DIR.parent})"
        ) from e


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(cfg.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def insert_request(
    *,
    source: str,
    model: Optional[str],
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: Optional[int] = None,
    status: str = "ok",
    error: Optional[str] = None,
    ts: Optional[int] = None,
) -> int:
    ts = ts if ts is not None else int(time.time())
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO requests
                (ts, source, model, prompt_tokens, completion_tokens, latency_ms, status, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts, source, model, prompt_tokens, completion_tokens, latency_ms, status, error),
        )
        c.commit()
        return cur.lastrowid


def stats_total(since_ts: Optional[int] = None) -> dict[str, Any]:
    where = ""
    params: tuple = ()
    if since_ts is not None:
        where = "WHERE ts >= ?"
        params = (since_ts,)
    with _conn() as c:
        row = c.execute(
            f"""SELECT COUNT(*) AS n,
                       COALESCE(SUM(prompt_tokens), 0) AS pt,
                       COALESCE(SUM(completion_tokens), 0) AS ct,
                       COALESCE(AVG(latency_ms), 0) AS lat,
                       SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS ok
                FROM requests {where}""",
            params,
        ).fetchone()
    n = row["n"] or 0
    return {
        "requests": n,
        "prompt_tokens": row["pt"] or 0,
        "completion_tokens": row["ct"] or 0,
        "total_tokens": (row["pt"] or 0) + (row["ct"] or 0),
        "avg_latency_ms": int(row["lat"] or 0),
        "success_rate": ((row["ok"] or 0) / n * 100.0) if n else 100.0,
    }


def stats_by_source(since_ts: Optional[int] = None) -> list[dict[str, Any]]:
    where = ""
    params: tuple = ()
    if since_ts is not None:
        where = "WHERE ts >= ?"
        params = (since_ts,)
    with _conn() as c:
        rows = c.execute(
            f"""SELECT source,
                       COUNT(*) AS n,
                       COALESCE(SUM(prompt_tokens), 0) AS pt,
                       COALESCE(SUM(completion_tokens), 0) AS ct
                FROM requests {where}
                GROUP BY source
                ORDER BY n DESC""",
            params,
        ).fetchall()
    return [
        {
            "source": r["source"],
            "requests": r["n"],
            "prompt_tokens": r["pt"],
            "completion_tokens": r["ct"],
        }
        for r in rows
    ]


def stats_by_model(since_ts: Optional[int] = None) -> list[dict[str, Any]]:
    where = "WHERE model IS NOT NULL"
    params: tuple = ()
    if since_ts is not None:
        where += " AND ts >= ?"
        params = (since_ts,)
    with _conn() as c:
        rows = c.execute(
            f"""SELECT model,
                       COUNT(*) AS n,
                       COALESCE(SUM(prompt_tokens), 0) AS pt,
                       COALESCE(SUM(completion_tokens), 0) AS ct
                FROM requests {where}
                GROUP BY model
                ORDER BY n DESC""",
            params,
        ).fetchall()
    return [
        {
            "model": r["model"],
            "requests": r["n"],
            "prompt_tokens": r["pt"],
            "completion_tokens": r["ct"],
        }
        for r in rows
    ]


def stats_timeseries(days: int = 14) -> list[dict[str, Any]]:
    since = int(time.time()) - days * 86400
    with _conn() as c:
        rows = c.execute(
            """SELECT strftime('%Y-%m-%d', ts, 'unixepoch') AS date,
                      COUNT(*) AS n,
                      COALESCE(SUM(prompt_tokens), 0) AS pt,
                      COALESCE(SUM(completion_tokens), 0) AS ct
               FROM requests
               WHERE ts >= ?
               GROUP BY date
               ORDER BY date ASC""",
            (since,),
        ).fetchall()
    return [
        {
            "date": r["date"],
            "requests": r["n"],
            "prompt_tokens": r["pt"],
            "completion_tokens": r["ct"],
        }
        for r in rows
    ]
