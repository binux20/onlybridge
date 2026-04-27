from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import config as cfg
from backend.services import config_writer as cw
from backend.services.process_manager import registry

router = APIRouter(prefix="/api/setup", tags=["setup"])

_TOOL_TO_PROXY = {
    "claude": "claude",
    "opencode": "opencode",
    "openai_compat": "openai_compat",
}

_TOOLS_WITH_AUTO_CONFIG = ("claude", "opencode")


def _models_for_tool(c: dict, tool: str) -> tuple[str | None, str | None]:
    pm = (c.get("proxy_models") or {}).get(tool) or {}
    main = (pm.get("main") or "").strip() or (c.get("main_model") or "").strip() or None
    sub = (pm.get("sub") or "").strip() or (c.get("sub_model") or "").strip() or None
    return main, sub


def _proxy_url_for(tool: str) -> str:
    proxy_name = _TOOL_TO_PROXY.get(tool)
    if not proxy_name:
        raise HTTPException(status_code=404, detail=f"unknown tool '{tool}'")
    port = cfg.PROXY_PORTS[proxy_name]
    return f"http://127.0.0.1:{port}"


def _proxy_for(tool: str):
    proxy_name = _TOOL_TO_PROXY.get(tool)
    if not proxy_name:
        raise HTTPException(status_code=404, detail=f"unknown tool '{tool}'")
    mp = registry.get(proxy_name)
    if mp is None:
        raise HTTPException(status_code=503, detail=f"proxy '{proxy_name}' not registered")
    return mp


async def _fetch_proxy_main_model(port: int) -> str | None:
    timeout = aiohttp.ClientTimeout(total=2)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(f"http://127.0.0.1:{port}/config") as r:
                if r.status != 200:
                    return None
                data = await r.json()
                return data.get("main_model")
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None


class SetupBody(BaseModel):
    confirm: bool = False


@router.get("/{tool}/preview")
async def preview(tool: str) -> dict[str, Any]:
    if tool not in _TOOL_TO_PROXY:
        raise HTTPException(status_code=404, detail=f"unknown tool '{tool}'")
    if tool == "openai_compat":
        return {
            "tool": tool,
            "target_path": "manual",
            "after": f"Use baseURL: {_proxy_url_for(tool)}/v1 and any api_key in your tool.",
            "written": False,
            "note": "manual setup",
        }
    cur = cfg.load_config()
    model, sub_model = _models_for_tool(cur, tool)
    fn = cw.SETUP_FUNCS[tool]
    kwargs = {"dry_run": True, "model": model}
    if tool == "opencode":
        kwargs["sub_model"] = sub_model
    res = fn(_proxy_url_for(tool), **kwargs)
    return res.__dict__


@router.get("/{tool}/status")
async def status(tool: str) -> dict[str, Any]:
    mp = _proxy_for(tool)
    has_key = bool((cfg.load_config().get("onlysq_key") or "").strip())
    return {
        "tool": tool,
        "proxy": mp.info(),
        "has_key": has_key,
    }


async def _push_models_to_proxy(tool: str, model: str | None, sub_model: str | None) -> None:
    proxy_name = _TOOL_TO_PROXY.get(tool)
    if not proxy_name:
        return
    port = cfg.PROXY_PORTS[proxy_name]
    payload: dict[str, Any] = {}
    if model:
        payload["main_model"] = model
    if sub_model:
        payload["sub_model"] = sub_model
    if not payload:
        return
    timeout = aiohttp.ClientTimeout(total=2)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            await s.post(f"http://127.0.0.1:{port}/config", json=payload)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        pass


@router.post("/{tool}/start")
async def start(tool: str, body: SetupBody = SetupBody()) -> dict[str, Any]:
    if tool not in _TOOL_TO_PROXY:
        raise HTTPException(status_code=404, detail=f"unknown tool '{tool}'")
    mp = _proxy_for(tool)
    if not (cfg.load_config().get("onlysq_key") or "").strip():
        raise HTTPException(status_code=400, detail="OnlySQ key not configured")

    if mp.status() == "running":
        await mp.stop()
    proc_info = await mp.start()

    cur = cfg.load_config()
    model, sub_model = _models_for_tool(cur, tool)
    await _push_models_to_proxy(tool, model, sub_model)

    if tool == "openai_compat":
        return {
            "proxy": proc_info,
            "config": {
                "tool": tool,
                "target_path": "manual",
                "after": f"baseURL: {_proxy_url_for(tool)}/v1",
                "written": False,
                "note": "manual setup",
            },
            "model": model,
        }

    fn = cw.SETUP_FUNCS[tool]
    kwargs = {"dry_run": False, "model": model}
    if tool == "opencode":
        kwargs["sub_model"] = sub_model
    write_res = fn(_proxy_url_for(tool), **kwargs)
    return {"proxy": proc_info, "config": write_res.__dict__, "model": model}


@router.post("/{tool}/stop")
async def stop(tool: str) -> dict[str, Any]:
    mp = _proxy_for(tool)
    proc_info = await mp.stop()
    if tool in _TOOLS_WITH_AUTO_CONFIG:
        restore_res = cw.RESTORE_FUNCS[tool]()
        return {"proxy": proc_info, "config": restore_res.__dict__}
    return {"proxy": proc_info, "config": {"note": "manual setup, no restore needed"}}
