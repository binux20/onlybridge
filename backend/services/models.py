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
_RETRY_DELAY = 1.0
_MAX_ATTEMPTS = 10
_lock = asyncio.Lock()
_cache: dict[str, Any] = {"ts": 0.0, "items": [], "last_error": "", "last_status": 0}


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


async def _fetch_raw_once() -> tuple[dict[str, dict] | None, int, str]:
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(ONLYSQ_MODELS_URL) as r:
                if r.status != 200:
                    return None, r.status, f"HTTP {r.status}"
                data = await r.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        return None, 0, f"{type(e).__name__}: {e}"
    except Exception as e:
        return None, 0, f"{type(e).__name__}: {e}"
    models = data.get("models") if isinstance(data, dict) else None
    if isinstance(models, dict) and models:
        return models, 200, ""
    if isinstance(data, list) and data:
        # accept list-of-objects payload
        converted: dict[str, dict] = {}
        for item in data:
            if isinstance(item, dict):
                mid = item.get("id") or item.get("name")
                if mid:
                    converted[str(mid)] = item
        if converted:
            return converted, 200, ""
    return None, 200, "empty payload"


async def _fetch_raw_retry() -> tuple[dict[str, dict], str, int]:
    """Returns (raw_models, last_error, last_status). raw_models is {} if all attempts failed."""
    last_err = ""
    last_status = 0
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        raw, status, err = await _fetch_raw_once()
        last_status = status
        if raw is not None:
            return raw, "", status
        last_err = err
        if attempt < _MAX_ATTEMPTS:
            await asyncio.sleep(_RETRY_DELAY)
    return {}, last_err, last_status


async def list_models(*, force: bool = False) -> dict[str, Any]:
    async with _lock:
        now = time.time()
        if not force and _cache["items"] and (now - _cache["ts"]) < _CACHE_TTL:
            return {
                "items": _cache["items"],
                "from_cache": True,
                "last_error": _cache.get("last_error", ""),
                "last_status": _cache.get("last_status", 0),
            }
        raw, err, status = await _fetch_raw_retry()
        items = _filter(raw)
        if items:
            _cache["items"] = items
            _cache["ts"] = now
            _cache["last_error"] = ""
            _cache["last_status"] = status
            return {"items": items, "from_cache": False, "last_error": "", "last_status": status}
        _cache["last_error"] = err or "empty"
        _cache["last_status"] = status
        return {
            "items": _cache["items"],
            "from_cache": bool(_cache["items"]),
            "last_error": _cache["last_error"],
            "last_status": status,
        }


def invalidate_cache() -> None:
    _cache["ts"] = 0.0
