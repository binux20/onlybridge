from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from backend import config as cfg

MAX_BACKUPS = 20
_PLACEHOLDER_KEY = "sk-placeholder"


@dataclass
class SetupResult:
    tool: str
    target_path: str
    backup_path: Optional[str]
    before: Optional[str]
    after: str
    written: bool
    note: str = ""


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _backups_dir(tool: str) -> Path:
    d = cfg.DATA_DIR / f"{tool}_settings_backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _list_backups(tool: str) -> list[Path]:
    d = _backups_dir(tool)
    return sorted([p for p in d.iterdir() if p.is_file()], key=lambda p: p.name)


def _save_backup(tool: str, target: Path) -> Optional[Path]:
    if not target.exists():
        return None
    ts = time.strftime("%Y%m%d_%H%M%S")
    suffix = target.suffix or ".bak"
    dst = _backups_dir(tool) / f"{ts}{suffix}"
    dst.write_bytes(target.read_bytes())
    backups = _list_backups(tool)
    for old in backups[:-MAX_BACKUPS]:
        try:
            old.unlink()
        except OSError:
            pass
    return dst


def _read_text(p: Path) -> Optional[str]:
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _write_text(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _load_json_or_empty(p: Path) -> dict:
    raw = _read_text(p)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _apply(
    tool: str,
    target: Path,
    transform: Callable[[dict], dict],
    dry_run: bool,
) -> SetupResult:
    before = _read_text(target)
    current = _load_json_or_empty(target)
    new_data = transform(current)
    after = json.dumps(new_data, indent=2, ensure_ascii=False)

    if dry_run:
        return SetupResult(tool, str(target), None, before, after, written=False, note="dry_run")

    backup = _save_backup(tool, target)
    _write_text(target, after)
    return SetupResult(
        tool, str(target),
        str(backup) if backup else None,
        before, after, written=True,
    )


def _restore(tool: str, target: Path) -> SetupResult:
    backups = _list_backups(tool)
    if not backups:
        before = _read_text(target)
        return SetupResult(tool, str(target), None, before, before or "", written=False, note="no backups")
    latest = backups[-1]
    content = latest.read_text(encoding="utf-8")
    before = _read_text(target)
    _write_text(target, content)
    return SetupResult(tool, str(target), str(latest), before, content, written=True, note="restored")


# ---------- Claude Code ----------

def _claude_target() -> Path:
    override = (cfg.load_config().get("tool_paths") or {}).get("claude") or ""
    if override.strip():
        return Path(override.strip()).expanduser()
    return _home() / ".claude" / "settings.json"


def setup_claude_code(proxy_url: str, dry_run: bool = False, model: str | None = None) -> SetupResult:
    def transform(data: dict) -> dict:
        env = dict(data.get("env") or {})
        env["ANTHROPIC_BASE_URL"] = proxy_url
        env["ANTHROPIC_API_KEY"] = _PLACEHOLDER_KEY
        if model:
            env["ANTHROPIC_MODEL"] = model
        out = dict(data)
        out["env"] = env
        if model:
            out["model"] = model
        return out
    return _apply("claude", _claude_target(), transform, dry_run)


def restore_claude_code() -> SetupResult:
    return _restore("claude", _claude_target())


# ---------- OpenCode ----------

def _opencode_target() -> Path:
    override = (cfg.load_config().get("tool_paths") or {}).get("opencode") or ""
    if override.strip():
        return Path(override.strip()).expanduser()
    base = _home() / ".config" / "opencode"
    j = base / "opencode.json"
    jc = base / "opencode.jsonc"
    if jc.exists() and not j.exists():
        return jc
    return j


def setup_opencode(proxy_url: str, dry_run: bool = False, model: str | None = None, sub_model: str | None = None) -> SetupResult:
    base_url = proxy_url.rstrip("/") + "/v1"
    main_label = model or "OnlyBridge Main"
    sub_label = sub_model or model or "OnlyBridge Sub"

    def transform(data: dict) -> dict:
        out = dict(data)
        providers = dict(out.get("provider") or {})
        providers["onlybridge"] = {
            "npm": "@ai-sdk/openai-compatible",
            "name": "OnlyBridge",
            "options": {"baseURL": base_url, "apiKey": _PLACEHOLDER_KEY},
            "models": {
                "main": {"name": main_label},
                "sub":  {"name": sub_label},
            },
        }
        out["provider"] = providers
        if model:
            out["model"] = "onlybridge/main"
        return out
    return _apply("opencode", _opencode_target(), transform, dry_run)


def restore_opencode() -> SetupResult:
    return _restore("opencode", _opencode_target())


# ---------- Continue ----------

def _continue_target() -> Path:
    return _home() / ".continue" / "config.json"


def setup_continue(proxy_url: str, dry_run: bool = False) -> SetupResult:
    api_base = proxy_url.rstrip("/") + "/v1"

    def transform(data: dict) -> dict:
        out = dict(data)
        models = list(out.get("models") or [])
        models = [m for m in models if not (isinstance(m, dict) and m.get("title", "").startswith("OnlyBridge"))]
        models.append({
            "title": "OnlyBridge",
            "provider": "openai",
            "model": "main",
            "apiBase": api_base,
            "apiKey": _PLACEHOLDER_KEY,
        })
        out["models"] = models
        return out
    return _apply("continue", _continue_target(), transform, dry_run)


def restore_continue() -> SetupResult:
    return _restore("continue", _continue_target())


# ---------- aider ----------

def _aider_target() -> Path:
    return _home() / ".aider.conf.yml"


def setup_aider(proxy_url: str, dry_run: bool = False) -> SetupResult:
    api_base = proxy_url.rstrip("/") + "/v1"
    target = _aider_target()
    before = _read_text(target)

    lines = (before or "").splitlines()
    kept = [l for l in lines if not l.lstrip().startswith(("openai-api-base:", "openai-api-key:", "model:"))]
    kept += [
        f"openai-api-base: {api_base}",
        f"openai-api-key: {_PLACEHOLDER_KEY}",
        "model: openai/main",
    ]
    after = "\n".join(kept).strip() + "\n"

    if dry_run:
        return SetupResult("aider", str(target), None, before, after, written=False, note="dry_run")

    backup = _save_backup("aider", target)
    _write_text(target, after)
    return SetupResult(
        "aider", str(target),
        str(backup) if backup else None,
        before, after, written=True,
    )


def restore_aider() -> SetupResult:
    return _restore("aider", _aider_target())


SETUP_FUNCS: dict[str, Callable[..., SetupResult]] = {
    "claude": setup_claude_code,
    "opencode": setup_opencode,
    "continue": setup_continue,
    "aider": setup_aider,
}

RESTORE_FUNCS: dict[str, Callable[[], SetupResult]] = {
    "claude": restore_claude_code,
    "opencode": restore_opencode,
    "continue": restore_continue,
    "aider": restore_aider,
}
