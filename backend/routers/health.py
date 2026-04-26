from __future__ import annotations

from typing import Any

from fastapi import APIRouter

try:
    import tiktoken  # noqa: F401
    _HAS_TIKTOKEN = True
except Exception:
    _HAS_TIKTOKEN = False

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/tokens")
async def tokens_status() -> dict[str, Any]:
    return {
        "has_tiktoken": _HAS_TIKTOKEN,
        "install_cmd": "py -m pip install tiktoken",
    }
