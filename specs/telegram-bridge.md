# Telegram Bridge Specs

Spec IDs: `TG-001` – `TG-030`

---

## Overview

The Telegram Bridge is a standalone Python microservice that connects a
Telegram bot to the local OpenHands agent-server. It lets a user chat with
their agent directly from any Telegram client (phone, desktop, web) without
opening the Agent Canvas UI.

### Architecture

```
Telegram User
    │  sends message
    ▼
Telegram Bot API
    │  POST /telegram/webhook  (webhook mode)
    │  OR poll getUpdates      (dev/polling mode)
    ▼
┌────────────────────────────────────────────┐
│  telegram/bridge/   (FastAPI, port 18002)  │
│                                            │
│  • Webhook / polling receiver              │
│  • SQLite session store                    │
│    (chat_id  →  conversation_id)           │
│  • Agent-server REST client                │
│  • Response poller / WebSocket listener    │
└────────────────────────────────────────────┘
    │  POST /api/conversations                (start conv)
    │  WebSocket /ws/{conv_id}                (send / receive)
    │  GET  /api/conversations/{conv_id}      (status check)
    ▼
Agent Server  :18000
    │
    ▼
LLM → reply  →  bridge  →  Telegram sendMessage  →  User
```

---

## File & Package Layout

All new files created by this spec live under `telegram/` at the repository
root unless another path is explicitly named.

```
telegram/
├── pyproject.toml          # Python package: fastapi, uvicorn, aiohttp,
│                           #   python-telegram-bot[webhooks], aiosqlite
├── README.md               # Quick-start: BotFather, env vars, run commands
└── bridge/
    ├── __init__.py
    ├── __main__.py         # python -m bridge  entry point
    ├── app.py              # FastAPI application (webhook + health endpoint)
    ├── config.py           # Config dataclass built from env vars
    ├── session.py          # aiosqlite-backed chat_id → conversation_id store
    ├── agent_client.py     # Agent-server REST + WebSocket client
    └── handlers.py         # Telegram update handlers (message, /start, /new)

scripts/
└── dev-with-telegram.mjs   # Extends dev-with-automation to also launch bridge

config/
└── defaults.json           # gains  "telegram": 18002  under "ports"

.env.sample                 # gains  TELEGRAM_BOT_TOKEN=  and  TELEGRAM_WEBHOOK_URL=
```

---

## Service Specs

---

### TG-001 — Python package manifest

`telegram/pyproject.toml` shall declare a project named `openhands-telegram-bridge`
with these runtime dependencies:

| Package | Minimum version | Purpose |
|---|---|---|
| `fastapi` | `0.115` | HTTP server & webhook endpoint |
| `uvicorn[standard]` | `0.30` | ASGI server |
| `python-telegram-bot[webhooks]` | `21.0` | Telegram Bot API client (async) |
| `aiohttp` | `3.9` | HTTP requests to agent-server |
| `aiosqlite` | `0.20` | Async SQLite for session store |

The project shall include a `[project.scripts]` entry so that the bridge can
be invoked as `openhands-telegram-bridge` when installed.

---

### TG-002 — Config from environment variables

`bridge/config.py` shall expose a `BridgeConfig` dataclass (or simple attrs
class) populated by reading environment variables at startup.

| Env var | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | **Yes** | — | Bot token from @BotFather |
| `TELEGRAM_WEBHOOK_URL` | No | `""` | Full HTTPS URL for webhook mode. Empty = polling mode. |
| `TELEGRAM_WEBHOOK_SECRET` | No | `""` | Telegram webhook secret token sent as `X-Telegram-Bot-Api-Secret-Token` header |
| `TELEGRAM_PORT` | No | `18002` | Port the FastAPI service listens on |
| `TELEGRAM_DB_PATH` | No | `~/.openhands/agent-canvas/telegram.db` | SQLite database path |
| `AGENT_SERVER_URL` | No | `http://localhost:18000` | Agent-server base URL |
| `SESSION_API_KEY` | No | `""` | Passed as `X-Session-API-Key` header on all agent-server calls |
| `OPENHANDS_WORKING_DIR` | No | `""` | Forwarded as `working_dir` when creating a new conversation |

`BridgeConfig` shall validate at startup that `TELEGRAM_BOT_TOKEN` is non-empty
and log a clear error + exit with code 1 if it is missing.

---

### TG-003 — SQLite session store

`bridge/session.py` shall implement a `SessionStore` class backed by
`aiosqlite` with the following schema:

```sql
CREATE TABLE IF NOT EXISTS sessions (
  chat_id       INTEGER PRIMARY KEY,
  conv_id       TEXT    NOT NULL,
  created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

`SessionStore` shall expose:

- `async get(chat_id: int) -> str | None` — returns `conv_id` or `None`
- `async set(chat_id: int, conv_id: str) -> None` — upsert
- `async delete(chat_id: int) -> None` — remove session (used by `/new` command)
- `async open() -> None` / `async close() -> None` — lifecycle hooks called by
  FastAPI's `lifespan` handler

---

### TG-004 — Webhook endpoint

`bridge/app.py` shall register a FastAPI route:

```
POST /telegram/webhook
```

**Request validation:** When `TELEGRAM_WEBHOOK_SECRET` is non-empty, the
handler shall reject requests that do not carry a matching
`X-Telegram-Bot-Api-Secret-Token` header with HTTP 403.

**Processing:** The handler shall deserialize the Telegram `Update` object,
dispatch it to the appropriate handler in `handlers.py`, and **always** return
HTTP 200 to Telegram within 3 seconds (use `BackgroundTasks` for slow work).

**Health endpoint:**

```
GET /health
```

Returns `{"status": "ok", "mode": "webhook"|"polling"}`.

---

### TG-005 — Polling mode for local development

When `TELEGRAM_WEBHOOK_URL` is empty, the bridge shall start in **polling mode**:

- On startup, call `bot.delete_webhook()` to ensure Telegram is not holding a
  stale webhook registration.
- Launch `Application.run_polling()` in a background thread (using
  `python-telegram-bot`'s built-in runner). The FastAPI server shall still start
  on `TELEGRAM_PORT` to expose the `/health` endpoint.
- Log clearly at startup: `Running in POLLING mode — suitable for local dev only`.

When `TELEGRAM_WEBHOOK_URL` is set, the bridge shall operate in **webhook mode**:

- On startup, call `bot.set_webhook(url=..., secret_token=...)`.
- Log clearly: `Webhook registered at {TELEGRAM_WEBHOOK_URL}/telegram/webhook`.

---

### TG-006 — Inbound message handler

`bridge/handlers.py` shall implement `handle_message(update, context)` which
fires for every plain text message that is NOT a command.

Algorithm:

1. Extract `chat_id` and `text` from `update.message`.
2. Look up `conv_id = await session_store.get(chat_id)`.
3. If `conv_id` is `None`, call `agent_client.create_conversation(text)` to
   start a new agent-server conversation → store the returned `conv_id`.
4. If `conv_id` exists, call `agent_client.send_message(conv_id, text)` to
   continue the conversation.
5. Send a `ChatAction.TYPING` indicator via `context.bot.send_chat_action`.
6. Await `agent_client.wait_for_response(conv_id, after_event_id)` (see TG-009).
7. Send the agent's reply with `update.message.reply_text(...)`.
8. Handle all exceptions: log the error and reply with a user-facing apology
   message (see TG-013).

---

### TG-007 — New conversation creation via REST

`bridge/agent_client.py` shall implement:

```python
async def create_conversation(initial_message: str) -> str:
    """
    POST /api/conversations
    Returns the new conversation_id.
    """
```

**Request body** (all optional fields beyond `initial_user_message` may be
omitted if not configured):

```json
{
  "initial_user_message": "<user text>",
  "working_dir": "<OPENHANDS_WORKING_DIR if set>",
  "tools": ["terminal", "file_editor", "task_tracker"]
}
```

**Headers:** Always include `X-Session-API-Key: {SESSION_API_KEY}` when the
key is non-empty.

**Return value:** Extract `conversation_id` from the JSON response and return it.

---

### TG-008 — Follow-up message delivery via WebSocket

`bridge/agent_client.py` shall implement:

```python
async def send_message(conv_id: str, text: str) -> int:
    """
    Connect to ws://{AGENT_SERVER_URL}/ws/{conv_id}?token={SESSION_API_KEY},
    send a user message event, and return the timestamp of the sent event
    so the caller can use it as the `after` cursor for response polling.
    """
```

**WebSocket connection URL:**
`ws://{agent_server_host}/ws/{conv_id}?token={SESSION_API_KEY}`
(replace `http://` with `ws://` or `https://` with `wss://`).

