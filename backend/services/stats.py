from __future__ import annotations

import time
from typing import Any, Optional

from backend import db

_DAY = 86400


def _since_for(period: str) -> Optional[int]:
    now = int(time.time())
    if period == "today":
        return now - _DAY
    if period == "week":
        return now - 7 * _DAY
    if period == "all":
        return None
    return None


def summary(period: str = "all") -> dict[str, Any]:
    since = _since_for(period)
    return {
        "period": period,
        "totals": db.stats_total(since_ts=since),
        "by_source": db.stats_by_source(since_ts=since),
        "by_model": db.stats_by_model(since_ts=since),
    }


def timeseries(days: int = 14) -> list[dict[str, Any]]:
    return db.stats_timeseries(days=days)
