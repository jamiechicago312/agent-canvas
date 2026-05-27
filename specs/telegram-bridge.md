# Telegram Bridge Specs

Spec IDs: `TG-001` – `TG-028`  
Last updated: 2026-05-27

---

## Core Design Principle

**The Telegram bot is a second frontend for Agent Canvas — not a separate AI
service.** When you message the bot, you are talking to the exact same agent
you talk to through the browser: same conversation history, same LLM
configuration, same tools, same workspace. Telegram is just another client,
exactly the way the browser is a client today.

The bridge itself contains zero AI logic. It is a thin adapter:
receive message from Telegram → forward to agent-server → wait for reply →
send back to Telegram.

---

## Architecture

```
Your phone (mobile data)
    │
    │  sends message
    ▼
Telegram's servers  ◄──────── bridge polls every ~1 s (polling mode)
    │                         Your phone never touches your laptop directly.
    │  update delivered       Telegram acts as the relay.
    ▼
┌─────────────────────────────────────────────────────────┐
│  telegram/bridge/  — service #4 inside Docker image     │
│                       port 18002                         │
│                                                          │
│  • Polling receiver  (default, no public URL needed)     │
│  • OR Webhook receiver  (opt-in for production)          │
│  • Owner-only gate   (only your chat_id is accepted)     │
│  • SQLite session    (chat_id → conversation_id)         │
│  • Agent-server client  (REST + WebSocket)               │
└─────────────────────────────────────────────────────────┘
         │                              │
         │  local backend               │  cloud backend
         │  POST /api/conversations     │  POST /api/v1/app-conversations
         │  WS   /ws/{conv_id}          │  WS   {sandbox_url}/ws/{conv_id}
         ▼                              ▼
  Agent Server :18000            OpenHands Cloud
  (same Docker container)        app.all-hands.dev
         │                              │
         └──────────────┬───────────────┘
                        │  same LLM, same conversation,
                        │  same history as the browser UI
                        ▼
               reply text  →  Telegram sendMessage  →  Your phone
```

**Why polling works on mobile:** Your phone connects to Telegram's servers, not
to your laptop. The bridge reaches *out* to Telegram every second. Telegram
queues incoming messages until the bridge picks them up. No inbound ports, no
public URL, no port-forwarding required. This works from behind any home
router, corporate firewall, or Docker network.

---

## Deployment model

The bridge is **service #4 in the existing Docker image** — the same image
already used to run agent-canvas. It starts automatically when
`TELEGRAM_BOT_TOKEN` is set. Users who do not set the token see no change.

```bash
# Local test — one command, nothing else to install:
docker run -e TELEGRAM_BOT_TOKEN=your_token -p 8000:8000 \
  ghcr.io/openhands/agent-canvas

# Always-on cloud — add your OpenHands Cloud API key:
docker run -e TELEGRAM_BOT_TOKEN=your_token \
           -e OPENHANDS_API_KEY=your_cloud_key \
           -p 8000:8000 \
           ghcr.io/openhands/agent-canvas
```

---

## Two backends, one bridge

### Local (default)
Bridge talks to the agent-server co-located in the same Docker container at
`http://127.0.0.1:18000`. Auth: `SESSION_API_KEY` as `X-Session-API-Key`
header. Works immediately with no extra config. Agent stops when the container
stops.

### Cloud (always-on)
Bridge talks to `https://app.all-hands.dev` using `OPENHANDS_API_KEY`.
Conversations persist in the cloud even when the container is not running.
Auth: `Authorization: Bearer {OPENHANDS_API_KEY}` header. The bridge can run
anywhere — the container, a tiny VPS, or Railway free-tier.

The bridge detects which backend to use at startup:
- `OPENHANDS_API_KEY` is set → cloud mode
- `OPENHANDS_API_KEY` is absent → local mode

---

## File & Package Layout

All new files live under `telegram/` at the repository root unless explicitly
stated otherwise.

