from __future__ import annotations

import json
import uuid
from pathlib import Path
from threading import RLock
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"
BACKUPS_DIR = DATA_DIR / "claude_settings_backups"

CONFIG_PATH = DATA_DIR / "config.json"
DATABASE_PATH = DATA_DIR / "onlybridge.db"

PORT_DASHBOARD = 8800
PORT_CLAUDE = 7777
PORT_OPENCODE = 7778
PORT_OPENAI_COMPAT = 7779

PROXY_PORTS = {
    "claude": PORT_CLAUDE,
    "opencode": PORT_OPENCODE,
    "openai_compat": PORT_OPENAI_COMPAT,
}

PROXY_MODULES = {
    "claude": "backend.proxies.proxy_claude",
    "opencode": "backend.proxies.proxy_opencode_fixed",
    "openai_compat": "backend.proxies.proxy_openaicompabilite",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "onlysq_key": "",
    "main_model": "claude-opus-4-7",
    "sub_model": "claude-haiku-4-5",
    "vision_model": "gemini-2.5-pro",
    "telemetry_opt_in": False,
    "anonymous_id": "",
    "lang": "en",
    "stream_mode": "realtime",
    "tool_paths": {"claude": "", "opencode": ""},
    "proxy_models": {
        "claude":        {"main": "", "sub": ""},
        "opencode":      {"main": "", "sub": ""},
        "openai_compat": {"main": "", "sub": ""},
    },
}

_lock = RLock()


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    with _lock:
        _ensure_dirs()
        if not CONFIG_PATH.exists():
            cfg = dict(DEFAULT_CONFIG)
            cfg["anonymous_id"] = uuid.uuid4().hex
            _write(cfg)
            return cfg
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = {}
        changed = False
        for key, default in DEFAULT_CONFIG.items():
            if key not in cfg:
                cfg[key] = default
                changed = True
        if not cfg.get("anonymous_id"):
            cfg["anonymous_id"] = uuid.uuid4().hex
            changed = True
        if changed:
            _write(cfg)
        return cfg


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        cfg = load_config()
        for key, value in patch.items():
            if key in DEFAULT_CONFIG or key == "anonymous_id":
                if key == "tool_paths" and isinstance(value, dict):
                    cur = cfg.get("tool_paths") or {}
                    cur.update({k: v for k, v in value.items() if isinstance(k, str)})
                    cfg[key] = cur
                elif key == "proxy_models" and isinstance(value, dict):
                    cur = cfg.get("proxy_models") or {}
                    for proxy_name, sub_dict in value.items():
                        if not isinstance(proxy_name, str) or not isinstance(sub_dict, dict):
                            continue
                        slot = dict(cur.get(proxy_name) or {})
                        for field in ("main", "sub"):
                            if field in sub_dict and isinstance(sub_dict[field], (str, type(None))):
                                slot[field] = sub_dict[field] or ""
                        cur[proxy_name] = slot
                    cfg[key] = cur
                else:
                    cfg[key] = value
        _write(cfg)
        return cfg


def _write(cfg: dict[str, Any]) -> None:
    _ensure_dirs()
    CONFIG_PATH.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
