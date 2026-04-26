"""
OnlySQ → OpenAI API Proxy for OpenCode
Accepts OpenAI format requests, emulates tool use via prompt injection.
(OnlySQ native tools are broken, so we inject tools as system prompt)

Start: uvicorn proxy_opencode_fixed:app --host 127.0.0.1 --port 7778
OpenCode: base_url=http://127.0.0.1:7778/v1  api_key=any
"""

import asyncio
import json
import re
import time
import logging
import uuid
from collections import deque
from typing import AsyncGenerator
import contextlib
from pathlib import Path

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from backend.proxies._stats_db import log_request, tokens_from_messages, count_tokens as _count_tokens

# ─────────────────────────────────────────────
# КОНФИГУРАЦИЯ
# ─────────────────────────────────────────────
ONLYSQ_URL = "https://api.onlysq.ru/ai/openai/chat/completions"

MAIN_MODEL = "claude-opus-4-5"
MAIN_RPM   = 3
SUB_MODEL  = "claude-haiku-4-5"
SUB_RPM    = 10

WINDOW_SEC   = 60
SOFT_BAN_SEC = 25
HTTP_TIMEOUT = 300
LOOP_TTL_SEC = 120

VISION_MODEL  = "gemini-2.5-pro"
VISION_RPM    = 10

_BRIDGE_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "config.json"
CONFIG_FILE      = Path(__file__).parent / "proxy_config.json"
MODELS_CACHE_TTL = 86400


def _read_onlysq_key() -> str:
    try:
        with open(_BRIDGE_CONFIG_PATH, "r", encoding="utf-8") as f:
            return (json.load(f).get("onlysq_key") or "").strip()
    except (OSError, json.JSONDecodeError):
        return ""


def _read_stream_mode() -> str:
    try:
        with open(_BRIDGE_CONFIG_PATH, "r", encoding="utf-8") as f:
            v = (json.load(f).get("stream_mode") or "realtime").strip().lower()
            return v if v in ("realtime", "legacy") else "realtime"
    except (OSError, json.JSONDecodeError):
        return "realtime"