```
telegram/
├── pyproject.toml            # package: openhands-telegram-bridge
├── README.md                 # setup guide (see TG-028)
└── bridge/
    ├── __init__.py
    ├── __main__.py           # entry point: python -m bridge
    ├── app.py                # FastAPI app — webhook endpoint + /health
    ├── config.py             # BridgeConfig dataclass from env vars
    ├── session.py            # aiosqlite chat_id → conv_id store
    ├── agent_client.py       # REST + WebSocket client (local and cloud)
    └── handlers.py           # Telegram update handlers

docker/
└── entrypoint.sh             # gains service #4 block (TG-019)

docker/
└── Dockerfile                # gains pip install ./telegram (TG-019)

config/
└── defaults.json             # gains ports.telegram: 18002 (TG-016)

.env.sample                   # gains Telegram section (TG-018)

src/routes/
└── telegram-settings.tsx     # new Settings > Integrations > Telegram page

src/components/features/
└── integrations/
    └── telegram/
        ├── telegram-settings-page.tsx
        └── telegram-status-badge.tsx
```

---

## Service Specs

---

### TG-001 — Python package manifest

`telegram/pyproject.toml` shall declare a project named
`openhands-telegram-bridge` with these runtime dependencies:

| Package | Minimum version | Purpose |
|---|---|---|
| `fastapi` | `0.115` | HTTP server + webhook endpoint |
| `uvicorn[standard]` | `0.30` | ASGI server |
| `python-telegram-bot[webhooks]` | `21.0` | Telegram Bot API client (async) |
| `aiohttp` | `3.9` | HTTP requests to agent-server |
| `aiosqlite` | `0.20` | Async SQLite for session store |
| `websockets` | `13.0` | WebSocket client for agent-server events |

`[project.scripts]` shall expose `openhands-telegram-bridge` as the CLI
entry point (`bridge.__main__:main`).

---

### TG-002 — Config from environment variables

`bridge/config.py` shall expose a `BridgeConfig` dataclass populated at
startup from environment variables.

| Env var | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | **Yes** | — | Token from @BotFather |
| `TELEGRAM_OWNER_CHAT_ID` | No | `""` | Numeric Telegram chat ID of the owner. If empty, the first person to message the bot becomes the owner (chat_id saved to DB). |
| `TELEGRAM_WEBHOOK_URL` | No | `""` | Full HTTPS base URL for webhook mode. Empty → polling mode (default). |
| `TELEGRAM_WEBHOOK_SECRET` | No | `""` | Webhook signature verification token |
| `TELEGRAM_PORT` | No | `18002` | FastAPI listen port |
| `TELEGRAM_DB_PATH` | No | `~/.openhands/agent-canvas/telegram.db` | SQLite file path |
| `AGENT_SERVER_URL` | No | `http://127.0.0.1:18000` | Local agent-server URL |
| `SESSION_API_KEY` | No | `""` | Local agent-server auth key |
| `OPENHANDS_API_KEY` | No | `""` | OpenHands Cloud API key. When set, cloud mode is active. |
| `OPENHANDS_HOST` | No | `https://app.all-hands.dev` | Cloud host. Only used when `OPENHANDS_API_KEY` is set. |
| `OPENHANDS_WORKING_DIR` | No | `""` | Passed as `working_dir` on new local conversations |

**Startup validation:** If `TELEGRAM_BOT_TOKEN` is empty, log a clear error
and exit with code 1. Log the active mode (`local` or `cloud`) and
`polling` or `webhook` at startup.

---

### TG-003 — SQLite session store

`bridge/session.py` shall implement `SessionStore` backed by `aiosqlite`.

**Schema:**

```sql
CREATE TABLE IF NOT EXISTS sessions (
  chat_id       INTEGER PRIMARY KEY,
  conv_id       TEXT    NOT NULL,
  created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS owner (
  id            INTEGER PRIMARY KEY CHECK (id = 1),
  chat_id       INTEGER NOT NULL
);
```

**API:**

- `async get(chat_id: int) -> str | None`
- `async set(chat_id: int, conv_id: str) -> None` — upsert, updates `updated_at`
- `async delete(chat_id: int) -> None`
- `async get_owner() -> int | None` — returns the whitelisted owner chat_id
- `async set_owner(chat_id: int) -> None` — called once on first message if `TELEGRAM_OWNER_CHAT_ID` was not pre-set
- `async open() / async close()` — called by FastAPI lifespan

---

### TG-004 — FastAPI app and webhook endpoint

`bridge/app.py` shall create a FastAPI application with two routes:

**`POST /telegram/webhook`**
- Validates `X-Telegram-Bot-Api-Secret-Token` header when
  `TELEGRAM_WEBHOOK_SECRET` is non-empty (HTTP 403 on mismatch).
- Deserializes the `Update` and dispatches to `handlers.py`.
- Returns HTTP 200 within 3 seconds. Slow work runs via `BackgroundTasks`.

