from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from backend.services import stats as stats_service

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("")
async def summary(period: str = Query("all", pattern="^(today|week|all)$")) -> dict[str, Any]:
    return stats_service.summary(period)


@router.get("/timeseries")
async def timeseries(days: int = Query(14, ge=1, le=90)) -> list[dict[str, Any]]:
    return stats_service.timeseries(days)
