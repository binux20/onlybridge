from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.services.process_manager import registry

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("/{proxy}")
async def stream_logs(proxy: str) -> StreamingResponse:
    mp = registry.get(proxy)
    if mp is None:
        raise HTTPException(status_code=404, detail=f"unknown proxy '{proxy}'")

    async def gen():
        ag = mp.logs_stream().__aiter__()
        while True:
            try:
                line = await asyncio.wait_for(ag.__anext__(), timeout=15.0)
                yield f"data: {json.dumps({'line': line})}\n\n"
            except asyncio.TimeoutError:
                yield ": ping\n\n"
            except StopAsyncIteration:
                break
            except asyncio.CancelledError:
                break

    return StreamingResponse(gen(), media_type="text/event-stream")