def _write_onlysq_key(key: str) -> None:
    try:
        data = {}
        if _BRIDGE_CONFIG_PATH.exists():
            with open(_BRIDGE_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        data["onlysq_key"] = key.strip()
        _BRIDGE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_BRIDGE_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError as e:
        log.error(f"[CONFIG] cannot save onlysq_key: {e}")

# ─────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("proxy")

# ─────────────────────────────────────────────
# КОНФИГУРАЦИЯ С ПЕРСИСТЕНТНОСТЬЮ
# ─────────────────────────────────────────────
class ProxyConfig:
    def __init__(self, path: Path):
        self.path = path
        self.data = self._load()

    def _default(self) -> dict:
        return {
            "models_cache": [],
            "models_cache_ts": 0,
            "user_preferences": {
                "main_model": MAIN_MODEL,
                "sub_model":  SUB_MODEL,
                "vision_model": VISION_MODEL,
            },
            "vision_support": {},
        }

    def _load(self) -> dict:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                default = self._default()
                for key in default:
                    if key not in data:
                        data[key] = default[key]
                return data
            except Exception as e:
                log.warning(f"[CONFIG] Ошибка загрузки: {e}")
        return self._default()

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log.error(f"[CONFIG] Ошибка сохранения: {e}")

    def get_main_model(self)   -> str: return self.data["user_preferences"].get("main_model",   MAIN_MODEL)
    def get_sub_model(self)    -> str: return self.data["user_preferences"].get("sub_model",    SUB_MODEL)
    def get_vision_model(self) -> str: return self.data["user_preferences"].get("vision_model", VISION_MODEL)

    def _is_model_valid(self, model: str) -> bool:
        if not self.data.get("models_cache"):
            return True
        return model in {m.get("id") for m in self.data["models_cache"]}

    def set_main_model(self, model: str) -> bool:
        if not self._is_model_valid(model): return False
        self.data["user_preferences"]["main_model"] = model; self._save(); return True

    def set_sub_model(self, model: str) -> bool:
        if not self._is_model_valid(model): return False
        self.data["user_preferences"]["sub_model"] = model; self._save(); return True

    def set_vision_model(self, model: str) -> bool:
        if not self._is_model_valid(model): return False
        self.data["user_preferences"]["vision_model"] = model; self._save(); return True

    def is_vision_capable(self, model: str) -> bool | None:
        return self.data["vision_support"].get(model)

    def set_vision_capable(self, model: str, capable: bool):
        self.data["vision_support"][model] = capable; self._save()

    def get_models_cache(self) -> list:
        return self.data.get("models_cache", [])

    def is_cache_valid(self) -> bool:
        ts = self.data.get("models_cache_ts", 0)
        return (time.time() - ts) < MODELS_CACHE_TTL and len(self.data.get("models_cache", [])) > 0

    def set_models_cache(self, models: list):
        self.data["models_cache"] = models
        self.data["models_cache_ts"] = time.time()
        self._save()
        log.info(f"[CONFIG] Кэш моделей: {len(models)} шт.")


config = ProxyConfig(CONFIG_FILE)

# ─────────────────────────────────────────────
# RATE LIMITER (single key from OnlyBridge config)
# ─────────────────────────────────────────────
class KeyPool:
    def __init__(self, main_rpm: int, sub_rpm: int):
        self.main_rpm = main_rpm
        self.sub_rpm  = sub_rpm
        self._ts_main: deque = deque()
        self._ts_sub:  deque = deque()
        self._ban_until: float = 0.0
        self._lock = asyncio.Lock()
        self._key_cached: str = _read_onlysq_key()

    def reload_key(self) -> None:
        self._key_cached = _read_onlysq_key()

    def current_key(self) -> str:
        return self._key_cached or _read_onlysq_key()

    def _evict(self, now: float):
        while self._ts_main and now - self._ts_main[0] >= WINDOW_SEC: self._ts_main.popleft()
        while self._ts_sub  and now - self._ts_sub[0]  >= WINDOW_SEC: self._ts_sub.popleft()

    async def acquire(self, is_sub: bool) -> str | None:
        while True:
            async with self._lock:
                key = self.current_key()
                if not key:
                    return None
                now = time.monotonic()
                self._evict(now)
                ts    = self._ts_sub  if is_sub else self._ts_main
                rpm   = self.sub_rpm  if is_sub else self.main_rpm
                label = "SUB" if is_sub else "MAIN"
                if now < self._ban_until:
                    min_wait = self._ban_until - now
                elif len(ts) < rpm:
                    ts.append(now)
                    log.info(f"[POOL] {label} ...{key[-6:]} | {len(ts)}/{rpm} rpm")
                    return key
                else:
                    min_wait = WINDOW_SEC - (now - ts[0]) if ts else WINDOW_SEC
                log.info(f"[POOL] limit, waiting {min_wait:.1f}s")
            await asyncio.sleep(max(0.1, min_wait + 0.1))

    async def ban(self, key: str, is_sub: bool):
        async with self._lock:
            self._ban_until = time.monotonic() + SOFT_BAN_SEC
            log.warning(f"[POOL] 429 - pausing {SOFT_BAN_SEC}s")


pool = KeyPool(MAIN_RPM, SUB_RPM)

# ─────────────────────────────────────────────
# VIRTUAL LOOP
# ─────────────────────────────────────────────
class LoopState:
    __slots__ = ("base_messages", "text_before", "all_tools", "pending", "results", "created_at")
    def __init__(self):
        self.base_messages: list = []
        self.text_before: str   = ""
        self.all_tools: list    = []
        self.pending: list      = []
        self.results: list      = []
        self.created_at: float  = time.monotonic()


_loops: dict[str, LoopState] = {}
_loops_lock = asyncio.Lock()

def _gc_loops():
    now  = time.monotonic()
    dead = [k for k, v in _loops.items() if now - v.created_at > LOOP_TTL_SEC]
    for k in dead:
        del _loops[k]

# ─────────────────────────────────────────────
# JSON REPAIR
# ─────────────────────────────────────────────
_REPAIR_SUFFIXES = ('"', '}', '"}', '}}', '"}}', '"]}', '"]}}', '"}]')

def repair_json(s: str) -> dict | None:
    s = s.strip()
    try:    return json.loads(s)
    except: pass
    for suf in _REPAIR_SUFFIXES:
        try:    return json.loads(s + suf)
        except: pass
    idx = s.rfind("}")
    if idx > 0:
        try:    return json.loads(s[:idx + 1])
        except: pass
    return None

# ─────────────────────────────────────────────
# ПАРСИНГ ТУЛОВ ИЗ ТЕКСТА
# ─────────────────────────────────────────────
_RE_FENCED_OBJ = re.compile(r"```(?:json)?\s*(\{.*?\})\s*(?:```|$)",  re.DOTALL | re.IGNORECASE)
_RE_FENCED_ARR = re.compile(r"```(?:json)?\s*(\[.*?\])\s*(?:```|$)",  re.DOTALL | re.IGNORECASE)
_RE_RAW_ARR    = re.compile(r"(\[\s*\{.*?\}\s*\])", re.DOTALL)

def _make_tool_block(data: dict, idx: int) -> dict:
    return {
        "type":  "tool_use",
        "id":    f"toolu_{uuid.uuid4().hex[:16]}",
        "name":  data["name"],
        "input": data.get("arguments", data.get("input", {})),
    }

def _is_valid_tool_call(d: dict) -> bool:
    if not isinstance(d, dict): return False
    name = d.get("name")
    if not isinstance(name, str) or len(name) < 2: return False
    if name.lower() in ("example", "test", "sample", "demo", "model", "type", "object"): return False
    suspicious = {"description", "properties", "required", "schema", "title", "version"}
    if suspicious & set(d.keys()): return False
    return True

def _parse_tool_items(raw: str) -> list[dict]:
    try:
        items = json.loads(raw)
        if isinstance(items, dict): items = [items]
    except Exception:
        item = repair_json(raw)
        items = [item] if item else []
    return [_make_tool_block(d, i) for i, d in enumerate(items) if _is_valid_tool_call(d)]

def extract_tools(text: str) -> tuple[list[dict], str]:
    candidates: list[tuple[int, int, str]] = []
    for pat in (_RE_FENCED_OBJ, _RE_FENCED_ARR, _RE_RAW_ARR):
        for m in pat.finditer(text):
            candidates.append((m.start(), m.end(), m.group(1)))
    if not candidates:
        return [], text
    candidates.sort(key=lambda x: x[0])
    deduped: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, raw in candidates:
        if start >= last_end:
            deduped.append((start, end, raw))
            last_end = end
    text_before = text[:deduped[0][0]].strip()
    tools: list[dict] = []
    for _, _, raw in deduped:
        for tool in _parse_tool_items(raw):
            if tool["name"] == "Agent":
                return [tool], text_before
            tools.append(tool)
    return tools, text_before

# ─────────────────────────────────────────────
# СИСТЕМНЫЙ ПРОМПТ С ТУЛАМИ
# ─────────────────────────────────────────────
_TOOL_PRIORITY = ["Bash", "Write", "Read", "Edit", "LS", "Glob", "Grep",
                  "WebFetch", "WebSearch", "MultiEdit", "TodoWrite"]

_MUST_USE_TOOLS = {
    "WebSearch": "MUST call this tool to search the internet. NEVER answer from memory.",
    "WebFetch":  "MUST call this tool to fetch a URL. NEVER guess page content.",
    "Bash":      "MUST call this tool to run shell commands. NEVER simulate output.",
    "Read":      "MUST call this tool to read files. NEVER guess file contents.",
}

_SIZE_LIMIT = """
## File Operation Size Limits - CRITICAL
- Write tool: NEVER write more than 300 lines per call. Split large files into sequential calls.
- Edit tool: NEVER edit more than 300 lines at once. Use multiple Edit calls.
"""

_SUBAGENT_SPAWN_RULES = """
## Subagent Spawning (task / Agent / Task tools) - CRITICAL

If a tool named `task`, `Task`, or `Agent` is available, it lets you launch a sub-agent to handle a multi-step task autonomously. Strict rules for calling it:

### Required arguments (ALL must be present)
- **description** (string, required): Short 3-7 word summary of what the sub-agent will do. Example: "explore syntxaiapi project".
- **prompt** (string, required): The FULL task for the sub-agent. Must be self-contained: the sub-agent sees ONLY this prompt, nothing from the parent conversation. Include:
  * What exactly to investigate / build / fix.
  * Which files/paths/URLs are relevant.
  * The expected output format (e.g. "return a markdown report with sections X, Y, Z").
  * Any constraints (e.g. "do not modify files", "read-only").
- **subagent_type** (string, required): The kind of sub-agent. Common values: `"explore"` (read-only investigation of a codebase), `"general"` (generic autonomous task). If unsure, use `"explore"` for research tasks, `"general"` for build/fix tasks.

### Optional arguments
- **task_id** (string, optional): Only set when RESUMING a previously-suspended sub-agent task. Omit for new tasks.
- **command** (string, optional): Only when the subagent_type defines specific commands. Omit by default.

### Correct example (spawn a new explore-agent)
```json
{"name": "task", "arguments": {
  "description": "investigate project structure",
  "prompt": "Investigate the project at D:\\\\path\\\\to\\\\repo in depth. Return a markdown report covering: 1) top-level structure, 2) tech stack and dependencies, 3) entry points, 4) main modules. Do NOT modify any files.",
  "subagent_type": "explore"
}}
```

### Common mistakes - DO NOT make these
- Omitting `subagent_type` - causes validation error, sub-agent never starts.
- Passing a vague one-liner in `prompt` ("look at the project") - sub-agent has no context and returns empty.
- Referring to "the previous message" or "what the user said" - sub-agent does NOT see conversation history. Inline all needed info into `prompt`.
- Using `task` for trivial single-step work (reading one file, running one command) - just call the direct tool instead.
- Filling `task_id` for a NEW task - leave it out unless explicitly resuming.

### When to spawn a sub-agent
Use `task` when the work involves MULTIPLE steps that can be done independently from the main conversation: deep codebase exploration, multi-file refactors, research across many sources. For a single file read, one web fetch, or one shell command - do it directly, not via sub-agent.
"""

def build_tools_system(tools: list[dict]) -> str:
    sorted_tools = sorted(
        tools,
        key=lambda t: _TOOL_PRIORITY.index(t["name"]) if t["name"] in _TOOL_PRIORITY else 999,
    )
    lines = [
        "## Tool Use Instructions",
        "You have tools. Call them using fenced JSON blocks ONLY:",
        "",
        "```json",
        '{"name": "tool_name", "arguments": {"arg1": "value1"}}',
        "```",
        "",
        "CRITICAL Rules:",
        "- ONLY ```json blocks. Never XML or plain text descriptions.",
        "- PARALLEL TOOLS: When multiple tools are needed, output ALL ```json blocks",
        "  in ONE response. Do NOT wait for results before calling the next tool if",
        "  you can determine it independently. Example: need to read 3 files -> output",
        "  3 json blocks at once, not one by one.",
        "- For sub-agent tools ('task', 'Task', 'Agent'): see the Subagent Spawning section below - you MUST include description, prompt, AND subagent_type.",
        "- NEVER invent tool names - use only those listed below.",
        "- For MUST-marked tools: FORBIDDEN to answer without calling them first.",
        "- For non-trivial tasks: ALWAYS call a tool. Do NOT just respond in text.",
        "",
        "Available tools:",
    ]
    has_subagent_tool = False
    for t in sorted_tools:
        name  = t.get("name", "")
        if name in ("task", "Task", "Agent"):
            has_subagent_tool = True
        desc  = t.get("description", "")
        # поддерживаем оба формата: OpenAI (parameters) и Anthropic (input_schema)
        schema = t.get("input_schema") or t.get("parameters") or {}
        props  = schema.get("properties", {}) if isinstance(schema, dict) else {}
        req    = schema.get("required", []) if isinstance(schema, dict) else []
        args_hint = ", ".join(f"{k}{'*' if k in req else ''}" for k in list(props.keys())[:6])
        must_note = f" [MUST] {_MUST_USE_TOOLS[name]}" if name in _MUST_USE_TOOLS else ""
        lines.append(f"- **{name}**({args_hint}): {desc[:120]}{must_note}")
    lines.append(_SIZE_LIMIT)
    if has_subagent_tool:
        lines.append(_SUBAGENT_SPAWN_RULES)
    return "\n".join(lines)

# ─────────────────────────────────────────────
# КОНВЕРТАЦИЯ: OpenAI input → Anthropic внутренний формат
# ─────────────────────────────────────────────
def openai_messages_to_anthropic(messages: list[dict]) -> tuple[list[dict], str]:
    """
    Конвертирует список сообщений OpenAI формата в Anthropic формат.
    Возвращает (anthropic_messages, system_text).
    Обрабатывает:
      - role: system  → в system_text
      - role: tool    → role: user, content: [{type: tool_result, ...}]
      - assistant с tool_calls → role: assistant, content: [{type: tool_use, ...}, ...]
    """
    system_parts: list[str] = []
    result: list[dict] = []

    for msg in messages:
        role    = msg.get("role", "user")
        content = msg.get("content") or ""

        if role == "system":
            if isinstance(content, str):
                system_parts.append(content)
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        system_parts.append(b.get("text", ""))
            continue

        # OpenAI tool result → Anthropic tool_result
        if role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            text = content if isinstance(content, str) else json.dumps(content)
            result.append({
                "role": "user",
                "content": [{
                    "type":        "tool_result",
                    "tool_use_id": tool_call_id,
                    "content":     text,
                }]
            })
            continue

        # assistant с tool_calls → Anthropic tool_use блоки
        if role == "assistant" and msg.get("tool_calls"):
            blocks: list[dict] = []
            if isinstance(content, str) and content.strip():
                blocks.append({"type": "text", "text": content})
            for tc in msg["tool_calls"]:
                fn   = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    inp = json.loads(fn.get("arguments", "{}"))
                except Exception:
                    inp = {}
                blocks.append({
                    "type":  "tool_use",
                    "id":    tc.get("id", f"toolu_{uuid.uuid4().hex[:8]}"),
                    "name":  name,
                    "input": inp,
                })
            result.append({"role": "assistant", "content": blocks})
            continue

        # Обычное сообщение
        if isinstance(content, list):
            # Может быть multimodal
            blocks = []
            for b in content:
                if not isinstance(b, dict): continue
                if b.get("type") == "text":
                    blocks.append({"type": "text", "text": b.get("text", "")})
                elif b.get("type") == "image_url":
                    url = b.get("image_url", {}).get("url", "")
                    if url.startswith("data:"):
                        import re as _re
                        m = _re.match(r"data:([^;]+);base64,(.+)", url)
                        if m:
                            blocks.append({"type": "image", "source": {
                                "type": "base64", "media_type": m.group(1), "data": m.group(2)
                            }})
            result.append({"role": role, "content": blocks})
        else:
            result.append({"role": role, "content": content})

    return result, "\n\n".join(system_parts)


def merge_consecutive_same_role(messages: list[dict]) -> list[dict]:
    """
    OnlySQ не принимает два подряд идущих сообщения с одинаковой ролью.
    Сливаем их в одно.
    """
    merged: list[dict] = []
    for msg in messages:
        if merged and merged[-1]["role"] == msg["role"]:
            prev = merged[-1]
            pc = prev["content"]
            nc = msg["content"]
            if isinstance(pc, str) and isinstance(nc, str):
                prev["content"] = pc + "\n\n" + nc
            elif isinstance(pc, list) and isinstance(nc, list):
                prev["content"] = pc + nc
            elif isinstance(pc, list) and isinstance(nc, str):
                prev["content"] = pc + [{"type": "text", "text": nc}]
            elif isinstance(pc, str) and isinstance(nc, list):
                prev["content"] = [{"type": "text", "text": pc}] + nc
        else:
            merged.append({"role": msg["role"], "content": msg["content"]})
    return merged

# ─────────────────────────────────────────────
# КОНВЕРТАЦИЯ: Anthropic internal → OpenAI для OnlySQ
# ─────────────────────────────────────────────
_BILLING_RE = re.compile(r"x-anthropic-billing-header:[^\n]*\n?", re.IGNORECASE)
_reminder_counter = 0

def flatten_anthropic_content(content, image_descriptions: dict = None) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    image_idx = 0
    for block in content:
        if not isinstance(block, dict): continue
        t = block.get("type")
        if t == "text":
            parts.append(block.get("text", ""))
        elif t == "tool_use":
            inp = json.dumps(block.get("input", {}), ensure_ascii=False)
            parts.append(f'```json\n{{"name": "{block["name"]}", "arguments": {inp}}}\n```')
        elif t == "tool_result":
            c = block.get("content", "")
            if isinstance(c, list):
                c = "\n".join(b.get("text","") for b in c if isinstance(b,dict) and b.get("type")=="text")
            tid = block.get("tool_use_id", "")
            parts.append(f"[TOOL_RESULT id={tid}]\n{c}\n[/TOOL_RESULT]")
        elif t == "image":
            source = block.get("source", {})
            if source.get("type") == "base64" and source.get("data"):
                if image_descriptions and image_idx in image_descriptions:
                    parts.append(f"[IMAGE DESCRIPTION]:\n{image_descriptions[image_idx]}\n[/IMAGE DESCRIPTION]")
                else:
                    parts.append("[image in conversation history]")
                image_idx += 1
            else:
                parts.append("[image omitted]")
    return "\n\n".join(p for p in parts if p)


def anthropic_messages_to_openai(
    anthropic_body: dict,
    image_descriptions: dict = None,
) -> list[dict]:
    """
    Конвертирует Anthropic-формат (внутренний) → OpenAI messages для OnlySQ.
    Тулы и tool_result превращаются в обычный текст.
    """
    global _reminder_counter
    system_parts: list[str] = []

    sys = anthropic_body.get("system", "")
    if isinstance(sys, list):
        sys = " ".join(b.get("text","") for b in sys if isinstance(b,dict) and b.get("type")=="text")
    if sys:
        system_parts.append(sys)

    tools = anthropic_body.get("tools", [])
    if tools:
        system_parts.append(build_tools_system(tools))

    messages: list[dict] = []
    for msg_idx, msg in enumerate(anthropic_body.get("messages", [])):
        role    = msg.get("role", "user")
        content = msg.get("content", "")
        final_r = role if role in ("user", "assistant", "system") else "user"
        msg_img_descs = (image_descriptions or {}).get(msg_idx, {})

        text = flatten_anthropic_content(content, msg_img_descs)
        text = _BILLING_RE.sub("", text).strip()

        if final_r == "user":
            _reminder_counter += 1
            if _reminder_counter % 6 == 0:
                text += "\n\n[Reminder: use ```json blocks for ALL tool calls. No XML!]"

        if text:
            messages.append({"role": final_r, "content": text})

    # Вставляем system в первое user-сообщение
    if system_parts:
        sys_text = "\n\n".join(system_parts) + "\n\n"
        if messages and messages[0]["role"] in ("user", "system"):
            messages[0]["content"] = sys_text + messages[0]["content"]
        elif messages:
            messages.insert(0, {"role": "user", "content": sys_text})
        else:
            messages.append({"role": "user", "content": sys_text})

    return merge_consecutive_same_role(messages)


def build_onlysq_body(anthropic_body: dict, model: str) -> dict:
    return {
        "model":    model,
        "messages": anthropic_messages_to_openai(anthropic_body),
        "stream":   False,
    }

# ─────────────────────────────────────────────
# ANTHROPIC RESPONSE ← текст
# ─────────────────────────────────────────────
def to_anthropic_response(text: str, model: str, stop_reason: str = "end_turn") -> dict:
    content: list[dict] = []
    tools, text_before = extract_tools(text)
    if text_before:
        content.append({"type": "text", "text": text_before})
    if tools:
        content.extend(tools)
        stop_reason = "tool_use"
    elif not text_before:
        content.append({"type": "text", "text": text})
    return {
        "id":          f"msg_{int(time.time()*1000)}",
        "type":        "message",
        "role":        "assistant",
        "content":     content,
        "model":       model,
        "stop_reason": stop_reason,
        "usage":       {"input_tokens": 0, "output_tokens": 0},
    }

# ─────────────────────────────────────────────
# OPENAI NON-STREAM RESPONSE ← текст
# ─────────────────────────────────────────────
def to_openai_chat_response(text: str, model: str) -> dict:
    """Собирает OpenAI chat.completion ответ из сырого текста модели."""
    tools, text_before = extract_tools(text)
    chat_id   = f"chatcmpl-{uuid.uuid4().hex[:16]}"
    created   = int(time.time())
    if not tools:
        choice = {
            "index":         0,
            "message":       {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }
    else:
        tool_calls = [{
            "id":       f"call_{uuid.uuid4().hex[:8]}",
            "type":     "function",
            "function": {
                "name":      t["name"],
                "arguments": json.dumps(t["input"], ensure_ascii=False),
            },
        } for t in tools]
        choice = {
            "index":         0,
            "message":       {"role": "assistant", "content": text_before or None, "tool_calls": tool_calls},
            "finish_reason": "tool_calls",
        }
    return {
        "id":      chat_id,
        "object":  "chat.completion",
        "created": created,
        "model":   model,
        "choices": [choice],
        "usage":   {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }

# ─────────────────────────────────────────────
# SSE СТРИМИНГ (Anthropic формат наружу)
# ─────────────────────────────────────────────
_TOOL_FENCE_START = re.compile(
    r"```(?:json)?\s*\n?\s*[\{\[]"
    r"|\[\s*\n\s*\{",
    re.IGNORECASE
)

async def stream_sse(
    resp: aiohttp.ClientResponse,
    model: str,
    original_body: dict,
    is_sub: bool = False,
) -> AsyncGenerator[str, None]:
    msg_id = f"msg_{int(time.time()*1000)}"
    yield (
        f"event: message_start\ndata: {json.dumps({'type':'message_start','message':{'id':msg_id,'type':'message','role':'assistant','content':[],'model':model,'stop_reason':None,'usage':{'input_tokens':0,'output_tokens':0}}})}\n\n"
        f"event: content_block_start\ndata: {json.dumps({'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}})}\n\n"
    )
    full_text   = ""
    pending_buf = ""
    tool_started = False
    try:
        async for raw in resp.content:
            line = raw.decode("utf-8", errors="ignore").strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            try:
                chunk = json.loads(line[6:])
                delta = chunk["choices"][0].get("delta", {})
                piece = delta.get("content") or ""
                if not piece: continue
                full_text += piece
                if tool_started:
                    pending_buf += piece
                    continue
                pending_buf += piece
                m = _TOOL_FENCE_START.search(pending_buf)
                if m:
                    tool_started = True
                    safe_text = pending_buf[:m.start()].rstrip()
                    if safe_text:
                        yield (
                            f"event: content_block_delta\ndata: "
                            f"{json.dumps({'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':safe_text}})}\n\n"
                        )
                    pending_buf = ""
                else:
                    if len(pending_buf) > 10:
                        safe_text   = pending_buf[:-10]
                        pending_buf = pending_buf[-10:]
                        yield (
                            f"event: content_block_delta\ndata: "
                            f"{json.dumps({'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':safe_text}})}\n\n"
                        )
            except Exception:
                pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.debug(f"[STREAM] обрыв: {e}")

    tools, text_before = extract_tools(full_text)

    # RETRY: если тул начался но JSON обрезан
    if tool_started and not tools:
        log.warning(f"[STREAM] JSON тула обрезан ({len(full_text)} симв), retry non-stream…")
        retry_body         = build_onlysq_body(original_body, model)
        retry_body["stream"] = False
        retry_resp, _      = await call_onlysq(retry_body, is_sub)
        if retry_resp is not None:
            try:
                data      = await retry_resp.json()
                full_text = data["choices"][0]["message"].get("content", "")
                tools, text_before = extract_tools(full_text)
                log.info(f"[STREAM] Retry успешен, тулов: {len(tools)}")
            except Exception as e:
                log.error(f"[STREAM] Retry ошибка: {e}")
            finally:
                retry_resp.release()

    stop_reason = "tool_use" if tools else "end_turn"
    first_tool  = tools[0] if tools else None

    if not tools and pending_buf:
        yield (
            f"event: content_block_delta\ndata: "
            f"{json.dumps({'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':pending_buf}})}\n\n"
        )

    yield f"event: content_block_stop\ndata: {json.dumps({'type':'content_block_stop','index':0})}\n\n"

    if len(tools) > 1 and first_tool:
        state                = LoopState()
        state.base_messages  = original_body.get("messages", []).copy()
        state.text_before    = text_before
        state.all_tools      = tools
        state.pending        = tools[1:]
        async with _loops_lock:
            _loops[first_tool["id"]] = state
        log.info(f"[LOOP] Создан loop {first_tool['id']}, {len(tools)} тулов")

    if first_tool:
        idx = 1
        yield (
            f"event: content_block_start\ndata: "
            f"{json.dumps({'type':'content_block_start','index':idx,'content_block':{'type':'tool_use','id':first_tool['id'],'name':first_tool['name'],'input':{}}})}\n\n"
            f"event: content_block_delta\ndata: "
            f"{json.dumps({'type':'content_block_delta','index':idx,'delta':{'type':'input_json_delta','partial_json':json.dumps(first_tool['input'])}})}\n\n"
            f"event: content_block_stop\ndata: "
            f"{json.dumps({'type':'content_block_stop','index':idx})}\n\n"
        )

    yield (
        f"event: message_delta\ndata: {json.dumps({'type':'message_delta','delta':{'stop_reason':stop_reason},'usage':{'output_tokens':10}})}\n\n"
        f"event: message_stop\ndata: {json.dumps({'type':'message_stop'})}\n\n"
    )


# ─────────────────────────────────────────────
# SSE СТРИМИНГ для OpenAI /v1/chat/completions
# ─────────────────────────────────────────────
async def stream_sse_openai_legacy(
    resp: aiohttp.ClientResponse,
    model: str,
) -> AsyncGenerator[str, None]:
    """Legacy: читает полный JSON от OnlySQ (stream=False), отдаёт чанками по 400."""
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
    created = int(time.time())

    def sse(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    yield sse({"id": chat_id, "object": "chat.completion.chunk", "created": created,
               "model": model, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})

    try:
        raw_data = await resp.json(content_type=None)
        full_text = raw_data["choices"][0]["message"].get("content", "") or ""
    except Exception as e:
        log.error(f"[STREAM_OAI] Ошибка чтения JSON от OnlySQ: {e}")
        yield sse({"id": chat_id, "object": "chat.completion.chunk", "created": created,
                   "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        yield "data: [DONE]\n\n"
        return

    tools, text_before = extract_tools(full_text)
    log.info(f"[STREAM_OAI] текст={len(full_text)} симв | тулов={len(tools)}")

    if not tools:
        # Стримим текст чанками
        for i in range(0, max(len(full_text), 1), 400):
            piece = full_text[i:i + 400]
            if piece:
                yield sse({"id": chat_id, "object": "chat.completion.chunk", "created": created,
                           "model": model, "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]})
        yield sse({"id": chat_id, "object": "chat.completion.chunk", "created": created,
                   "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    else:
        if text_before:
            yield sse({"id": chat_id, "object": "chat.completion.chunk", "created": created,
                       "model": model, "choices": [{"index": 0, "delta": {"content": text_before}, "finish_reason": None}]})
        tool_calls = [{
            "index":    idx,
            "id":       f"call_{uuid.uuid4().hex[:8]}",
            "type":     "function",
            "function": {
                "name":      t["name"],
                "arguments": json.dumps(t["input"], ensure_ascii=False),
            },
        } for idx, t in enumerate(tools)]
        yield sse({"id": chat_id, "object": "chat.completion.chunk", "created": created,
                   "model": model, "choices": [{"index": 0, "delta": {"tool_calls": tool_calls}, "finish_reason": "tool_calls"}]})

    yield "data: [DONE]\n\n"


async def stream_sse_openai_realtime(
    resp: aiohttp.ClientResponse,
    model: str,
    onlysq_body: dict,
    is_sub: bool,
) -> AsyncGenerator[str, None]:
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
    created = int(time.time())

    def sse(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    yield sse({"id": chat_id, "object": "chat.completion.chunk", "created": created,
               "model": model, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})

    full_text = ""
    pending_buf = ""
    tool_started = False

    try:
        async for raw in resp.content:
            line = raw.decode("utf-8", errors="ignore").strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            try:
                chunk = json.loads(line[6:])
                delta = chunk["choices"][0].get("delta", {})
                piece = delta.get("content") or ""
                if not piece:
                    continue
                full_text += piece
                if tool_started:
                    pending_buf += piece
                    continue
                pending_buf += piece
                m = _TOOL_FENCE_START.search(pending_buf)
                if m:
                    tool_started = True
                    safe_text = pending_buf[:m.start()].rstrip()
                    if safe_text:
                        yield sse({"id": chat_id, "object": "chat.completion.chunk", "created": created,
                                   "model": model, "choices": [{"index": 0, "delta": {"content": safe_text}, "finish_reason": None}]})
                    pending_buf = ""
                else:
                    if len(pending_buf) > 10:
                        safe_text = pending_buf[:-10]
                        pending_buf = pending_buf[-10:]
                        yield sse({"id": chat_id, "object": "chat.completion.chunk", "created": created,
                                   "model": model, "choices": [{"index": 0, "delta": {"content": safe_text}, "finish_reason": None}]})
            except Exception:
                pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.debug(f"[STREAM_OAI_RT] обрыв: {e}")

    tools, text_before = extract_tools(full_text)

    if tool_started and not tools:
        log.warning(f"[STREAM_OAI_RT] JSON тула обрезан ({len(full_text)} симв), retry non-stream...")
        retry_body = dict(onlysq_body)
        retry_body["stream"] = False
        retry_resp, _ = await call_onlysq(retry_body, is_sub)
        if retry_resp is not None:
            try:
                data = await retry_resp.json()
                full_text = data["choices"][0]["message"].get("content", "")
                tools, text_before = extract_tools(full_text)
                log.info(f"[STREAM_OAI_RT] retry успешен, тулов: {len(tools)}")
            except Exception as e:
                log.error(f"[STREAM_OAI_RT] retry ошибка: {e}")
            finally:
                retry_resp.release()

    if not tools:
        if pending_buf:
            yield sse({"id": chat_id, "object": "chat.completion.chunk", "created": created,
                       "model": model, "choices": [{"index": 0, "delta": {"content": pending_buf}, "finish_reason": None}]})
        yield sse({"id": chat_id, "object": "chat.completion.chunk", "created": created,
                   "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    else:
        if text_before:
            yield sse({"id": chat_id, "object": "chat.completion.chunk", "created": created,
                       "model": model, "choices": [{"index": 0, "delta": {"content": text_before}, "finish_reason": None}]})
        tool_calls = [{
            "index":    idx,
            "id":       f"call_{uuid.uuid4().hex[:8]}",
            "type":     "function",
            "function": {
                "name":      t["name"],
                "arguments": json.dumps(t["input"], ensure_ascii=False),
            },
        } for idx, t in enumerate(tools)]
        yield sse({"id": chat_id, "object": "chat.completion.chunk", "created": created,
                   "model": model, "choices": [{"index": 0, "delta": {"tool_calls": tool_calls}, "finish_reason": "tool_calls"}]})

    yield "data: [DONE]\n\n"


async def stream_sse_openai(
    resp: aiohttp.ClientResponse,
    model: str,
    onlysq_body: dict,
    is_sub: bool,
) -> AsyncGenerator[str, None]:
    if _read_stream_mode() == "legacy":
        async for c in stream_sse_openai_legacy(resp, model):
            yield c
    else:
        async for c in stream_sse_openai_realtime(resp, model, onlysq_body, is_sub):
            yield c


# ─────────────────────────────────────────────
# FAKE TOOL SSE (VirtualLoop)
# ─────────────────────────────────────────────
def fake_tool_sse(tool: dict, model: str) -> AsyncGenerator[str, None]:
    async def _gen():
        msg_id = f"msg_{int(time.time()*1000)}"
        yield (
            f"event: message_start\ndata: {json.dumps({'type':'message_start','message':{'id':msg_id,'type':'message','role':'assistant','content':[],'model':model,'stop_reason':None,'usage':{'input_tokens':0,'output_tokens':0}}})}\n\n"
            f"event: content_block_start\ndata: {json.dumps({'type':'content_block_start','index':0,'content_block':{'type':'tool_use','id':tool['id'],'name':tool['name'],'input':{}}})}\n\n"
            f"event: content_block_delta\ndata: {json.dumps({'type':'content_block_delta','index':0,'delta':{'type':'input_json_delta','partial_json':json.dumps(tool['input'])}})}\n\n"
            f"event: content_block_stop\ndata: {json.dumps({'type':'content_block_stop','index':0})}\n\n"
            f"event: message_delta\ndata: {json.dumps({'type':'message_delta','delta':{'stop_reason':'tool_use'},'usage':{'output_tokens':10}})}\n\n"
            f"event: message_stop\ndata: {json.dumps({'type':'message_stop'})}\n\n"
        )
    return _gen()

# ─────────────────────────────────────────────
# ОПРЕДЕЛЕНИЕ ТИПА АГЕНТА
# ─────────────────────────────────────────────
def detect_agent_type(body: dict) -> tuple[bool, str]:
    tool_names = {t.get("name") for t in body.get("tools", [])}
    if "Agent" in tool_names:
        return False, "MAIN"
    if "Generate a concise, sentence-case title" in str(body):
        return True, "TITLE"
    if "task" in tool_names or "Task" in tool_names:
        return False, "MAIN"
    if tool_names:
        return True, "SUBAGENT"
    return True, "SUBAGENT"

# ─────────────────────────────────────────────
# HTTP КЛИЕНТ
# ─────────────────────────────────────────────
_session: aiohttp.ClientSession | None = None
ONLYSQ_MODELS_URL = "https://api.onlysq.ru/ai/models"


async def fetch_models_from_onlysq(session: aiohttp.ClientSession, api_key: str) -> list:
    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        async with session.get(ONLYSQ_MODELS_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, dict) and "models" in data:
                    return [{"id": mid, **info} for mid, info in data["models"].items()]
                elif isinstance(data, list):
                    return data
                elif isinstance(data, dict) and "data" in data:
                    return data["data"]
    except Exception as e:
        log.warning(f"[MODELS] Ошибка: {e}")
    return []


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global _session
    connector = aiohttp.TCPConnector(limit_per_host=15)
    _session  = aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT))
    bridge_key = _read_onlysq_key()
    if not config.is_cache_valid() and bridge_key:
        log.info("[MODELS] fetching from OnlySQ")
        models = await fetch_models_from_onlysq(_session, bridge_key)
        if models: config.set_models_cache(models)
    else:
        log.info(f"[MODELS] Используем кэш: {len(config.get_models_cache())} моделей")
    yield
    await _session.close()


app = FastAPI(lifespan=lifespan)


async def call_onlysq(openai_body: dict, is_sub: bool, max_retries: int = 10):
    for attempt in range(max_retries):
        key     = await pool.acquire(is_sub)
        if not key:
            log.warning("[API] no OnlySQ key configured")
            return None, None
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        log.info(f"[API] attempt {attempt+1}/{max_retries}, key ...{key[-6:]}")
        try:
            resp = await _session.post(ONLYSQ_URL, json=openai_body, headers=headers)
        except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError,
                aiohttp.ClientConnectorError, asyncio.TimeoutError) as e:
            log.warning(f"[API] Сетевая ошибка ({attempt+1}): {e}")
            await asyncio.sleep(min(2 ** attempt, 10))
            continue
        if resp.status == 200:
            return resp, key
        err = await resp.text()
        resp.release()
        log.warning(f"[API] Статус {resp.status}: {err[:300]}")
        if resp.status == 429:
            await pool.ban(key, is_sub); continue
        if resp.status >= 500:
            await asyncio.sleep(1); continue
        return None, key
    return None, None

# ─────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────
@app.head("/")
@app.get("/")
async def health_check():
    return JSONResponse({"status": "ok"})


@app.get("/v1/models")
async def list_models():
    models_cache = config.get_models_cache()
    result = [{"id": m.get("id") or m.get("name") or str(m), "object": "model",
                "created": int(time.time()), "owned_by": "onlysq"} for m in models_cache]
    existing = {m["id"] for m in result}
    for cm in [config.get_main_model(), config.get_sub_model(), config.get_vision_model()]:
        if cm not in existing:
            result.append({"id": cm, "object": "model", "created": int(time.time()), "owned_by": "onlysq"})
    return JSONResponse({"data": result, "object": "list"})


@app.get("/config")
async def get_config():
    has_key = bool(_read_onlysq_key())
    return JSONResponse({
        "main_model":   config.get_main_model(),
        "sub_model":    config.get_sub_model(),
        "vision_model": config.get_vision_model(),
        "vision_support": config.data.get("vision_support", {}),
        "models_cached": len(config.get_models_cache()),
        "has_key": has_key,
    })


@app.post("/config")
async def set_config(request: Request):
    body = await request.json(); errors = []
    if "onlysq_key" in body:
        _write_onlysq_key(str(body["onlysq_key"]))
        pool.reload_key()
    if "main_model"   in body and not config.set_main_model(body["main_model"]):   errors.append(f"main_model '{body['main_model']}' not found")
    if "sub_model"    in body and not config.set_sub_model(body["sub_model"]):     errors.append(f"sub_model '{body['sub_model']}' not found")
    if "vision_model" in body and not config.set_vision_model(body["vision_model"]): errors.append(f"vision_model '{body['vision_model']}' not found")
    result = {"status": "error" if errors else "ok",
              "config": {"main_model": config.get_main_model(), "sub_model": config.get_sub_model(), "vision_model": config.get_vision_model()}}
    if errors: result["errors"] = errors
    return JSONResponse(result, status_code=400 if errors else 200)


@app.post("/v1/messages/count_tokens")
async def count_tokens(_: Request):
    return JSONResponse({"input_tokens": 150})


# ─── /v1/messages  (Claude Code / Anthropic формат) ────────────────────────
@app.post("/v1/messages")
async def messages(request: Request):
    _gc_loops()
    body      = await request.json()
    is_stream = body.get("stream", False)
    is_sub, label = detect_agent_type(body)
    model     = config.get_sub_model() if is_sub else config.get_main_model()
    body["model"] = model
    log.info(f"\n{'='*50}\n[REQ] {label} | stream={is_stream} | model={model}")

    # Virtual Loop
    if body.get("messages"):
        last = body["messages"][-1]
        loop_id = state = None
        if last["role"] == "user" and isinstance(last.get("content"), list):
            async with _loops_lock:
                for block in last["content"]:
                    tid = block.get("tool_use_id")
                    if block.get("type") == "tool_result" and tid in _loops:
                        loop_id = tid; state = _loops[tid]; break

        if loop_id and state:
            for block in last["content"]:
                state.results.append(block)
            if state.pending:
                next_tool = state.pending.pop(0)
                async with _loops_lock:
                    _loops[next_tool["id"]] = state
                    del _loops[loop_id]
                log.info(f"[LOOP] Выдаём {next_tool['name']}, осталось {len(state.pending)}")
                if is_stream:
                    return StreamingResponse(fake_tool_sse(next_tool, model), media_type="text/event-stream")
                return JSONResponse(to_anthropic_response(
                    f'```json\n{{"name":"{next_tool["name"]}","arguments":{json.dumps(next_tool["input"])}}}\n```', model))
            else:
                log.info(f"[LOOP] Очередь {loop_id} закрыта, идём в API")
                assistant_content = (
                    [{"type": "text", "text": state.text_before}] if state.text_before else []
                ) + state.all_tools
                body["messages"] = state.base_messages + [
                    {"role": "assistant", "content": assistant_content},
                    {"role": "user",      "content": state.results},
                ]
                async with _loops_lock:
                    del _loops[loop_id]

    openai_body = build_onlysq_body(body, model)
    log.info(f"[ONLYSQ] model={model} | stream={is_stream} | tools={len(body.get('tools',[]))}")
    resp, key = await call_onlysq(openai_body, is_sub)
    if resp is None:
        err_msg = "OnlySQ недоступен"
        if is_stream:
            async def err_sse():
                r = to_anthropic_response(err_msg, model)
                yield f"event: message_start\ndata: {json.dumps({'type':'message_start','message':r})}\n\n"
                yield f"event: message_stop\ndata: {json.dumps({'type':'message_stop'})}\n\n"
            return StreamingResponse(err_sse(), media_type="text/event-stream")
        return JSONResponse(to_anthropic_response(err_msg, model), status_code=503)

    if is_stream:
        async def gen():
            try:
                async for chunk in stream_sse(resp, model, body, is_sub):
                    yield chunk
            except asyncio.CancelledError:
                pass
            finally:
                resp.release()
        return StreamingResponse(gen(), media_type="text/event-stream")
    else:
        try:
            data = await resp.json()
            text = data["choices"][0]["message"].get("content", "")
            return JSONResponse(to_anthropic_response(text, model))
        except Exception as e:
            log.error(f"[MESSAGES] Ошибка парсинга: {e}")
            return JSONResponse(to_anthropic_response("Ошибка парсинга ответа OnlySQ", model))
        finally:
            resp.release()


# ─── /v1/chat/completions  (OpenCode / OpenAI формат) ──────────────────────
@app.get("/v1/chat/completions")
@app.post("/v1/chat/completions")
async def openai_chat_completions(request: Request):
    if request.method == "GET":
        return JSONResponse({"object": "list", "data": []})

    openai_body_in = await request.json()
    is_stream      = bool(openai_body_in.get("stream", False))
    requested_model = openai_body_in.get("model") or config.get_main_model()

    # Конвертируем OpenAI → Anthropic внутренний формат
    anthropic_msgs, system_text = openai_messages_to_anthropic(openai_body_in.get("messages", []))

    # Извлекаем тулы (OpenAI формат function tools)
    openai_tools = openai_body_in.get("tools", [])
    # Конвертируем в Anthropic tool формат (для build_tools_system)
    anthropic_tools = []
    for t in openai_tools:
        if t.get("type") == "function":
            fn = t.get("function", {})
            anthropic_tools.append({
                "name":         fn.get("name", ""),
                "description":  fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })

    anthropic_body = {
        "model":    requested_model,
        "messages": anthropic_msgs,
        "system":   system_text,
        "tools":    anthropic_tools,
        "stream":   is_stream,
    }
    if "max_tokens" in openai_body_in:
        anthropic_body["max_tokens"] = openai_body_in["max_tokens"]

    is_sub, label = detect_agent_type(anthropic_body)
    model = config.get_sub_model() if is_sub else config.get_main_model()
    anthropic_body["model"] = model

    log.info(f"\n[OPENAI] model={model} | agent={label} | stream={is_stream} | tools={len(anthropic_tools)}")

    onlysq_body = build_onlysq_body(anthropic_body, model)
    if is_stream and _read_stream_mode() == "realtime":
        onlysq_body["stream"] = True
    log.info(f"[ONLYSQ] messages: {len(onlysq_body['messages'])} | stream_to_onlysq={onlysq_body.get('stream')}")

    prompt_tok = tokens_from_messages(onlysq_body.get("messages"))
    started = time.time()

    resp, key = await call_onlysq(onlysq_body, is_sub)
    if resp is None:
        log_request(source="opencode", model=model, prompt_tokens=prompt_tok, completion_tokens=0,
                    latency_ms=int((time.time() - started) * 1000), status="error", error="OnlySQ unavailable")
        return JSONResponse({"error": {"message": "OnlySQ unavailable", "type": "server_error"}}, status_code=503)

    if is_stream:
        captured: list[str] = []
        async def gen():
            try:
                async for chunk in stream_sse_openai(resp, model, onlysq_body, is_sub):
                    captured.append(chunk)
                    yield chunk
            except asyncio.CancelledError:
                pass
            finally:
                resp.release()
                completion_tok = _count_tokens("".join(captured))
                log_request(source="opencode", model=model, prompt_tokens=prompt_tok,
                            completion_tokens=completion_tok,
                            latency_ms=int((time.time() - started) * 1000), status="ok")
        return StreamingResponse(gen(), media_type="text/event-stream")
    else:
        try:
            data = await resp.json()
            text = data["choices"][0]["message"].get("content", "")
            log_request(source="opencode", model=model, prompt_tokens=prompt_tok,
                        completion_tokens=_count_tokens(text),
                        latency_ms=int((time.time() - started) * 1000), status="ok")
            return JSONResponse(to_openai_chat_response(text, model))
        except Exception as e:
            log.error(f"[OPENAI] parse error: {e}")
            log_request(source="opencode", model=model, prompt_tokens=prompt_tok, completion_tokens=0,
                        latency_ms=int((time.time() - started) * 1000), status="error", error=str(e)[:200])
            return JSONResponse({"error": {"message": str(e), "type": "server_error"}}, status_code=500)
        finally:
            resp.release()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=7778, log_level="info")