**`GET /health`**
```json
{
  "status": "ok",
  "mode": "polling" | "webhook",
  "backend": "local" | "cloud",
  "owner_set": true | false
}
```

---

### TG-005 — Polling mode (default)

**Polling is the default and recommended mode.** No public URL, no HTTPS
certificate, no firewall rules needed. Works immediately on any machine.

When `TELEGRAM_WEBHOOK_URL` is empty:
1. On startup, call `bot.delete_webhook()` to clear any stale Telegram webhook.
2. Run `Application.run_polling()` in a background asyncio task.
3. FastAPI still starts on `TELEGRAM_PORT` (for the `/health` endpoint).
4. Log: `[telegram] Running in POLLING mode — no public URL needed`.

When `TELEGRAM_WEBHOOK_URL` is set (opt-in, production):
1. Register: `bot.set_webhook(url=f"{TELEGRAM_WEBHOOK_URL}/telegram/webhook", secret_token=...)`.
2. Log: `[telegram] Webhook registered at {url}/telegram/webhook`.

---

### TG-025 — Owner-only lockdown

The bridge is a **private bot**. Only one person — the owner — can use it.

**Owner identification algorithm** (runs at the top of every handler, before
any agent call):

1. Load `owner_chat_id` from the DB via `session_store.get_owner()`.
2. If `TELEGRAM_OWNER_CHAT_ID` env var is set, treat that value as the
   owner regardless of the DB.
3. If neither is set AND the DB has no owner yet:
   - The first person to send any message becomes the owner.
   - Store their `chat_id` via `session_store.set_owner(chat_id)`.
   - Log: `[telegram] Owner set to chat_id={chat_id} on first message`.
4. If an incoming message comes from a `chat_id` that is NOT the owner:
   - Do not process the message.
   - Do not reply.
   - Log: `[telegram] Rejected message from unknown chat_id={chat_id}`.

**This means:** whoever messages the bot first owns it. To explicitly
pre-configure ownership, set `TELEGRAM_OWNER_CHAT_ID` in the env.

---

### TG-006 — Inbound message handler

`bridge/handlers.py` shall implement `handle_message(update, context)`.

Algorithm:
1. Run the owner gate (TG-025). Silently return if not owner.
2. Extract `chat_id` and `text`.
3. Look up `conv_id = await session_store.get(chat_id)`.
4. If `None`: call `agent_client.create_conversation(text)` → store returned `conv_id`.
5. If exists: call `agent_client.send_message(conv_id, text)`.
6. Start the typing indicator loop (TG-010).
7. Await `agent_client.wait_for_response(ws)`.
8. Cancel typing indicator loop.
9. Send the reply, splitting if needed (TG-011).
10. Catch all exceptions → send user-facing error message (TG-013).

---

### TG-026 — Backend selection and agent-server client

`bridge/agent_client.py` shall implement an `AgentClient` class that detects
the active backend at init time and routes all API calls accordingly.

```python
class AgentClient:
    def __init__(self, config: BridgeConfig):
        self.mode = "cloud" if config.openhands_api_key else "local"
```

**Local mode — `create_conversation(initial_message: str) -> str`:**

```
POST {AGENT_SERVER_URL}/api/conversations
Headers: X-Session-API-Key: {SESSION_API_KEY}
Body: {
  "initial_user_message": "<text>",
  "working_dir": "<OPENHANDS_WORKING_DIR if set>",
  "tools": ["terminal", "file_editor", "task_tracker"]
}
Returns: conversation_id from response JSON
```

**Cloud mode — `create_conversation(initial_message: str) -> str`:**

```
POST {OPENHANDS_HOST}/api/v1/app-conversations
Headers: Authorization: Bearer {OPENHANDS_API_KEY}
Body: {
  "initial_user_message": "<text>"
}
Returns: conversation_id from response JSON
```

The same method signature is used for both modes so `handlers.py` never needs
to branch on backend type.

---

### TG-007 — *(merged into TG-026)*

See TG-026 for conversation creation. The create-conversation logic for both
local and cloud is specified there.

---

### TG-008 — Follow-up message delivery via WebSocket

`agent_client.py` shall implement `send_message(conv_id: str, text: str) ->
websockets.WebSocketClientProtocol`:

**Local WebSocket URL:**
```
ws://{agent_server_host}/ws/{conv_id}?token={SESSION_API_KEY}
```
(derive by replacing `http://` → `ws://` and `https://` → `wss://` in
`AGENT_SERVER_URL`)

