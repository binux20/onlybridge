"""
OnlySQ → Anthropic API Proxy
Транслирует запросы Claude Code (Anthropic API формат) в OpenAI-совместимый
формат OnlySQ, эмулируя tool use через промпт-инжекцию JSON-блоков.

Запуск: uvicorn proxy_onlysqstream:app --host 127.0.0.1 --port 7777
Использование: ANTHROPIC_BASE_URL=http://127.0.0.1:7777 ANTHROPIC_API_KEY=fake claude
"""

import asyncio
import json
import html

def unescape_recursive(obj):
    """Recursively unescape HTML entities in strings within dicts/lists."""
    if isinstance(obj, str):
        return html.unescape(obj)
    elif isinstance(obj, dict):
        return {k: unescape_recursive(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [unescape_recursive(item) for item in obj]
    return obj

import re
import time
import logging
import uuid
from collections import deque
from typing import AsyncGenerator
import contextlib
from pathlib import Path

import aiohttp
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from backend.proxies._stats_db import log_request, tokens_from_messages, count_tokens as _count_tokens

# ─────────────────────────────────────────────
#  КОНФИГУРАЦИЯ
# ─────────────────────────────────────────────
ONLYSQ_URL = "https://api.onlysq.ru/ai/openai/chat/completions"

_BRIDGE_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "config.json"


def _read_onlysq_key() -> str:
    try:
        with open(_BRIDGE_CONFIG_PATH, "r", encoding="utf-8") as f:
            return (json.load(f).get("onlysq_key") or "").strip()
    except (OSError, json.JSONDecodeError):
        return ""


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
        logging.getLogger("proxy").error(f"[CONFIG] cannot save onlysq_key: {e}")

# Главный агент (запросы с инструментами Skills от Claude Code)
MAIN_MODEL   = "claude-opus-4-5"
MAIN_RPM     = 3

# Субагенты / воркеры (короткие вспомогательные запросы)
SUB_MODEL    = "claude-haiku-4-5"
SUB_RPM      = 10
SUB_KEY_COUNT = 1   # сколько ключей из конца пула отдавать субагентам

WINDOW_SEC   = 60   # скользящее окно RPM
SOFT_BAN_SEC = 25   # пауза ключа после 429

# Таймаут для http-запросов к OnlySQ
HTTP_TIMEOUT = 300

# TTL для записей VirtualLoop (сек). Устаревшие удаляются при каждом запросе.
LOOP_TTL_SEC = 120

# Vision модель для обработки изображений (Gemini поддерживает vision на OnlySQ)
# Используется если основная модель не поддерживает изображения
VISION_MODEL = "gemini-2.5-pro"
VISION_RPM = 10  # отдельный лимит, не тратит Claude RPM

# Файл конфигурации (персистентность между перезапусками)
CONFIG_FILE = Path(__file__).parent / "proxy_config.json"
MODELS_CACHE_TTL = 86400  # 24 часа

# ─────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("proxy")


# ─────────────────────────────────────────────
#  КОНФИГУРАЦИЯ С ПЕРСИСТЕНТНОСТЬЮ
# ─────────────────────────────────────────────
class ProxyConfig:
    """Управляет настройками прокси с сохранением в файл."""

    def __init__(self, path: Path):
        self.path = path
        self.data = self._load()

    def _default(self) -> dict:
        return {
            "models_cache": [],
            "models_cache_ts": 0,
            "user_preferences": {
                "main_model": MAIN_MODEL,
                "sub_model": SUB_MODEL,
                "vision_model": VISION_MODEL,
            },
            "vision_support": {},  # model_name -> True/False/None
        }

    def _load(self) -> dict:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Мержим с дефолтами на случай новых полей
                default = self._default()
                for key in default:
                    if key not in data:
                        data[key] = default[key]
                return data
            except Exception as e:
                log.warning(f"[CONFIG] Ошибка загрузки конфига: {e}, используем дефолт")
        return self._default()

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log.error(f"[CONFIG] Ошибка сохранения конфига: {e}")

    # ── Модели ─────────────────────────────────────────────────────────────────
    def get_main_model(self) -> str:
        return self.data["user_preferences"].get("main_model", MAIN_MODEL)

    def get_sub_model(self) -> str:
        return self.data["user_preferences"].get("sub_model", SUB_MODEL)

    def get_vision_model(self) -> str:
        return self.data["user_preferences"].get("vision_model", VISION_MODEL)

    def _is_model_valid(self, model: str) -> bool:
        """Проверяет что модель есть в кэше OnlySQ."""
        if not self.data.get("models_cache"):
            return True  # Нет кэша — пропускаем валидацию
        valid_ids = {m.get("id") for m in self.data["models_cache"]}
        return model in valid_ids

    def set_main_model(self, model: str) -> bool:
        if not self._is_model_valid(model):
            log.warning(f"[CONFIG] Модель {model} не найдена в OnlySQ")
            return False
        self.data["user_preferences"]["main_model"] = model
        self._save()
        log.info(f"[CONFIG] Main model изменён на: {model}")
        return True

    def set_sub_model(self, model: str) -> bool:
        if not self._is_model_valid(model):
            log.warning(f"[CONFIG] Модель {model} не найдена в OnlySQ")
            return False
        self.data["user_preferences"]["sub_model"] = model
        self._save()
        log.info(f"[CONFIG] Sub model изменён на: {model}")
        return True

    def set_vision_model(self, model: str) -> bool:
        if not self._is_model_valid(model):
            log.warning(f"[CONFIG] Модель {model} не найдена в OnlySQ")
            return False
        self.data["user_preferences"]["vision_model"] = model
        self._save()
        log.info(f"[CONFIG] Vision model изменён на: {model}")
        return True

    # ── Vision support tracking ────────────────────────────────────────────────
    def is_vision_capable(self, model: str) -> bool | None:
        """Возвращает True/False если известно, None если не проверялось."""
        return self.data["vision_support"].get(model)

    def set_vision_capable(self, model: str, capable: bool):
        """Запоминает поддержку vision для модели."""
        self.data["vision_support"][model] = capable
        self._save()
        status = "поддерживает" if capable else "НЕ поддерживает"
        log.info(f"[CONFIG] Модель {model} {status} vision")

    # ── Кэш моделей ────────────────────────────────────────────────────────────
    def get_models_cache(self) -> list:
        return self.data.get("models_cache", [])

    def is_cache_valid(self) -> bool:
        ts = self.data.get("models_cache_ts", 0)
        return (time.time() - ts) < MODELS_CACHE_TTL and len(self.data.get("models_cache", [])) > 0

    def set_models_cache(self, models: list):
        self.data["models_cache"] = models
        self.data["models_cache_ts"] = time.time()
        self._save()
        log.info(f"[CONFIG] Кэш моделей обновлён: {len(models)} моделей")


# Глобальный экземпляр конфига
config = ProxyConfig(CONFIG_FILE)


# ─────────────────────────────────────────────
#  RATE LIMITER (single key from OnlyBridge config)
# ─────────────────────────────────────────────
class KeyPool:
    def __init__(self, main_rpm: int, sub_rpm: int):
        self.main_rpm = main_rpm
        self.sub_rpm = sub_rpm
        self._ts_main: deque = deque()
        self._ts_sub: deque = deque()
        self._ban_until: float = 0.0
        self._lock = asyncio.Lock()
        self._key_cached: str = _read_onlysq_key()

    def reload_key(self) -> None:
        self._key_cached = _read_onlysq_key()

    def current_key(self) -> str:
        return self._key_cached or _read_onlysq_key()

    def _evict(self, now: float):
        while self._ts_main and now - self._ts_main[0] >= WINDOW_SEC:
            self._ts_main.popleft()
        while self._ts_sub and now - self._ts_sub[0] >= WINDOW_SEC:
            self._ts_sub.popleft()

    async def acquire(self, is_sub: bool) -> str | None:
        while True:
            async with self._lock:
                key = self.current_key()
                if not key:
                    return None
                now = time.monotonic()
                self._evict(now)
                ts = self._ts_sub if is_sub else self._ts_main
                rpm = self.sub_rpm if is_sub else self.main_rpm
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
#  VIRTUAL LOOP  (очередь параллельных тулов)
# ─────────────────────────────────────────────
class LoopState:
    __slots__ = ("base_messages", "text_before", "all_tools",
                 "pending", "results", "created_at")

    def __init__(self):
        self.base_messages: list  = []
        self.text_before:   str   = ""
        self.all_tools:     list  = []
        self.pending:       list  = []
        self.results:       list  = []
        self.created_at:    float = time.monotonic()


_loops: dict[str, LoopState] = {}
_loops_lock = asyncio.Lock()


def _gc_loops():
    """Удаляем устаревшие записи, чтобы не было утечки памяти."""
    now = time.monotonic()
    dead = [k for k, v in _loops.items() if now - v.created_at > LOOP_TTL_SEC]
    for k in dead:
        del _loops[k]
        log.debug(f"[LOOP] GC удалил зависший loop {k}")


# ─────────────────────────────────────────────
#  JSON REPAIR
# ─────────────────────────────────────────────
_REPAIR_SUFFIXES = ('"', '}', '"}', '}}', '"}}', '"]}', '"]}}', '"}]')

def repair_json(s: str) -> dict | None:
    s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    # Попытка починить обрезанный JSON
    for suf in _REPAIR_SUFFIXES:
        try:
            return json.loads(s + suf)
        except Exception:
            pass
    # Последний шанс — вырезаем всё после последней закрывающей скобки
    idx = s.rfind("}")
    if idx > 0:
        try:
            return json.loads(s[: idx + 1])
        except Exception:
            pass
    return None


# ─────────────────────────────────────────────
#  ОБРАБОТКА ИЗОБРАЖЕНИЙ
# ─────────────────────────────────────────────

def extract_images_from_messages(messages: list) -> list[dict]:
    """Извлекает все изображения из сообщений Anthropic формата."""
    images = []
    for msg in messages:
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image":
                    source = block.get("source", {})
                    if source.get("type") == "base64" and source.get("data"):
                        images.append({
                            "media_type": source.get("media_type", "image/jpeg"),
                            "data": source.get("data"),
                        })
    return images


def has_images_in_body(body: dict) -> bool:
    """Проверяет есть ли изображения в последнем user-сообщении."""
    messages = body.get("messages", [])
    # Ищем последнее user-сообщение
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "image":
                        source = block.get("source", {})
                        if source.get("type") == "base64" and source.get("data"):
                            return True
            break
    return False


async def describe_image_with_vision(session: aiohttp.ClientSession, image: dict, api_key: str, vision_model: str = None) -> str:
    """
    Отправляет изображение в vision-модель и получает описание.
    Использует отдельный лимит RPM (не тратит Claude лимит).
    """
    if vision_model is None:
        vision_model = config.get_vision_model()
    image_url = f"data:{image['media_type']};base64,{image['data']}"

    openai_body = {
        "model": vision_model,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Describe this image in detail. Include all visible text, UI elements, code, diagrams, or any other relevant information. Be thorough and precise."
                },
                {
                    "type": "image_url",
                    "image_url": {"url": image_url}
                }
            ]
        }],
        "stream": False
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with session.post(ONLYSQ_URL, json=openai_body, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data["choices"][0]["message"].get("content", "[Image description unavailable]")
            else:
                err = await resp.text()
                log.warning(f"[VISION] Ошибка {resp.status}: {err[:200]}")
                return f"[Image description failed: HTTP {resp.status}]"
    except Exception as e:
        log.error(f"[VISION] Exception: {e}")
        return f"[Image description failed: {e}]"


# ─────────────────────────────────────────────
#  ПАРСИНГ ТУЛОВ ИЗ ТЕКСТА МОДЕЛИ
# ─────────────────────────────────────────────

# Вариант 1: ```json { ... } ```  (одиночный объект в фенсе)
_RE_FENCED_OBJ = re.compile(r"```(?:json)?\s*(\{.*?\})\s*(?:```|$)", re.DOTALL | re.IGNORECASE)
# Вариант 2: ```json [ {...}, {...} ] ```  (массив в фенсе)
_RE_FENCED_ARR = re.compile(r"```(?:json)?\s*(\[.*?\])\s*(?:```|$)", re.DOTALL | re.IGNORECASE)
# Вариант 3: сырой массив без фенса — `[\n  {...},\n  {...}\n]`
_RE_RAW_ARR    = re.compile(r"(\[\s*\{.*?\}\s*\])", re.DOTALL)


def _make_tool_block(data: dict, idx: int) -> dict:
    return {
        "type":  "tool_use",
        "id":    f"toolu_{uuid.uuid4().hex[:16]}",
        "name":  data["name"],
        "input": data.get("arguments", data.get("input", {})),
    }


def _is_valid_tool_call(d: dict) -> bool:
    """Проверяет что dict это валидный tool call, а не просто JSON."""
    if not isinstance(d, dict):
        return False
    # Обязательно должен быть "name"
    if "name" not in d:
        return False
    name = d["name"]
    # name должен быть строкой и выглядеть как имя инструмента (PascalCase или snake_case)
    if not isinstance(name, str) or len(name) < 2:
        return False
    # Исключаем очевидно не-инструменты
    if name.lower() in ("example", "test", "sample", "demo", "model", "type", "object"):
        return False
    # Должен содержать "arguments" или "input" (или быть пустым вызовом)
    # Не должен содержать поля которые явно указывают на не-tool JSON
    suspicious_keys = {"description", "properties", "required", "schema", "title", "version"}
    if suspicious_keys & set(d.keys()):
        return False
    return True


def _parse_tool_items(raw: str) -> list[dict]:
    """Парсит одиночный объект или массив объектов в список tool_use блоков."""
    try:
        items = json.loads(raw)
        if isinstance(items, dict):
            items = [items]
    except Exception:
        item = repair_json(raw)
        items = [item] if item else []
    return [_make_tool_block(d, i) for i, d in enumerate(items) if _is_valid_tool_call(d)]


def extract_tools(text: str) -> tuple[list[dict], str]:
    """
    Возвращает (список tool_use блоков, текст до первого инструмента).
    Поддерживает форматы:
      - ```json { ... } ```
      - ```json [ {...}, {...} ] ```
      - голый [ {...}, {...} ] без фенса
    Если найден Agent — возвращает только его.
    """
    candidates: list[tuple[int, int, str]] = []
    for pat in (_RE_FENCED_OBJ, _RE_FENCED_ARR, _RE_RAW_ARR):
        for m in pat.finditer(text):
            candidates.append((m.start(), m.end(), m.group(1)))

    if not candidates:
        return [], text

    candidates.sort(key=lambda x: x[0])

    # Дедупликация перекрывающихся совпадений
    deduped: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, raw in candidates:
        if start >= last_end:
            deduped.append((start, end, raw))
            last_end = end

    first_start = deduped[0][0]
    text_before = text[:first_start].strip()

    tools: list[dict] = []
    for _, _, raw in deduped:
        for tool in _parse_tool_items(raw):
            if tool["name"] == "Agent":
                return [tool], text_before
            tools.append(tool)

    return tools, text_before


# ─────────────────────────────────────────────
#  СБОРКА СИСТЕМНОГО ПРОМПТА С ТУЛАМИ
# ─────────────────────────────────────────────
_TOOL_PRIORITY = ["Bash", "Write", "Read", "Edit", "LS", "Glob", "Grep",
                  "WebFetch", "WebSearch", "MultiEdit", "NotebookRead", "NotebookEdit"]

# Тулы которые дают доступ к внешним данным — модель ОБЯЗАНА их вызывать
_MUST_USE_TOOLS = {
    "WebSearch": "MUST call this tool to search the internet. NEVER answer search queries from memory.",
    "WebFetch":  "MUST call this tool to fetch a URL. NEVER guess page content.",
    "Bash":      "MUST call this tool to run shell commands. NEVER simulate command output.",
    "Read":      "MUST call this tool to read files. NEVER guess file contents.",
}

# Инструкции по ограничению размера операций (добавляются в системный промпт)
_SIZE_LIMIT_INSTRUCTIONS = """
## File Operation Size Limits - CRITICAL

When writing or editing files, you MUST follow these rules:
- **Write tool**: NEVER write more than 300 lines in a single call.
  If a file needs more than 300 lines — write the first 300, then use additional Write/Edit calls for the rest.
- **Edit tool**: NEVER edit more than 300 lines at once.
  Split large edits into multiple sequential Edit calls (each ≤ 300 lines).
- **Why**: Large single operations may get truncated mid-transfer, corrupting the file silently.

Correct approach for a 600-line file:
1. Write lines 1-300 → first Write call
2. Write lines 301-600 → second Write call (append mode or Edit)

NEVER try to do it all in one call. Multiple small calls > one large broken call.
"""

_HTML_ENTITIES_WARNING = """
## CRITICAL: HTML Entities Display Artifact

Due to proxy architecture, TOOL_RESULT outputs show HTML entities:
- Double quotes appear as &quot;
- Single quotes appear as &#x27; or &apos;
- Less-than appears as &lt;
- Greater-than appears as &gt;
- Ampersand appears as &amp;

IMPORTANT: This is DISPLAY-ONLY. Actual files are CORRECT.

Example - you see:    def hello(): return &quot;Hi&quot;
Real file contains:   def hello(): return "Hi"

RULES:
1. NEVER attempt to fix HTML entities - files are already correct
2. NEVER replace &quot; with double-quote in Edit operations - this BREAKS working files
3. To verify real content, run: py -c "print(open('file.py').read())"
4. To verify code works, just execute it: py file.py
5. Hex dump shows truth: od -c file (0x22 = real quote character)

Why: Proxy converts Anthropic to OpenAI formats. HTML escaping happens in
display layer only. File I/O uses correct bytes (verified via hex dumps).
"""


def build_tools_system(tools: list[dict]) -> str:
    sorted_tools = sorted(
        tools,
        key=lambda t: _TOOL_PRIORITY.index(t["name"]) if t["name"] in _TOOL_PRIORITY else 999,
    )
    lines = [
        "## Tool Use Instructions",
        "You have tools available. To call a tool output a fenced JSON block:",
        "",
        "```json",
        '{"name": "tool_name", "arguments": {"arg1": "value1"}}',
        "```",
        "",
        "CRITICAL Rules:",
        "- Use ONLY ```json blocks. Never use XML <tool_call> or plain text.",
        "- You MAY output multiple ```json blocks in one reply (parallel tools).",
        "- For the 'Agent' tool always include both 'prompt' and 'description' fields.",
        "- Do NOT invent tool names — use only those listed below.",
        "- For tools marked MUST: you are FORBIDDEN from answering without calling them first.",
        "",
        "Available tools:",
    ]
    for t in sorted_tools:
        name   = t["name"]
        desc   = t.get("description", "")
        schema = t.get("input_schema", {})
        props  = schema.get("properties", {})
        req    = schema.get("required", [])
        args_hint = ", ".join(
            f"{k}{'*' if k in req else ''}"
            for k in list(props.keys())[:6]
        )
        must_note = f" ⚠️ {_MUST_USE_TOOLS[name]}" if name in _MUST_USE_TOOLS else ""
        lines.append(f"- **{name}**({args_hint}): {desc[:120]}{must_note}")

    # Добавляем инструкции по размеру операций
    lines.append(_SIZE_LIMIT_INSTRUCTIONS)
    lines.append(_HTML_ENTITIES_WARNING)
    return "\n".join(lines)


# ─────────────────────────────────────────────
#  КОНВЕРТАЦИЯ ANTHROPIC → OPENAI
# ─────────────────────────────────────────────
_BILLING_RE = re.compile(r"x-anthropic-billing-header:[^\n]*\n?", re.IGNORECASE)

_reminder_counter = 0  # глобальный, но только для добавления ремайндера


def flatten_content(content, image_descriptions: dict = None) -> str:
    """
    Превращает Anthropic content (str | list) в плоский текст.
    image_descriptions: словарь {index: description} для замены изображений на их описания.
    """
    if isinstance(content, str):
        return content
    parts: list[str] = []
    image_idx = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        t = block.get("type")
        if t == "text":
            text_content = block.get("text", "")
            # Decode HTML entities from Claude Code
            parts.append(html.unescape(text_content) if isinstance(text_content, str) else text_content)
        elif t == "tool_use":
            inp = json.dumps(block.get("input", {}), ensure_ascii=False)
            parts.append(f'```json\n{{"name": "{block["name"]}", "arguments": {inp}}}\n```')
        elif t == "tool_result":
            c = block.get("content", "")
            # Decode HTML entities that Claude Code may have escaped
            if isinstance(c, str):
                c = html.unescape(c)
            if isinstance(c, list):
                c = "\n".join(
                    html.unescape(b.get("text", "")) for b in c
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            tid = block.get("tool_use_id", "")
            parts.append(f"[TOOL_RESULT id={tid}]\n{c}\n[/TOOL_RESULT]")
        elif t == "image":
            source = block.get("source", {})
            if source.get("type") == "base64" and source.get("data"):
                # Если есть описание от vision модели — используем его
                if image_descriptions and image_idx in image_descriptions:
                    desc = image_descriptions[image_idx]
                    parts.append(f"[IMAGE DESCRIPTION]:\n{desc}\n[/IMAGE DESCRIPTION]")
                else:
                    # Без описания просто помечаем что была картинка
                    parts.append("[image in conversation history]")
                image_idx += 1
            else:
                parts.append("[image omitted]")
    return "\n\n".join(p for p in parts if p)


def convert_content_to_openai_multimodal(content, include_images: bool = True) -> list[dict] | str:
    """
    Конвертирует Anthropic content в OpenAI multimodal формат.
    Возвращает list для multimodal или str если нет изображений.
    """
    if isinstance(content, str):
        return content

    has_images = any(
        isinstance(b, dict) and b.get("type") == "image"
        for b in content if isinstance(b, dict)
    )

    if not has_images or not include_images:
        return flatten_content(content)

    # Multimodal формат
    parts: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        t = block.get("type")
        if t == "text":
            text = block.get("text", "")
            if text:
                parts.append({"type": "text", "text": text})
        elif t == "tool_use":
            inp = json.dumps(block.get("input", {}), ensure_ascii=False)
            parts.append({"type": "text", "text": f'```json\n{{"name": "{block["name"]}", "arguments": {inp}}}\n```'})
        elif t == "tool_result":
            c = block.get("content", "")
            # Decode HTML entities that Claude Code may have escaped
            if isinstance(c, str):
                c = html.unescape(c)
            if isinstance(c, list):
                c = "\n".join(
                    html.unescape(b.get("text", "")) for b in c
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            tid = block.get("tool_use_id", "")
            parts.append({"type": "text", "text": f"[TOOL_RESULT id={tid}]\n{c}\n[/TOOL_RESULT]"})
        elif t == "image":
            source = block.get("source", {})
            if source.get("type") == "base64" and source.get("data"):
                media_type = source.get("media_type", "image/jpeg")
                data = source.get("data")
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{data}"}
                })
            else:
                parts.append({"type": "text", "text": "[image omitted]"})

    return parts if parts else ""


def to_openai_messages(body: dict, image_descriptions: dict = None, use_multimodal: bool = False) -> list[dict]:
    """
    Конвертирует Anthropic messages в OpenAI формат.

    image_descriptions: {msg_idx: {img_idx: description}} — описания изображений от vision модели
    use_multimodal: если True — передаём изображения напрямую в OpenAI multimodal формате
    """
    global _reminder_counter

    system_parts: list[str] = []

    # Системный промпт
    sys = body.get("system", "")
    if isinstance(sys, list):
        sys = " ".join(b.get("text", "") for b in sys if isinstance(b, dict) and b.get("type") == "text")
    if sys:
        # Decode HTML entities from Claude Code
        sys = html.unescape(sys)
        system_parts.append(sys)

    # Тулы → в системный промпт
    tools = body.get("tools", [])
    if tools:
        system_parts.append(build_tools_system(tools))

    # Собираем сообщения
    messages: list[dict] = []

    for msg_idx, msg in enumerate(body.get("messages", [])):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        final_r = role if role in ("user", "assistant", "system") else "user"

        # Получаем описания изображений для этого сообщения
        msg_img_descs = image_descriptions.get(msg_idx, {}) if image_descriptions else {}

        if use_multimodal and final_r == "user":
            # Multimodal: передаём изображения напрямую
            converted = convert_content_to_openai_multimodal(content, include_images=True)
            if isinstance(converted, list):
                # Добавляем reminder в конец если нужно
                _reminder_counter += 1
                if _reminder_counter % 6 == 0:
                    converted.append({"type": "text", "text": "\n\n[Reminder: use ```json blocks for ALL tool calls. No XML!]"})
                messages.append({"role": final_r, "content": converted})
            else:
                text = _BILLING_RE.sub("", converted).strip()
                _reminder_counter += 1
                if _reminder_counter % 6 == 0:
                    text += "\n\n[Reminder: use ```json blocks for ALL tool calls. No XML!]"
                if text:
                    messages.append({"role": final_r, "content": text})
        else:
            # Текстовый режим: изображения заменяются описаниями
            text = flatten_content(content, msg_img_descs)
            text = _BILLING_RE.sub("", text).strip()

            if final_r == "user":
                _reminder_counter += 1
                if _reminder_counter % 6 == 0:
                    text += "\n\n[Reminder: use ```json blocks for ALL tool calls. No XML!]"

            if text:
                messages.append({"role": final_r, "content": text})

    # Вставляем системный промпт в начало первого user-сообщения
    if system_parts:
        sys_text = "\n\n".join(system_parts) + "\n\n"
        if messages and messages[0]["role"] in ("user", "system"):
            first_content = messages[0]["content"]
            if isinstance(first_content, list):
                # Multimodal: вставляем system как первый text block
                messages[0]["content"] = [{"type": "text", "text": sys_text}] + first_content
            else:
                messages[0]["content"] = sys_text + first_content
        elif messages:
            messages.insert(0, {"role": "user", "content": sys_text})
        else:
            messages.append({"role": "user", "content": sys_text})

    return messages


def to_openai_body(body: dict, model: str, image_descriptions: dict = None, use_multimodal: bool = False) -> dict:
    return {
        "model":  model,
        "messages": to_openai_messages(body, image_descriptions, use_multimodal),
        "stream": body.get("stream", False),
    }


# ─────────────────────────────────────────────
#  КОНВЕРТАЦИЯ OPENAI → ANTHROPIC (non-stream)
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
#  SSE СТРИМИНГ
# ─────────────────────────────────────────────

# Маркер начала JSON-блока с тулом (фенс или сырой массив)
_TOOL_FENCE_START = re.compile(
    r"```(?:json)?\s*\n?\s*[\{\[]"   # ```json { или ```json [
    r"|\[\s*\n\s*\{",                  # голый [\n  {
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
                    safe_text = pending_buf[: m.start()].rstrip()
                    if safe_text:
                        yield (
                            f"event: content_block_delta\ndata: "
                            f"{json.dumps({'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':safe_text}})}\n\n"
                        )
                    pending_buf = ""
                else:
                    if len(pending_buf) > 10:
                        safe_text = pending_buf[:-10]
                        yield (
                            f"event: content_block_delta\ndata: "
                            f"{json.dumps({'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':safe_text}})}\n\n"
                        )
                        pending_buf = pending_buf[-10:]

            except Exception:
                pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.debug(f"[STREAM] обрыв соединения: {e}")

    # Парсим тулы из полного текста
    tools, text_before = extract_tools(full_text)

    # ── RETRY: если тул начался но JSON обрезан — повторяем non-stream ────────
    if tool_started and not tools:
        log.warning(f"[STREAM] JSON тула обрезан ({len(full_text)} симв), retry non-stream…")
        openai_body = to_openai_body(original_body, model)
        openai_body["stream"] = False
        retry_resp, _ = await call_onlysq(openai_body, is_sub)
        if retry_resp is not None:
            try:
                data = await retry_resp.json()
                full_text = data["choices"][0]["message"].get("content", "")
                tools, text_before = extract_tools(full_text)
                log.info(f"[STREAM] Retry успешен, тулов: {len(tools)}")
            except Exception as e:
                log.error(f"[STREAM] Retry парсинг ошибка: {e}")
            finally:
                retry_resp.release()
        else:
            log.error("[STREAM] Retry тоже не удался")
    # ─────────────────────────────────────────────────────────────────────────

    stop_reason = "tool_use" if tools else "end_turn"
    first_tool = tools[0] if tools else None

    # Сбрасываем pending_buf если тулов нет
    if not tools and pending_buf:
        yield (
            f"event: content_block_delta\ndata: "
            f"{json.dumps({'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':pending_buf}})}\n\n"
        )

    yield f"event: content_block_stop\ndata: {json.dumps({'type':'content_block_stop','index':0})}\n\n"

    # Если несколько тулов — сохраняем в VirtualLoop
    if len(tools) > 1 and first_tool:
        state = LoopState()
        state.base_messages = original_body.get("messages", []).copy()
        state.text_before   = text_before
        state.all_tools     = tools
        state.pending       = tools[1:]
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


def fake_tool_sse(tool: dict, model: str) -> AsyncGenerator[str, None]:
    """Эмитирует SSE-ответ с одним тулом без реального API-запроса."""
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
#  ОПРЕДЕЛЯЕМ ТИП ЗАПРОСА
# ─────────────────────────────────────────────
def detect_agent_type(body: dict) -> tuple[bool, str]:
    """
    Возвращает (is_subagent, label).
    Главный агент — есть тул 'Agent' в списке (субагент его никогда не получает).
    """
    tool_names = {t.get("name") for t in body.get("tools", [])}
    if "Agent" in tool_names:
        return False, "ГЛАВНЫЙ"

    if "Generate a concise, sentence-case title" in str(body):
        return True, "ЗАГОЛОВКИ"

    return True, "СУБАГЕНТ"


# ─────────────────────────────────────────────
#  HTTP КЛИЕНТ
# ─────────────────────────────────────────────
_session: aiohttp.ClientSession | None = None

ONLYSQ_MODELS_URL = "https://api.onlysq.ru/ai/models"


async def fetch_models_from_onlysq(session: aiohttp.ClientSession, api_key: str) -> list:
    """Загружает список моделей из OnlySQ API."""
    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        async with session.get(ONLYSQ_MODELS_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 200:
                data = await resp.json()
                # OnlySQ возвращает {"api-version": ..., "models": {...}}
                if isinstance(data, dict) and "models" in data:
                    models_dict = data["models"]
                    # Конвертируем dict в list с id
                    return [
                        {"id": model_id, **model_info}
                        for model_id, model_info in models_dict.items()
                    ]
                elif isinstance(data, list):
                    return data
                elif isinstance(data, dict) and "data" in data:
                    return data["data"]
                else:
                    log.warning(f"[MODELS] Неизвестный формат ответа: {list(data.keys()) if isinstance(data, dict) else type(data)}")
                    return []
            else:
                log.warning(f"[MODELS] Ошибка загрузки моделей: HTTP {resp.status}")
                return []
    except Exception as e:
        log.warning(f"[MODELS] Не удалось загрузить модели: {e}")
        return []


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global _session
    connector = aiohttp.TCPConnector(limit_per_host=15)
    _session  = aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
    )

    bridge_key = _read_onlysq_key()
    if not config.is_cache_valid() and bridge_key:
        log.info("[MODELS] fetching from OnlySQ")
        models = await fetch_models_from_onlysq(_session, bridge_key)
        if models:
            config.set_models_cache(models)

    yield
    await _session.close()


# ─────────────────────────────────────────────
#  API
# ─────────────────────────────────────────────
app = FastAPI(lifespan=lifespan)


async def call_onlysq(openai_body: dict, is_sub: bool, max_retries: int = 10):
    for attempt in range(max_retries):
        key = await pool.acquire(is_sub)
        if not key:
            log.warning("[API] no OnlySQ key configured")
            return None, None
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type":  "application/json",
        }
        log.info(f"[API] attempt {attempt+1}/{max_retries}, key ...{key[-6:]}")

        try:
            resp = await _session.post(ONLYSQ_URL, json=openai_body, headers=headers)
        except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError,
                aiohttp.ClientConnectorError, asyncio.TimeoutError) as e:
            log.warning(f"[API] Сетевая ошибка (попытка {attempt+1}): {e}")
            await asyncio.sleep(min(2 ** attempt, 10))  # экспоненциальный backoff
            continue

        if resp.status == 200:
            return resp, key

        err = await resp.text()
        resp.release()
        log.warning(f"[API] Статус {resp.status}: {err[:200]}")

        if resp.status == 429:
            await pool.ban(key, is_sub)
            continue

        if resp.status >= 500:
            # Серверная ошибка OnlySQ — можно retry
            log.warning(f"[API] Серверная ошибка {resp.status}, retry…")
            await asyncio.sleep(1)
            continue

        # 4xx и прочее — сразу возвращаем None
        return None, key

    return None, None