**Message envelope** (matches the agent-server's expected user action format):

```json
{
  "action": "message",
  "args": {
    "content": "<user text>",
    "image_urls": []
  }
}
```

After sending, the function shall **leave the WebSocket open** and yield it back
to `wait_for_response` so the same connection can be used for streaming the
reply (see TG-009). If the WebSocket cannot be established, raise
`AgentServerError`.

---

### TG-009 — Response streaming via WebSocket

`bridge/agent_client.py` shall implement:

```python
async def wait_for_response(
    ws: WebSocketClientConnection,
    timeout_seconds: int = 120,
) -> str:
    """
    Read events from an open WebSocket until the agent finishes its turn.
    Returns the full assistant message text.
    Raises TimeoutError if no response arrives within timeout_seconds.
    """
```

**Termination conditions** (stop listening when ANY of these is received):

| Event type | Field check | Action |
|---|---|---|
| `MessageObservation` | `source == "agent"` | Accumulate message text; continue if more expected |
| `AgentStateChangedObservation` | `agent_state in ("paused", "finished", "error")` | Stop and return accumulated text |

**Agent state mapping:** If `agent_state == "error"`, raise `AgentServerError`
so TG-013 can send an apology message.

**Timeout:** If `timeout_seconds` elapses without a termination event, raise
`TimeoutError`.

The WebSocket shall be closed after this function returns or raises.

---

### TG-010 — Typing indicator persistence

The bridge shall **re-send** `ChatAction.TYPING` every 4 seconds while
`wait_for_response` is running. Telegram typing indicators auto-expire after
5 seconds; periodic re-sends ensure the indicator stays visible for long agent
responses.

Implementation: wrap the indicator send in a `asyncio.create_task` loop that
runs concurrently with `wait_for_response` and is cancelled when the response
arrives.

---

### TG-011 — Long message splitting

Telegram messages are limited to **4 096 characters**. The bridge shall split
agent responses that exceed this limit into sequential messages:

1. Split on the last newline boundary before the 4 096-char mark to avoid
   cutting mid-sentence.
2. Send each chunk with `reply_text` (first chunk) and `send_message` with
   `reply_to_message_id` (subsequent chunks).
3. Add a small async delay (100 ms) between chunks to preserve order.

---

### TG-012 — `/start` and `/new` commands

`bridge/handlers.py` shall register two command handlers:

**`/start`**
- If no session exists: reply with a welcome message explaining the bot and
  prompt the user to type their first task.
- If a session exists: confirm the current session is active and offer `/new`
  to start fresh.

**`/new`**
- Delete the current session via `session_store.delete(chat_id)`.
- Reply: *"Starting a new conversation. What would you like help with?"*

Both handlers shall respond in under 3 seconds (no agent call required).

---

### TG-013 — Error handling

The bridge shall handle three error categories:

| Situation | User-facing message |
|---|---|
| `TELEGRAM_BOT_TOKEN` missing at startup | Log error + exit (never reaches users) |
| Agent server unreachable | *"⚠️ The agent server is not running. Please start it and try again."* |
| Agent returns error state | *"❌ The agent encountered an error. Try `/new` to start a fresh conversation."* |
| Response timeout (> 120 s) | *"⏳ The agent is taking longer than expected. Your conversation is still running — check the Canvas UI."* |
| Unexpected exception | Log full traceback; reply *"Something went wrong. Please try again or use `/new` to reset."* |

Errors shall never expose stack traces or internal config values to the Telegram user.

---

### TG-014 — Graceful shutdown

On `SIGTERM` or `SIGINT`, the bridge shall:

1. Call `bot.delete_webhook()` (webhook mode only) to deregister from Telegram.
2. Close all open WebSocket connections.
3. Close the SQLite connection pool.
4. Exit cleanly without leaving orphaned conversations in a `RUNNING` state.

---

### TG-015 — Agent-server authentication

All HTTP requests to the agent-server shall include the header
`X-Session-API-Key: {SESSION_API_KEY}` when `SESSION_API_KEY` is non-empty.
All WebSocket URLs shall append `?token={SESSION_API_KEY}` when the key is
non-empty. If the agent-server returns HTTP 401, the bridge shall log
`Session API key rejected — check SESSION_API_KEY env var` and raise
`AgentServerError`.

---

## Dev Stack Integration

---

### TG-016 — Port assignment in `config/defaults.json`

`config/defaults.json` shall gain a new entry under `"ports"`:

```json
"telegram": 18002
```

All references to the telegram bridge port in scripts and docs shall read from
this file instead of hardcoding `18002`.

---

### TG-017 — `scripts/dev-with-telegram.mjs`

A new launcher script `scripts/dev-with-telegram.mjs` shall start the full
dev stack **plus** the telegram bridge. It shall:

1. Import and reuse all helpers from `scripts/dev-safe.mjs` and
   `scripts/dev-with-automation.mjs`.
2. Read `ports.telegram` from `config/defaults.json`.
3. Start the automation backend (identical to `dev-with-automation.mjs`).
4. Start the telegram bridge via:
   ```
   uvx --from ./telegram openhands-telegram-bridge
   ```
   with environment variables:
   ```
   AGENT_SERVER_URL=http://localhost:{agentServerPort}
   SESSION_API_KEY={sessionApiKey}
   TELEGRAM_BOT_TOKEN={TELEGRAM_BOT_TOKEN from process.env}
   TELEGRAM_WEBHOOK_URL={TELEGRAM_WEBHOOK_URL from process.env, may be empty}
   TELEGRAM_PORT={telegramPort}
   OPENHANDS_WORKING_DIR={workingDir}
   ```
5. If `TELEGRAM_BOT_TOKEN` is not set in the environment, print a clear
   one-time setup message and continue starting the rest of the stack without
   the bridge. Do **not** hard-fail if the token is missing — the rest of the
   stack should run normally.
6. Add a `telegram` entry to the `VITE_RUNTIME_SERVICES_INFO` block so agents
   know the bridge is reachable:
   ```json
   "telegram": {
     "description": "Telegram bridge. Forwards Telegram messages to/from this agent-server.",
     "url_from_agent": "http://localhost:{telegramPort}",
     "api_prefix": "/telegram"
   }
   ```
7. Log the bridge URL and mode (polling/webhook) at startup.
8. Add a `package.json` script:
   ```json
   "dev:telegram": "node scripts/dev-with-telegram.mjs"
   ```

---

### TG-018 — `.env.sample` additions

`.env.sample` shall gain the following new entries in a `# Telegram Bridge`
section:

```bash
# Telegram Bridge
# Get your token from @BotFather on Telegram: https://t.me/BotFather
TELEGRAM_BOT_TOKEN=

# Set to your full public HTTPS URL for webhook mode (leave blank for polling mode)
# Example: https://your-domain.com
# Leave blank for local development (polling mode used automatically)
TELEGRAM_WEBHOOK_URL=

# Optional: secret token for webhook signature verification (recommended for production)
TELEGRAM_WEBHOOK_SECRET=
```

---

### TG-019 — Docker support

`docker/entrypoint.sh` shall:

1. Conditionally start the telegram bridge if `TELEGRAM_BOT_TOKEN` is non-empty:
   ```bash
   if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
     openhands-telegram-bridge &
     log "Telegram bridge started (port ${TELEGRAM_PORT:-18002})"
   fi
   ```
2. Pass `AGENT_SERVER_URL=http://localhost:18000` and `SESSION_API_KEY` to the
   bridge process.

`docker/Dockerfile` shall install the telegram bridge package in the image build
via `pip install ./telegram` (local editable) or equivalent `uv pip install`.
The installed `openhands-telegram-bridge` binary shall be on `PATH`.

---

### TG-020 — Ingress proxy routing for webhook

`scripts/ingress.mjs` (the standalone ingress proxy used by dev launchers)
shall add a routing rule when the telegram bridge is running:

```
/telegram/*  →  http://localhost:{telegramPort}
```

This allows Telegram to call the webhook via the unified ingress entry point
(e.g. `https://your-domain.com/telegram/webhook`) rather than requiring a
separate public URL for the bridge port.

---

## Frontend Settings

---

### TG-021 — Settings route: `src/routes/telegram-settings.tsx`

A new route `/settings/telegram` shall render a settings page for the Telegram
integration. The page shall:

- Display a header: `SETTINGS$NAV_TELEGRAM` i18n key (value: `"Telegram"`).
- Contain a single input field for `TELEGRAM_BOT_TOKEN` (password/masked type).
- Show save/cancel buttons consistent with the other settings pages.
- Save the token via `PUT /api/settings/secrets` with name `TELEGRAM_BOT_TOKEN`
  (using the existing `SecretsService`).
- Read the stored token via `GET /api/settings/secrets/TELEGRAM_BOT_TOKEN` on
  page load (display the masked value from the server response).

---

### TG-022 — Telegram nav item in settings sidebar

`src/hooks/use-settings-nav-items.ts` shall add a new nav entry for
`/settings/telegram` with:

- Icon: use the `MessageSquare` icon (or equivalent from the existing icon set).
- Label: `I18nKey.SETTINGS$NAV_TELEGRAM`.
- Position: below "Secrets" in the nav list.

The nav item shall be visible only when the app is running in local (non-cloud)
mode, consistent with other local-only settings entries.

---

### TG-023 — Connection status display

The Telegram settings page shall display a connection status indicator by
calling `GET /health` on the bridge URL
(`http://localhost:{TELEGRAM_PORT}/health`). This request shall:

- Show a green dot + `"Bridge running (polling)"` or `"Bridge running (webhook)"`.
- Show a red dot + `"Bridge not running — start with npm run dev:telegram"` when
  the health endpoint is unreachable.
- Use a 5-second poll interval (standard React Query refetch).

---

### TG-024 — i18n keys

The following keys shall be added to `src/i18n/translation.json` with English
values as shown. All 15 supported languages shall receive the English fallback
as their initial value (standard practice for new keys).

| Key | English value |
|---|---|
| `SETTINGS$NAV_TELEGRAM` | `"Telegram"` |
| `SETTINGS$TELEGRAM_TITLE` | `"Telegram Integration"` |
| `SETTINGS$TELEGRAM_TOKEN_LABEL` | `"Bot Token"` |
| `SETTINGS$TELEGRAM_TOKEN_HELP` | `"Create a bot with @BotFather on Telegram to get your token."` |
| `SETTINGS$TELEGRAM_STATUS_RUNNING_POLLING` | `"Bridge running (polling mode)"` |
| `SETTINGS$TELEGRAM_STATUS_RUNNING_WEBHOOK` | `"Bridge running (webhook mode)"` |
| `SETTINGS$TELEGRAM_STATUS_NOT_RUNNING` | `"Bridge not running — start with npm run dev:telegram"` |
| `SETTINGS$TELEGRAM_SAVE_SUCCESS` | `"Bot token saved."` |

After adding the keys, run `npm run make-i18n` to regenerate
`src/i18n/declaration.ts` and the locale JSON files.

---

## `telegram/README.md` Content

The `telegram/README.md` shall include:

1. **Prerequisites**: Python ≥ 3.11, `uv`, a Telegram account.
2. **Step 1 — Create a bot**: Exact instructions for messaging @BotFather, getting the token.
3. **Step 2 — Configure**: Set `TELEGRAM_BOT_TOKEN=...` in `.env`.
4. **Step 3 — Run**:
   ```bash
   # With the full dev stack:
   npm run dev:telegram

   # Bridge only (agent-server must already be running):
   cd telegram && uv run python -m bridge
   ```
5. **Webhook for production**: Instructions for setting `TELEGRAM_WEBHOOK_URL` and running behind an HTTPS reverse proxy.
6. **Commands available**: `/start`, `/new`.
7. **Troubleshooting**: Common errors (wrong token, agent unreachable, webhook cert issues).

---

## Implementation Checklist (for OpenHands)

Work in the `20260527-telegram` branch of `jamiechicago312/agent-canvas`.

- [ ] **TG-001** `telegram/pyproject.toml` and package scaffolding
- [ ] **TG-002** `bridge/config.py` — env var config with startup validation
- [ ] **TG-003** `bridge/session.py` — SQLite session store
- [ ] **TG-004** `bridge/app.py` — webhook endpoint + health route
- [ ] **TG-005** Polling mode (no-webhook dev path) in `__main__.py`
- [ ] **TG-006** `bridge/handlers.py` — inbound message handler
- [ ] **TG-007** `bridge/agent_client.py` — `create_conversation()`
- [ ] **TG-008** `bridge/agent_client.py` — `send_message()`
- [ ] **TG-009** `bridge/agent_client.py` — `wait_for_response()`
- [ ] **TG-010** Typing indicator keep-alive loop
- [ ] **TG-011** Long message splitting helper
- [ ] **TG-012** `/start` and `/new` command handlers
- [ ] **TG-013** Error handling + user-facing messages
- [ ] **TG-014** Graceful shutdown on SIGTERM/SIGINT
- [ ] **TG-015** Session API key auth on all agent-server calls
- [ ] **TG-016** `config/defaults.json` — add `ports.telegram`
- [ ] **TG-017** `scripts/dev-with-telegram.mjs` + `package.json` script
- [ ] **TG-018** `.env.sample` additions
- [ ] **TG-019** Docker `entrypoint.sh` + `Dockerfile` changes
- [ ] **TG-020** Ingress proxy routing rule for `/telegram/*`
- [ ] **TG-021** `src/routes/telegram-settings.tsx`
- [ ] **TG-022** Nav item in `use-settings-nav-items.ts`
- [ ] **TG-023** Connection status polling on settings page
- [ ] **TG-024** i18n keys + `npm run make-i18n`
- [ ] **README** `telegram/README.md`