**Cloud WebSocket URL:**
Fetch the sandbox URL from `GET {OPENHANDS_HOST}/api/v1/app-conversations/{conv_id}`,
extract `conversation_url`, then connect:
```
wss://{conversation_url_host}/ws/{conv_id}?token={SESSION_API_KEY}
```
Use `Authorization: Bearer {OPENHANDS_API_KEY}` on the GET call.

**Message envelope** (same for both modes):
```json
{
  "action": "message",
  "args": { "content": "<text>", "image_urls": [] }
}
```

Return the open WebSocket so `wait_for_response` reuses the same connection.
Raise `AgentServerError` if connection fails.

---

### TG-009 — Response streaming via WebSocket

`agent_client.py` shall implement:
```python
async def wait_for_response(ws, timeout_seconds: int = 120) -> str
```

Listen on the open WebSocket. Stop and return accumulated assistant text when:

| Event | Field | Action |
|---|---|---|
| `MessageObservation` | `source == "agent"` | Accumulate text |
| `AgentStateChangedObservation` | `agent_state in ("paused", "finished", "error")` | Stop |

If `agent_state == "error"` → raise `AgentServerError`.
If timeout elapses → raise `TimeoutError`.
Always close the WebSocket on return or raise.

---

### TG-027 — Conversation appears in Agent Canvas UI

Because the bridge creates real conversations on the agent-server (local) or
OpenHands Cloud, every Telegram exchange is automatically visible in the
Agent Canvas browser UI:

- **Local**: The conversation appears in the conversations panel at
  `http://localhost:8000` alongside any browser-started conversations.
- **Cloud**: The conversation appears at `https://app.all-hands.dev` in the
  user's conversation list.

The user can switch between Telegram and browser mid-task with no friction —
context is shared because it is the same conversation object. No sync, no
export, no copy-paste required.

This spec does not require any additional implementation — it is a free
consequence of the architecture. The bridge shall include a note about this
in its README (TG-028).

---

### TG-010 — Typing indicator persistence

Re-send `ChatAction.TYPING` every 4 seconds while `wait_for_response` is
running (Telegram indicators expire after 5 seconds). Use an `asyncio.Task`
that loops independently and is cancelled when the response arrives.

---

### TG-011 — Long message splitting

Telegram hard limit: 4 096 characters per message.

Split on the last `\n` before the limit. Send first chunk via `reply_text`,
subsequent chunks via `send_message(reply_to_message_id=...)`. Add 100 ms
delay between chunks to preserve ordering.

---

### TG-012 — `/start` and `/new` commands

Both commands run the owner gate first (TG-025) and silently ignore non-owners.

**`/start`**
- No session: welcome message + prompt to type first task. Include a note that
  this conversation will also be visible in the Agent Canvas UI.
- Session exists: confirm active session, offer `/new` to start fresh.

**`/new`**
- `session_store.delete(chat_id)`.
- Reply: *"Starting a new conversation. What would you like help with?"*

---

### TG-013 — Error handling

| Situation | User-facing message |
|---|---|
| Missing `TELEGRAM_BOT_TOKEN` | Log + exit 1 at startup (never reaches users) |
| Agent server unreachable | *"⚠️ The agent server isn't reachable. Please check that it's running."* |
| Cloud auth rejected (401) | *"⚠️ Cloud authentication failed. Check your OPENHANDS_API_KEY."* |
| Agent returns error state | *"❌ The agent hit an error. Use `/new` to start a fresh conversation."* |
| Response timeout (> 120 s) | *"⏳ The agent is taking longer than expected. Your conversation is still running — check the Agent Canvas UI."* |
| Non-owner message | Silently ignore. No reply. |
| Unexpected exception | Log full traceback; reply *"Something went wrong. Try again or use `/new` to reset."* |

Never expose stack traces, internal URLs, or API keys to Telegram users.

---

### TG-014 — Graceful shutdown

On SIGTERM / SIGINT:
1. `bot.delete_webhook()` (webhook mode only).
2. Close all open WebSocket connections.
3. `session_store.close()`.
4. Exit 0.

---

### TG-015 — Agent-server authentication

- **Local**: add `X-Session-API-Key: {SESSION_API_KEY}` to every HTTP request
  and `?token={SESSION_API_KEY}` to every WebSocket URL when the key is set.
