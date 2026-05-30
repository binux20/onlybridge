from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from backend.services import models as models_service

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
async def list_models(refresh: bool = Query(False)) -> dict[str, Any]:
    result = await models_service.list_models(force=refresh)
    items = result.get("items", [])
    return {
        "items": items,
        "count": len(items),
        "from_cache": result.get("from_cache", False),
        "last_error": result.get("last_error", ""),
        "last_status": result.get("last_status", 0),
        "ok": bool(items) and not result.get("last_error"),
    }
