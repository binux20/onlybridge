# OnlyBridge

[English](./README.md) | [Русский](./README.ru.md)

Local dashboard that bridges the free **[OnlySQ](https://my.onlysq.ru)** API into your favourite code tools.

OnlySQ ships top-tier models (Claude Opus, Sonnet, Haiku, GPT, Gemini, DeepSeek, Qwen and more) for free, but its native `tool_calling` is broken and most code tools won't work out of the box. OnlyBridge ships three local proxies — one per API dialect — and starts only the one your current tool needs. Each proxy translates between your tool's API format and OnlySQ, injects tool schemas via prompt-engineering and parses fenced ```json blocks back out — so things like file edits, shell commands and parallel tool calls just work.

> This project exists thanks to OnlySQ. The whole goal is to make code tools (Claude Code, OpenCode, aider, Continue, Cline, Kilo Code, Zed and any other OpenAI-compatible client) usable on top of the OnlySQ free tier.
> <img width="1632" height="779" alt="Снимок экрана 2026-04-26 150354" src="https://github.com/user-attachments/assets/187e861c-cc91-4283-88bb-411e037e0141" />

## What you get

| Tool | Port | Format |
|---|---|---|
| Claude Code | `7777` | Anthropic `/v1/messages` |
| OpenCode | `7778` | OpenAI `/v1/chat/completions` |
| OpenAI-compatible (aider, Continue, Cline, Kilo Code, Zed, ...) | `7779` | OpenAI `/v1/chat/completions` |

A single dashboard on `http://localhost:8800` lets you:

- paste your OnlySQ API key once (shared by all proxies)
- pick the main / sub model per proxy (live model list parsed from OnlySQ, vision and embed models filtered out)
- click **Setup & Start** — the dashboard writes the right config into `~/.claude/settings.json` / `opencode.json` / etc., backs the previous file up, and starts the proxy
- watch live logs (SSE), request stats and a 14-day timeseries chart
- toggle EN/RU and dark/light
- pick streaming mode (Realtime / Legacy) — see below

## Streaming modes

The OpenCode and OpenAI-compatible proxies have a switchable streaming mode in **Settings → STREAMING MODE**. The change is picked up on the next request, no restart needed.

- **Realtime** (default) — true token-by-token streaming. The proxy opens an SSE stream to OnlySQ and forwards chunks as they arrive. JSON tool fences (```json) are buffered until they close, then emitted as `tool_calls`. If the upstream stream cuts off mid-JSON, the proxy automatically retries the same request non-stream and recovers the tool call.
- **Legacy** — buffered: the proxy waits for the full reply, then fakes a stream in 400-char chunks. UX is slower but it is the most stable for tools.

Claude Code is always streamed and is unaffected by this switch. Note: the Claude Code terminal UI buffers the stream before showing it, so token-by-token output is not visible there even though the proxy is forwarding chunks live.

## Quick start (Windows)

```
git clone https://github.com/binux20/onlybridge
cd onlybridge
start.bat
```

`start.bat` checks for Python, installs `requirements.txt` if needed, builds the frontend on first run, and opens the dashboard. No manual venv, no manual `pip install`.

## Quick start (macOS / Linux)

```
git clone https://github.com/binux20/onlybridge
cd onlybridge
bash start.sh
```

`start.sh` auto-detects `python3`/`python`, installs `requirements.txt` if needed, builds the frontend on first run (if `npm` is available), and opens the dashboard.

Then open <http://localhost:8800>, paste your OnlySQ key in **Setup**, pick a tool, hit **Setup & Start**.

## Getting an OnlySQ key

1. Sign up at <https://my.onlysq.ru>
2. Verify via the Telegram bot [@OnlySqVerificarion_bot](https://t.me/OnlySqVerificarion_bot) — Telegram + phone number is recommended (Telegram-only verification skips Premium-tier free models like Opus 4.5)
3. Generate the API key on <https://my.onlysq.ru>
4. Paste it into the dashboard

Full walkthrough, rate-limit tiers and per-tool manual setup are inside the **Docs** tab of the dashboard.

## Why three proxies and not one

Each client speaks a slightly different dialect:

- Claude Code expects Anthropic's streaming SSE with `content_block_delta` events and tool use blocks
- OpenCode expects OpenAI streaming with title-generation + sub-agent quirks
- aider / Continue / Cline / Kilo Code / Zed expect plain OpenAI streaming

Keeping them in separate processes also means a bug in one proxy can't take the others down, and OS-level port discovery (`psutil`) shows you which one is actually running.

## Project layout

```
backend/        FastAPI dashboard + 3 proxies + services
frontend/       Vue 3 + Vite + Tailwind, built into frontend/dist
data/           SQLite stats + your config (gitignored)
docs/STYLE.md   visual style spec
start.bat       one-click launcher (Windows)
start.sh        one-click launcher (macOS/Linux)
```

## Remote / VPS access

OnlyBridge can now be installed on a VPS and used from your local machine over the network. In **Settings → NETWORK** set `BIND HOST` to `0.0.0.0` (or a specific interface IP), generate an auth token, and apply. The token is required for any non-localhost request and is printed in the server logs on first start with a non-localhost bind. The same token is auto-written as `apiKey` into `opencode.json` / `~/.claude/settings.json` so your code tool authenticates against the bridge.

Full step-by-step (SSH tunnel → first connect → enable remote → firewall → optional TLS via nginx/Caddy) is inside the **Docs → VPS / LAN SETUP** tab.

> No built-in TLS. For internet-facing setups put nginx or Caddy in front with a real certificate, and firewall the ports to your IP. Token leak = OnlySQ key burn.

## Vision / images

All three proxies handle images uniformly:

- If the selected model supports vision, the image in the latest user message is sent directly (multimodal `image_url`). The capability is auto-detected on the first try and cached per-model.
- If the model does not support vision, the image is described by a fallback vision model (`gemini-2.5-pro` by default) and the description is injected as `[IMAGE DESCRIPTION]` into the text request. Only the latest user message is described — older images become `[image in conversation history]` to save traffic and Gemini calls.
- For OpenCode the dashboard auto-writes `capabilities.vision: true` and `modalities.input: [text, image]` for the bridge models so OpenCode stops filtering images on the client side.

## Per-proxy RPM

Each proxy has its own MAIN / SUB rate limit. Edit in the Setup sidebar — applied live, no restart. Useful if your OnlySQ tier differs from the default 3/10.

## Resilient model fetch + custom models

If the OnlySQ models endpoint returns 5xx / 429 / empty, the dashboard retries up to 10 times with a 1-second backoff before falling back to the cached list and showing a status note. You can also add custom model ids manually via the **+ ADD CUSTOM MODEL** button — useful when OnlySQ ships a new model before our filter catches it. Custom models live in `localStorage` and are marked with ★ in the picker.

## Recent updates

- Remote / VPS access with an auth token (Settings → NETWORK; Docs → VPS / LAN SETUP).
- Vision / image support across all three proxies with auto multimodal-or-Gemini fallback; latest user message only.
- Per-proxy MAIN / SUB RPM editable in the Setup sidebar, applied live.
- Models fetch retries 10x on upstream errors, with custom model entries as a manual fallback.
- Docs page now has a sticky table-of-contents sidebar with smooth scroll.
- Switchable Realtime / Legacy streaming for the OpenCode and OpenAI-compatible proxies (Settings → STREAMING MODE).
- Per-proxy main / sub model overrides — each proxy can run a different model than the global default.
- `start.bat` falls back to `python` if the `py` launcher is not in PATH.
- Stats note linking to `my.onlysq.ru/usage` for exact usage; tiktoken is used locally for an approximate count.
- Telegram contact for bugs / ideas added to Docs and Settings.

## Bugs / ideas

Telegram: [@notgay8](https://t.me/notgay8)

PRs welcome.

## License

[MIT](./LICENSE) — do whatever you want.
