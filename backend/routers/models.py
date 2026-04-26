from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from backend.services import models as models_service

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
async def list_models(refresh: bool = Query(False)) -> dict[str, Any]:
    items = await models_service.list_models(force=refresh)
    return {"items": items, "count": len(items)}