- **Cloud**: add `Authorization: Bearer {OPENHANDS_API_KEY}` to every HTTP
  request. WebSocket connections to the cloud sandbox use the session key
  returned with the conversation details, not the cloud API key.
- On HTTP 401 from either backend: log descriptive error and raise
  `AgentServerError` (see TG-013).

---

## Docker Integration

---

### TG-016 — Port assignment in `config/defaults.json`

Add to `"ports"`:
```json
"telegram": 18002
```

---

### TG-019 — Docker entrypoint and Dockerfile (primary deployment path)

This is the **primary** way to run the bridge. It requires zero extra setup
beyond the existing agent-canvas Docker image.

**`docker/entrypoint.sh`** shall add a Service #4 block immediately after
the automation server block and before the "wait for backends" block:

```bash
# ── 3b. Start Telegram Bridge (optional) ─────────────────────────────────────
TELEGRAM_PORT="${TELEGRAM_PORT:-${CONFIG_TELEGRAM_PORT:-18002}}"
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  log "Starting Telegram bridge on port $TELEGRAM_PORT..."
  openhands-telegram-bridge \
    --port "$TELEGRAM_PORT" &
  PIDS+=($!)
  log "Telegram bridge started (mode: ${TELEGRAM_WEBHOOK_URL:+webhook}${TELEGRAM_WEBHOOK_URL:-polling})"
else
  log "TELEGRAM_BOT_TOKEN not set — Telegram bridge disabled."
  log "To enable: docker run -e TELEGRAM_BOT_TOKEN=<token> ..."
fi
```

The bridge process shall inherit:
- `AGENT_SERVER_URL=http://127.0.0.1:${AGENT_SERVER_PORT}`
- `SESSION_API_KEY` (already exported by the entrypoint)
- `OPENHANDS_API_KEY` (pass-through from environment, enables cloud mode)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_CHAT_ID`, `TELEGRAM_WEBHOOK_URL`,
  `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_PORT`, `TELEGRAM_DB_PATH`

**`docker/Dockerfile`** shall install the bridge package in the build stage:
```dockerfile
COPY telegram/ /opt/agent-canvas/telegram/
RUN pip install /opt/agent-canvas/telegram/
```
The `openhands-telegram-bridge` binary must be on `PATH` in the final image.

**`config/defaults.json`** must export `CONFIG_TELEGRAM_PORT` via the
`config-gen` build step (same pattern as `CONFIG_AGENT_SERVER_PORT`).

---

### TG-017 — `scripts/dev-with-telegram.mjs`

A new launcher script for local development (non-Docker). Extends
`dev-with-automation.mjs` to also start the bridge via uvx:

```bash
uvx --from ./telegram openhands-telegram-bridge
```

Env vars passed: `AGENT_SERVER_URL`, `SESSION_API_KEY`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_WEBHOOK_URL`, `TELEGRAM_PORT`, `OPENHANDS_WORKING_DIR`.

If `TELEGRAM_BOT_TOKEN` is absent: print a setup message and start the rest
of the stack normally. Do not fail.

Add to `package.json`:
```json
"dev:telegram": "node scripts/dev-with-telegram.mjs"
```

---

### TG-020 — Ingress proxy routing

`scripts/ingress.mjs` shall add when the bridge is running:
```
/telegram/*  →  http://localhost:{telegramPort}
```

This lets webhook mode work through the unified ingress URL
(`https://your-domain.com/telegram/webhook`).

---

### TG-018 — `.env.sample` additions

```bash
# ── Telegram Bridge ─────────────────────────────────────────────────────────
# 1. Create a bot: message @BotFather on Telegram → /newbot → copy the token
TELEGRAM_BOT_TOKEN=

# 2. Your Telegram user ID (optional — first person to message the bot becomes
#    the owner automatically if this is blank).
#    Find yours by messaging @userinfobot on Telegram.
TELEGRAM_OWNER_CHAT_ID=

# 3. Cloud mode (optional — for always-on conversations):
#    Set your OpenHands Cloud API key. Leave blank to use the local agent-server.
OPENHANDS_API_KEY=

# 4. Webhook URL (optional — leave blank for polling mode, which works locally
#    with no public URL or port-forwarding needed).
TELEGRAM_WEBHOOK_URL=
```

---

## Frontend Settings

The Telegram integration lives under **Settings → Integrations**, alongside
any future integrations (Slack, Discord, etc.). It does NOT get its own
top-level nav item.

---

