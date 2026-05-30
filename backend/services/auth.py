from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from backend import config as cfg

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
_OPEN_PREFIXES = ("/assets/", "/favicon")
_OPEN_PATHS = {"/", "/api/health"}


def _client_host(request: Request) -> str:
    cl = request.client
    return (cl.host if cl else "") or ""


def _path_is_open(path: str) -> bool:
    if path in _OPEN_PATHS:
        return True
    if path.startswith(_OPEN_PREFIXES):
        return True
    # SPA routes that aren't /api/* -> let index.html load (auth happens in JS layer)
    if not path.startswith("/api/"):
        return True
    return False


def extract_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "") or ""
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    x = request.headers.get("X-OnlyBridge-Token", "") or ""
    return x.strip()


def is_authorized(request: Request) -> bool:
    """True if the request may proceed.

    Rules:
    - Requests from 127.0.0.1 / ::1 bypass auth (local-only convenience).
    - If bridge_auth_token is empty, all requests pass (legacy 127.0.0.1-only setup).
    - Otherwise the Authorization: Bearer <token> header must match.
    """
    if _client_host(request) in _LOCAL_HOSTS:
        return True
    token = (cfg.load_config().get("bridge_auth_token") or "").strip()
    if not token:
        return True
    return extract_token(request) == token


async def dashboard_auth_middleware(request: Request, call_next):
    if _path_is_open(request.url.path):
        return await call_next(request)
    if is_authorized(request):
        return await call_next(request)
    return JSONResponse({"error": "unauthorized", "hint": "missing or invalid bridge_auth_token"}, status_code=401)