@app.head("/")
@app.get("/")
async def health_check():
    """Health check endpoint для Claude Code."""
    return JSONResponse({"status": "ok"})


@app.get("/v1/models")
async def list_models():
    """Возвращает список доступных моделей в формате Anthropic API."""
    models_cache = config.get_models_cache()

    # Конвертируем в формат Anthropic
    anthropic_models = []
    for m in models_cache:
        model_id = m.get("id") or m.get("name") or str(m)
        anthropic_models.append({
            "id": model_id,
            "object": "model",
            "created": int(time.time()),
            "owned_by": "onlysq",
        })

    # Добавляем текущие выбранные модели если их нет в списке
    current_models = [
        config.get_main_model(),
        config.get_sub_model(),
        config.get_vision_model(),
    ]
    existing_ids = {m["id"] for m in anthropic_models}
    for cm in current_models:
        if cm not in existing_ids:
            anthropic_models.append({
                "id": cm,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "onlysq",
            })

    return JSONResponse({"data": anthropic_models, "object": "list"})


@app.get("/config")
async def get_config():
    return JSONResponse({
        "main_model": config.get_main_model(),
        "sub_model": config.get_sub_model(),
        "vision_model": config.get_vision_model(),
        "vision_support": config.data.get("vision_support", {}),
        "models_cached": len(config.get_models_cache()),
        "has_key": bool(_read_onlysq_key()),
    })