### TG-021 — Settings page

`src/routes/telegram-settings.tsx` shall render the Telegram settings page at
`/settings/integrations/telegram`.

**Layout:**

1. **Header**: `SETTINGS$TELEGRAM_TITLE` (`"Telegram Integration"`)
2. **Bot Token field**: masked input, label `SETTINGS$TELEGRAM_TOKEN_LABEL`.
   Help text: `SETTINGS$TELEGRAM_TOKEN_HELP` with a link to
   `https://t.me/BotFather`.
   Saved via `SecretsService` key `TELEGRAM_BOT_TOKEN`.
3. **Owner Chat ID field**: plain text input, label
   `SETTINGS$TELEGRAM_OWNER_LABEL`. Optional. Explain: *"Leave blank — the
   first person to message the bot is set as the owner automatically."*
4. **Connection status badge** (TG-023).
5. **How it works** section — one paragraph explaining:
   - Polling mode means no public URL needed.
   - Conversations appear in the Agent Canvas UI too.
   - `/new` command resets the conversation.
6. **Cloud mode notice**: if `OPENHANDS_API_KEY` is detected in settings,
   show *"☁️ Cloud mode active — conversations persist when the server is
   offline."*

---

### TG-022 — Integrations nav section

The Settings sidebar shall have an **Integrations** group (or reuse one if
it already exists) containing a `Telegram` entry at
`/settings/integrations/telegram`.

- Icon: `MessageSquare` or the nearest available equivalent.
- Label: `I18nKey.SETTINGS$NAV_TELEGRAM`.
- Visible in local mode only (non-cloud), consistent with other
  local-only settings entries.

---

### TG-023 — Connection status badge

The settings page shall poll `GET http://localhost:{TELEGRAM_PORT}/health`
every 5 seconds (React Query, no retry on 404) and render:

| Bridge response | Display |
|---|---|
| `{status:"ok", mode:"polling"}` | 🟢 `SETTINGS$TELEGRAM_STATUS_RUNNING_POLLING` |
| `{status:"ok", mode:"webhook"}` | 🟢 `SETTINGS$TELEGRAM_STATUS_RUNNING_WEBHOOK` |
| Unreachable | 🔴 `SETTINGS$TELEGRAM_STATUS_NOT_RUNNING` |

---

### TG-024 — i18n keys

Add to `src/i18n/translation.json`. All 15 languages receive the English value
as fallback initially. Run `npm run make-i18n` after adding.

| Key | English value |
|---|---|
| `SETTINGS$NAV_TELEGRAM` | `"Telegram"` |
| `SETTINGS$TELEGRAM_TITLE` | `"Telegram Integration"` |
| `SETTINGS$TELEGRAM_TOKEN_LABEL` | `"Bot Token"` |
| `SETTINGS$TELEGRAM_TOKEN_HELP` | `"Get your token from @BotFather on Telegram."` |
| `SETTINGS$TELEGRAM_OWNER_LABEL` | `"Your Telegram Chat ID (optional)"` |
| `SETTINGS$TELEGRAM_OWNER_HELP` | `"Leave blank — the first person to message the bot becomes the owner automatically. Find your ID by messaging @userinfobot."` |
| `SETTINGS$TELEGRAM_STATUS_RUNNING_POLLING` | `"Bridge running · polling mode"` |
| `SETTINGS$TELEGRAM_STATUS_RUNNING_WEBHOOK` | `"Bridge running · webhook mode"` |
| `SETTINGS$TELEGRAM_STATUS_NOT_RUNNING` | `"Bridge not running"` |
| `SETTINGS$TELEGRAM_CLOUD_ACTIVE` | `"☁️ Cloud mode active — conversations persist when the server is offline."` |
| `SETTINGS$TELEGRAM_SAVE_SUCCESS` | `"Telegram settings saved."` |

---

### TG-028 — `telegram/README.md`

The README is the user-facing setup guide. It must be clear enough for a
non-developer who has Docker and a Telegram account.

**Sections:**

1. **What this is**: *"Chat with your Agent Canvas agent from any Telegram
   client. Uses the same agent, same LLM, same conversation history as the
   browser UI."*

2. **Prerequisites**: Telegram account only. Nothing else to install.

3. **Step 1 — Create your bot (2 minutes)**:
   - Open Telegram, search for `@BotFather`
   - Send `/newbot`
   - Choose a name and username
   - Copy the token (looks like `1234567890:ABCdef...`)

