from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
from fastapi import APIRouter
from pydantic import BaseModel

from backend import config as cfg
from backend.services import models as models_service  # noqa: F401

router = APIRouter(prefix="/api/config", tags=["config"])

_SAFE_FIELDS = ("main_model", "sub_model", "vision_model", "telemetry_opt_in")


class ConfigPatch(BaseModel):
    onlysq_key: str | None = None
    main_model: str | None = None
    sub_model: str | None = None
    vision_model: str | None = None
    telemetry_opt_in: bool | None = None
    lang: str | None = None
    stream_mode: str | None = None
    tool_paths: dict[str, str] | None = None
    proxy_models: dict[str, dict[str, str | None]] | None = None


def _redact(c: dict[str, Any]) -> dict[str, Any]:
    out = dict(c)
    key = out.get("onlysq_key") or ""
    out["onlysq_key"] = ("..." + key[-6:]) if key else ""
    out["has_key"] = bool(key)
    return out


async def _broadcast_to_proxies(patch: dict[str, Any]) -> None:
    if not patch:
        return
    cur = cfg.load_config()
    pm = cur.get("proxy_models") or {}
    timeout = aiohttp.ClientTimeout(total=2)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async def _post(name: str, port: int):
            slot = pm.get(name) or {}
            per_patch = dict(patch)
            if "main_model" in per_patch or "sub_model" in per_patch or "proxy_models" in per_patch:
                main = (slot.get("main") or "").strip() or per_patch.get("main_model") or cur.get("main_model")
                sub  = (slot.get("sub") or "").strip() or per_patch.get("sub_model") or cur.get("sub_model")
                if main:
                    per_patch["main_model"] = main
                if sub:
                    per_patch["sub_model"] = sub
            per_patch.pop("proxy_models", None)
            try:
                await s.post(f"http://127.0.0.1:{port}/config", json=per_patch)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
        await asyncio.gather(*(_post(n, p) for n, p in cfg.PROXY_PORTS.items()))


@router.get("")
async def get_config() -> dict[str, Any]:
    return _redact(cfg.load_config())


@router.post("")
async def update_config(patch: ConfigPatch) -> dict[str, Any]:
    payload = patch.model_dump(exclude_none=True)
    cfg.save_config(payload)
    await _broadcast_to_proxies(payload)
    return _redact(cfg.load_config())
