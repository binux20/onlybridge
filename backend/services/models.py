from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import aiohttp

ONLYSQ_MODELS_URL = "https://api.onlysq.ru/ai/models"

_KEYWORDS = (
    "claude", "deepseek", "gemini", "qwen", "sonar", "glm",
    "pplx", "mistral", "llama", "grok", "kimi", "gpt", "yi",
)
_KEYWORD_REGEX = re.compile(r"(?:^|[^a-z0-9])o\d(?:$|[^a-z])")
_EXCLUDE = ("vision", "image", "audio", "tts", "whisper", "embed", "-vl", "-omni", "qvq")

_CACHE_TTL = 24 * 3600
_lock = asyncio.Lock()
_cache: dict[str, Any] = {"ts": 0.0, "items": []}


def _matches_whitelist(model_id: str) -> bool:
    lower = model_id.lower()
    if any(k in lower for k in _KEYWORDS):
        return True
    if _KEYWORD_REGEX.search(lower):
        return True
    return False


def _is_text_model(model_id: str, info: dict) -> bool:
    lower = model_id.lower()
    if any(bad in lower for bad in _EXCLUDE):
        return False
    name_lower = str(info.get("name") or "").lower()
    if any(bad in name_lower for bad in _EXCLUDE):
        return False
    modality = str(info.get("modality") or "").lower()
    if modality and modality != "text":
        return False
    return True


def _filter(raw_models: dict[str, dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for mid, info in raw_models.items():
        if not isinstance(info, dict):
            continue
        if not _matches_whitelist(mid):
            continue
        if not _is_text_model(mid, info):
            continue
        out.append({
            "id": mid,
            "name": info.get("name") or mid,
            "description": info.get("description") or "",
            "can_tools": bool(info.get("can-tools")),
            "can_think": bool(info.get("can-think")),
            "tier": info.get("tier"),
            "status": info.get("status"),
        })
    out.sort(key=lambda m: m["id"].lower())
    return out


async def _fetch_raw() -> dict[str, dict]:
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.get(ONLYSQ_MODELS_URL) as r:
            if r.status != 200:
                return {}
            data = await r.json()
            models = data.get("models") if isinstance(data, dict) else None
            return models if isinstance(models, dict) else {}


async def list_models(*, force: bool = False) -> list[dict[str, Any]]:
    async with _lock:
        now = time.time()
        if not force and _cache["items"] and (now - _cache["ts"]) < _CACHE_TTL:
            return _cache["items"]
        raw = await _fetch_raw()
        items = _filter(raw)
        if items:
            _cache["items"] = items
            _cache["ts"] = now
        return items or _cache["items"]


def invalidate_cache() -> None:
    _cache["ts"] = 0.0