4. **Step 2 — Run (one command)**:
   ```bash
   docker run -e TELEGRAM_BOT_TOKEN=<your_token> -p 8000:8000 \
     ghcr.io/openhands/agent-canvas
   ```
   Open Telegram, find your bot by its username, send `/start`. Done.

5. **Step 3 — Always-on with OpenHands Cloud**:
   ```bash
   docker run -e TELEGRAM_BOT_TOKEN=<token> \
              -e OPENHANDS_API_KEY=<cloud_key> \
              -p 8000:8000 \
              ghcr.io/openhands/agent-canvas
   ```
   Conversations persist in the cloud even when the container stops.

6. **How ownership works**: First message → you are the owner. All other
   Telegram users are silently ignored. To pre-configure: set
   `TELEGRAM_OWNER_CHAT_ID` (find your ID by messaging `@userinfobot`).

7. **Commands**:
   - `/start` — shows status, prompts for first message
   - `/new` — resets to a fresh conversation

8. **Your conversation in the browser too**: Every Telegram message creates
   or continues a real Agent Canvas conversation. Open
   `http://localhost:8000` (local) or `app.all-hands.dev` (cloud) and you
   will see the same conversation history. You can pick up mid-task in
   either place.

9. **Troubleshooting**:
   - *Bot does not respond*: Check `TELEGRAM_BOT_TOKEN` is correct. Run
     `docker logs <container>` and look for `[telegram]` lines.
   - *"Agent server isn't reachable"*: The container is still starting.
     Wait 10–15 seconds.
   - *Wrong cloud key*: Set `OPENHANDS_API_KEY` correctly or unset it to
     fall back to local mode.

---

## Out of scope for this spec

The following are explicitly deferred to future specs:

- **Automation notifications via Telegram**: automations sending a DM when
  they complete. This is a separate notification feature, not part of the
  bridge.
- **Multiple conversations via Telegram**: threading, channels, or group
  chats for parallel agent sessions. Phase 1 is one conversation per owner.
- **Media input**: photos, voice notes, files sent to the bot. Phase 1 is
  text only.
- **Inline keyboard buttons** or rich Telegram UI components.

---

## Implementation Checklist

Work in the `20260527-telegram` branch of `jamiechicago312/agent-canvas`.
Complete items in order — later items depend on earlier ones.

**Python service**
- [ ] **TG-001** `telegram/pyproject.toml` + package skeleton
- [ ] **TG-002** `bridge/config.py` — env vars, startup validation, mode logging
- [ ] **TG-003** `bridge/session.py` — SQLite store with owner table
- [ ] **TG-025** Owner gate in session store + handler guard
- [ ] **TG-004** `bridge/app.py` — webhook endpoint + `/health`
- [ ] **TG-005** Polling mode as default in `bridge/__main__.py`
- [ ] **TG-026** `bridge/agent_client.py` — local + cloud `create_conversation()`
- [ ] **TG-008** `bridge/agent_client.py` — local + cloud `send_message()`
- [ ] **TG-009** `bridge/agent_client.py` — `wait_for_response()`
- [ ] **TG-015** Auth headers on all agent-server calls
- [ ] **TG-006** `bridge/handlers.py` — message handler (owner gate + full flow)
- [ ] **TG-012** `/start` and `/new` handlers
- [ ] **TG-010** Typing indicator keep-alive
- [ ] **TG-011** Long message splitting
- [ ] **TG-013** Error handling + user messages
- [ ] **TG-014** Graceful shutdown

**Docker + config**
- [ ] **TG-016** `config/defaults.json` — add `ports.telegram: 18002`
- [ ] **TG-019** `docker/Dockerfile` — `pip install ./telegram/`
- [ ] **TG-019** `docker/entrypoint.sh` — Service #4 block

**Dev stack**
- [ ] **TG-017** `scripts/dev-with-telegram.mjs` + `package.json` script
- [ ] **TG-020** `scripts/ingress.mjs` — `/telegram/*` routing
- [ ] **TG-018** `.env.sample` additions

**Frontend**
- [ ] **TG-024** `src/i18n/translation.json` keys + `npm run make-i18n`
- [ ] **TG-022** Integrations nav section in settings sidebar
- [ ] **TG-021** `src/routes/telegram-settings.tsx` settings page
- [ ] **TG-023** Connection status badge + React Query poll

**Docs**
- [ ] **TG-028** `telegram/README.md`
