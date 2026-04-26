from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager

mimetypes.init()
for _ext, _type in (
    (".js", "application/javascript"),
    (".mjs", "application/javascript"),
    (".css", "text/css"),
    (".svg", "image/svg+xml"),
    (".map", "application/json"),
    (".json", "application/json"),
    (".html", "text/html"),
):
    mimetypes.add_type(_type, _ext)
    if mimetypes._db is not None:
        mimetypes._db.types_map[True][_ext] = _type

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend import config as cfg
from backend import db
from backend.routers import config as config_router
from backend.routers import setup as setup_router
from backend.routers import stats as stats_router
from backend.routers import logs as logs_router
from backend.routers import models as models_router
from backend.routers import health as health_router
from backend.services.process_manager import ManagedProcess, registry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("onlybridge")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg.load_config()
    db.init_db()
    for name, module in cfg.PROXY_MODULES.items():
        registry.register(ManagedProcess(name=name, python_module=module, port=cfg.PROXY_PORTS[name]))
    log.info("OnlyBridge dashboard ready on http://127.0.0.1:%d", cfg.PORT_DASHBOARD)
    try:
        yield
    finally:
        await registry.stop_all()


app = FastAPI(title="OnlyBridge", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:*", "http://127.0.0.1:*"],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": app.version}


app.include_router(config_router.router)
app.include_router(setup_router.router)
app.include_router(stats_router.router)
app.include_router(logs_router.router)
app.include_router(models_router.router)
app.include_router(health_router.router)


_PLACEHOLDER_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>OnlyBridge</title>
<style>
  body { font-family: Inter, system-ui, sans-serif; background:#0a0a0a; color:#eee;
         display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }
  .card { border:1px solid #262626; padding:32px 40px; border-radius:8px; max-width:520px; }
  h1 { margin:0 0 8px; font-weight:600; }
  .accent { color:#6366f1; }
  code { background:#111; padding:2px 6px; border-radius:4px; font-family:'JetBrains Mono',monospace; }
  p { color:#aaa; line-height:1.5; }
</style>
</head>
<body>
  <div class="card">
    <h1>OnlyBridge <span class="accent">&middot;</span> backend up</h1>
    <p>Frontend not built yet. Backend is running on port 8800.</p>
    <p>Try <code>GET /api/health</code>.</p>
  </div>
</body>
</html>
"""


_MIME_BY_SUFFIX = {
    ".js": b"application/javascript",
    ".mjs": b"application/javascript",
    ".css": b"text/css",
    ".svg": b"image/svg+xml",
    ".map": b"application/json",
    ".json": b"application/json",
}


class MimeFixASGI:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "").lower()
        override = None
        for suffix, ctype in _MIME_BY_SUFFIX.items():
            if path.endswith(suffix):
                override = ctype
                break
        if override is None:
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = [
                    (k, v) for (k, v) in message.get("headers", [])
                    if k.lower() != b"content-type"
                ]
                headers.append((b"content-type", override))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


from fastapi.responses import FileResponse, Response
from fastapi import HTTPException as _HTTPException

_INDEX_HTML = cfg.FRONTEND_DIST / "index.html"
_ASSETS_DIR = cfg.FRONTEND_DIST / "assets"

if _INDEX_HTML.exists():
    if _ASSETS_DIR.exists():
        app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str) -> Response:
        if full_path.startswith("api/") or full_path.startswith("assets/"):
            raise _HTTPException(status_code=404)
        candidate = cfg.FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(_INDEX_HTML), media_type="text/html")
else:
    @app.get("/", response_class=HTMLResponse)
    async def _placeholder() -> HTMLResponse:
        return HTMLResponse(_PLACEHOLDER_HTML)

app.add_middleware(MimeFixASGI)