@app.post("/config")
async def set_config(request: Request):
    """Изменяет конфигурацию прокси."""
    body_raw = await request.json()
    # DEBUG: log raw input to see if it has HTML entities
    import json as _json
    _sample = _json.dumps(body_raw, ensure_ascii=False)[:500]
    log.info(f"[DEBUG RAW BODY] {_sample}")
    body = unescape_recursive(body_raw)
    errors = []
    if "onlysq_key" in body:
        _write_onlysq_key(str(body["onlysq_key"]))
        pool.reload_key()
    if "main_model" in body:
        if not config.set_main_model(body["main_model"]):
            errors.append(f"main_model '{body['main_model']}' not found")
    if "sub_model" in body:
        if not config.set_sub_model(body["sub_model"]):
            errors.append(f"sub_model '{body['sub_model']}' not found")
    if "vision_model" in body:
        if not config.set_vision_model(body["vision_model"]):
            errors.append(f"vision_model '{body['vision_model']}' not found")

    result = {
        "status": "error" if errors else "ok",
        "config": {
            "main_model": config.get_main_model(),
            "sub_model": config.get_sub_model(),
            "vision_model": config.get_vision_model(),
        }
    }
    if errors:
        result["errors"] = errors
    return JSONResponse(result, status_code=400 if errors else 200)


@app.post("/v1/messages/count_tokens")
async def count_tokens(_: Request):
    return JSONResponse({"input_tokens": 150})


@app.post("/v1/messages")
async def messages(request: Request):
    _gc_loops()

    body_raw = await request.json()
    _started = time.time()
    _prompt_tok = tokens_from_messages(body_raw.get("messages") or [])
    # DEBUG: log raw input to see if it has HTML entities
    import json as _json
    _sample = _json.dumps(body_raw, ensure_ascii=False)[:500]
    log.info(f"[DEBUG RAW BODY] {_sample}")
    body = unescape_recursive(body_raw)
    is_stream = body.get("stream", False)
    is_sub, label = detect_agent_type(body)
    model = config.get_sub_model() if is_sub else config.get_main_model()
    body["model"] = model

    log.info(f"\n{'='*50}\n[REQ] {label} | stream={is_stream} | model={model}")

    # ── Virtual Loop: перехват tool_result ───────────────────────────────────
    if body.get("messages"):
        last = body["messages"][-1]
        loop_id = None
        state = None

        if last["role"] == "user" and isinstance(last.get("content"), list):
            async with _loops_lock:
                for block in last["content"]:
                    tid = block.get("tool_use_id")
                    if block.get("type") == "tool_result" and tid in _loops:
                        loop_id = tid
                        state = _loops[tid]
                        break

        if loop_id and state:
            # Сохраняем результат
            for block in last["content"]:
                state.results.append(block)

            if state.pending:
                # Выдаём следующий тул из очереди без запроса к API
                next_tool = state.pending.pop(0)
                async with _loops_lock:
                    _loops[next_tool["id"]] = state
                    del _loops[loop_id]
                log.info(f"[LOOP] Выдаём {next_tool['name']}, осталось {len(state.pending)}")

                if is_stream:
                    log_request(source="claude", model=model, prompt_tokens=_prompt_tok, completion_tokens=0,
                                latency_ms=int((time.time() - _started) * 1000), status="ok")
                    return StreamingResponse(fake_tool_sse(next_tool, model), media_type="text/event-stream")
                else:
                    log_request(source="claude", model=model, prompt_tokens=_prompt_tok, completion_tokens=0,
                                latency_ms=int((time.time() - _started) * 1000), status="ok")
                    return JSONResponse(to_anthropic_response(
                        f'```json\n{{"name":"{next_tool["name"]}","arguments":{json.dumps(next_tool["input"])}}}\n```',
                        model
                    ))
            else:
                # Очередь исчерпана — делаем реальный запрос со всем контекстом
                log.info(f"[LOOP] Очередь {loop_id} закрыта, собираем контекст и идём в API")
                assistant_content = (
                    [{"type": "text", "text": state.text_before}] if state.text_before else []
                ) + state.all_tools

                real_messages = state.base_messages + [
                    {"role": "assistant", "content": assistant_content},
                    {"role": "user",      "content": state.results},
                ]
                body["messages"] = real_messages
                async with _loops_lock:
                    del _loops[loop_id]

    # ── Реальный запрос к OnlySQ ──────────────────────────────────────────────
    _tools_in_req = [t.get("name") for t in body.get("tools", [])]
    _sys_preview  = str(body.get("system", ""))[:300].replace("\n", "↵")
    _msgs_count   = len(body.get("messages", []))
    _last_role    = body["messages"][-1]["role"] if body.get("messages") else "?"
    log.info(f"[DEBUG] тулы в запросе: {_tools_in_req}")
    log.info(f"[DEBUG] системка (300): {_sys_preview}")
    log.info(f"[DEBUG] сообщений: {_msgs_count}, последнее от: {_last_role}")

    # ── Обработка изображений ─────────────────────────────────────────────────
    image_descriptions = None
    use_multimodal = False

    if has_images_in_body(body):
        log.info("[IMAGE] Обнаружены изображения в запросе")
        vision_capable = config.is_vision_capable(model)

        # Если известно что модель НЕ поддерживает vision — сразу fallback
        if vision_capable is False:
            log.info(f"[IMAGE] Модель {model} не поддерживает vision (из кэша), используем fallback")
        else:
            # Пробуем multimodal (либо известно что поддерживает, либо не проверялось)
            use_multimodal = True
            test_body = to_openai_body(body, model, use_multimodal=True)
            resp, key = await call_onlysq(test_body, is_sub)

            if resp is not None and resp.status == 200:
                # Запоминаем что модель поддерживает vision
                if vision_capable is None:
                    config.set_vision_capable(model, True)
                log.info(f"[IMAGE] Multimodal запрос принят, модель {model} поддерживает vision")

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
                    finally:
                        resp.release()
            else:
                # Модель не поддержала vision — запоминаем
                if resp:
                    resp.release()
                if vision_capable is None:
                    config.set_vision_capable(model, False)
                log.info(f"[IMAGE] Модель {model} не поддерживает vision, запомнили")

        # Fallback: используем vision модель для описания
        vision_model = config.get_vision_model()
        log.info(f"[IMAGE] Используем {vision_model} для описания изображений")
        use_multimodal = False
        image_descriptions = {}

        # Собираем все изображения с их позициями
        for msg_idx, msg in enumerate(body.get("messages", [])):
            content = msg.get("content", [])
            if isinstance(content, list):
                msg_images = {}
                img_idx = 0
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "image":
                        source = block.get("source", {})
                        if source.get("type") == "base64" and source.get("data"):
                            image = {
                                "media_type": source.get("media_type", "image/jpeg"),
                                "data": source.get("data"),
                            }
                            # Получаем любой ключ для vision запроса (не тратит Claude лимит)
                            vision_key = await pool.acquire(is_sub=True)  # vision использует sub лимит
                            log.info(f"[IMAGE] Описываем изображение {msg_idx}:{img_idx} через {vision_model}")
                            description = await describe_image_with_vision(_session, image, vision_key, vision_model)
                            msg_images[img_idx] = description
                            log.info(f"[IMAGE] Описание получено: {description[:100]}...")
                        img_idx += 1
                if msg_images:
                    image_descriptions[msg_idx] = msg_images

    openai_body = to_openai_body(body, model, image_descriptions, use_multimodal)
    resp, key = await call_onlysq(openai_body, is_sub)

    if resp is None:
        err_msg = "OnlySQ недоступен после всех попыток."
        log.error(f"[API] {err_msg}")
        log_request(source="claude", model=model, prompt_tokens=_prompt_tok, completion_tokens=0,
                    latency_ms=int((time.time() - _started) * 1000), status="error", error="unavailable")
        if is_stream:
            async def err_sse():
                r = to_anthropic_response(err_msg, model)
                yield f"event: message_start\ndata: {json.dumps({'type':'message_start','message':r})}\n\n"
                yield f"event: message_stop\ndata: {json.dumps({'type':'message_stop'})}\n\n"
            return StreamingResponse(err_sse(), media_type="text/event-stream")
        return JSONResponse(to_anthropic_response(err_msg, model), status_code=503)

    if is_stream:
        _captured: list[str] = []
        async def gen():
            try:
                async for chunk in stream_sse(resp, model, body, is_sub):
                    _captured.append(chunk)
                    yield chunk
            except asyncio.CancelledError:
                pass
            finally:
                resp.release()
                log_request(source="claude", model=model, prompt_tokens=_prompt_tok,
                            completion_tokens=_count_tokens("".join(_captured)),
                            latency_ms=int((time.time() - _started) * 1000), status="ok")
        return StreamingResponse(gen(), media_type="text/event-stream")
    else:
        try:
            data = await resp.json()
            text = data["choices"][0]["message"].get("content", "")
            log_request(source="claude", model=model, prompt_tokens=_prompt_tok,
                        completion_tokens=_count_tokens(text),
                        latency_ms=int((time.time() - _started) * 1000), status="ok")
            return JSONResponse(to_anthropic_response(text, model))
        except Exception as e:
            log.error(f"[API] Ошибка парсинга ответа: {e}")
            log_request(source="claude", model=model, prompt_tokens=_prompt_tok, completion_tokens=0,
                        latency_ms=int((time.time() - _started) * 1000), status="error", error="parse_error")
            return JSONResponse(to_anthropic_response("Ошибка парсинга ответа OnlySQ.", model))
        finally:
            resp.release()


# ─────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=7777, log_level="info")