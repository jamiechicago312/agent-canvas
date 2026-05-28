from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import logging
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, status

from .agent_server import AgentServerClient, now_iso
from .config import (
    AppConfig,
    TelegramIntegrationConfig,
    build_webhook_target,
    load_config,
)
from .state_store import TelegramStateStore

logging.basicConfig(level=logging.INFO, format="[telegram] %(message)s")
logger = logging.getLogger("telegram-bridge")


class TelegramBridgeService:
    def __init__(self, config: AppConfig):
        self._config = config
        self._agent_server = AgentServerClient(config)
        self._state_store = TelegramStateStore(config.db_path)
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=10.0))
        self._runtime_lock = asyncio.Lock()
        self._message_lock = asyncio.Lock()
        self._polling_task: asyncio.Task[None] | None = None
        self._current = TelegramIntegrationConfig()
        self._last_error: str | None = None
        self._running = False

    async def start(self) -> None:
        await self.reload()

    async def shutdown(self) -> None:
        await self._stop_runtime(clear_remote_webhook=True)
        await self._agent_server.close()
        await self._http.aclose()

    async def reload(self) -> dict[str, Any]:
        async with self._runtime_lock:
            self._last_error = None
            try:
                config = await self._agent_server.get_telegram_config()
                self._current = config
                if not config.enabled:
                    await self._stop_runtime(clear_remote_webhook=True)
                    return self.status_payload()
                if not config.configured:
                    await self._stop_runtime(clear_remote_webhook=True)
                    self._last_error = "Telegram is enabled but no bot token is saved yet."
                    return self.status_payload()
                await self._validate_token(config)
                if config.mode == "webhook":
                    if not config.webhook_url:
                        await self._stop_runtime(clear_remote_webhook=True)
                        self._last_error = "Webhook mode requires a public URL."
                        return self.status_payload()
                    await self._start_webhook(config)
                else:
                    await self._start_polling(config)
            except Exception as error:  # noqa: BLE001
                await self._stop_runtime(clear_remote_webhook=False)
                self._last_error = str(error)
                logger.error("Failed to reload Telegram integration: %s", error)
            return self.status_payload()

    def status_payload(self) -> dict[str, Any]:
        status_code = "disabled"
        if self._last_error:
            status_code = "error"
        elif not self._current.enabled:
            status_code = "disabled"
        elif not self._current.configured:
            status_code = "not_configured"
        elif self._running and self._current.mode == "webhook":
            status_code = "running_webhook"
        elif self._running:
            status_code = "running_polling"

        return {
            "status": status_code,
            "enabled": self._current.enabled,
            "configured": self._current.configured,
            "running": self._running,
            "mode": self._current.mode,
            "owner_chat_id": self._current.owner_chat_id,
            "webhook_url": self._current.webhook_url,
            "last_error": self._last_error,
        }

    async def handle_webhook(self, payload: dict[str, Any]) -> None:
        if not self._current.enabled or self._current.mode != "webhook":
            return
        await self._handle_update(payload)

    async def _start_polling(self, config: TelegramIntegrationConfig) -> None:
        await self._stop_runtime(clear_remote_webhook=False)
        await self._delete_webhook(config.token)
        self._polling_task = asyncio.create_task(self._poll_updates(), name="telegram-polling")
        self._running = True
        logger.info("Running in polling mode")

    async def _start_webhook(self, config: TelegramIntegrationConfig) -> None:
        await self._stop_runtime(clear_remote_webhook=False)
        target = build_webhook_target(config.webhook_url or "")
        await self._call_telegram_api(config.token, "setWebhook", json={"url": target})
        self._running = True
        logger.info("Webhook registered at %s", target)

    async def _stop_runtime(self, clear_remote_webhook: bool) -> None:
        polling_task = self._polling_task
        self._polling_task = None
        if polling_task:
            polling_task.cancel()
            with suppress(asyncio.CancelledError):
                await polling_task
        if clear_remote_webhook and self._current.token:
            with suppress(Exception):
                await self._delete_webhook(self._current.token)
        self._running = False

    async def _validate_token(self, config: TelegramIntegrationConfig) -> None:
        await self._call_telegram_api(config.token, "getMe")

    async def _delete_webhook(self, token: str | None) -> None:
        if token:
            await self._call_telegram_api(token, "deleteWebhook", json={"drop_pending_updates": False})

    async def _poll_updates(self) -> None:
        logger.info("Polling task started")
        try:
            while True:
                offset = self._state_store.get_update_offset()
                payload: dict[str, Any] = {"timeout": 30}
                if offset is not None:
                    payload["offset"] = offset + 1
                response = await self._call_telegram_api(
                    self._current.token,
                    "getUpdates",
                    json=payload,
                )
                for update in response.get("result", []):
                    if isinstance(update, dict):
                        update_id = update.get("update_id")
                        if isinstance(update_id, int):
                            self._state_store.set_update_offset(update_id)
                        await self._handle_update(update)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            self._last_error = str(error)
            self._running = False
            logger.error("Polling loop failed: %s", error)
            raise

    async def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            return
        text = message.get("text")
        chat = message.get("chat")
        if not isinstance(text, str) or not isinstance(chat, dict):
            return
        chat_id = chat.get("id")
        if chat_id is None:
            return
        await self._handle_text_message(str(chat_id), text.strip())

    async def _handle_text_message(self, chat_id: str, text: str) -> None:
        if not text:
            return
        owner_assigned = await self._ensure_owner(chat_id)
        if not owner_assigned:
            logger.info("Ignoring message from unknown chat_id=%s", chat_id)
            return
        if text == "/start":
            await self._send_long_message(
                chat_id,
                "Telegram is connected to Agent Canvas. Send me a task to start working, or use /new for a fresh conversation.",
            )
            return
        if text == "/new":
            self._state_store.clear_conversation_id()
            await self._send_long_message(
                chat_id,
                "Started a fresh conversation. Send your next message when you're ready.",
            )
            return
        async with self._message_lock:
            conversation_id = self._state_store.get_conversation_id()
            since = now_iso()
            try:
                if conversation_id:
                    await self._agent_server.send_message(conversation_id, text)
                else:
                    conversation_id = await self._agent_server.create_conversation(text)
                    self._state_store.set_conversation_id(conversation_id)
                typing_task = asyncio.create_task(self._typing_keepalive(chat_id))
                try:
                    reply = await self._agent_server.wait_for_response(conversation_id, since)
                finally:
                    typing_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await typing_task
                await self._send_long_message(chat_id, reply)
            except httpx.HTTPStatusError as error:
                if error.response.status_code == status.HTTP_404_NOT_FOUND:
                    self._state_store.clear_conversation_id()
                    await self._send_long_message(
                        chat_id,
                        "That conversation is no longer available. Send the message again and I'll start a new one.",
                    )
                    return
                raise
            except TimeoutError:
                await self._send_long_message(
                    chat_id,
                    "The agent is still working. Try again in a moment from Telegram or the browser UI.",
                )
            except Exception as error:  # noqa: BLE001
                logger.error("Message handling failed: %s", error)
                await self._send_long_message(chat_id, f"Telegram bridge error: {error}")

    async def _ensure_owner(self, chat_id: str) -> bool:
        owner = self._current.owner_chat_id
        if owner:
            return owner == chat_id
        self._current = TelegramIntegrationConfig(
            enabled=self._current.enabled,
            token=self._current.token,
            owner_chat_id=chat_id,
            mode=self._current.mode,
            webhook_url=self._current.webhook_url,
        )
        await self._agent_server.save_telegram_config(self._current)
        logger.info("Owner set to chat_id=%s on first message", chat_id)
        return True

    async def _typing_keepalive(self, chat_id: str) -> None:
        while True:
            with suppress(Exception):
                await self._call_telegram_api(
                    self._current.token,
                    "sendChatAction",
                    json={"chat_id": chat_id, "action": "typing"},
                )
            await asyncio.sleep(4)

    async def _send_long_message(self, chat_id: str, text: str) -> None:
        for chunk in self._split_message(text):
            await self._call_telegram_api(
                self._current.token,
                "sendMessage",
                json={"chat_id": chat_id, "text": chunk},
            )

    async def _call_telegram_api(
        self,
        token: str | None,
        method: str,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not token:
            raise RuntimeError("Telegram bot token is not configured")
        response = await self._http.post(
            f"https://api.telegram.org/bot{token}/{method}",
            json=json,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            description = payload.get("description") or "Telegram API request failed"
            raise RuntimeError(str(description))
        return payload

    @staticmethod
    def _split_message(text: str, limit: int = 4000) -> list[str]:
        normalized = text.strip() or "Done."
        if len(normalized) <= limit:
            return [normalized]
        parts: list[str] = []
        remaining = normalized
        while len(remaining) > limit:
            split_at = remaining.rfind("\n\n", 0, limit)
            if split_at == -1:
                split_at = remaining.rfind("\n", 0, limit)
            if split_at == -1:
                split_at = remaining.rfind(" ", 0, limit)
            if split_at == -1:
                split_at = limit
            parts.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()
        if remaining:
            parts.append(remaining)
        return [part for part in parts if part]


@asynccontextmanager
async def lifespan(app: FastAPI):
    service: TelegramBridgeService = app.state.telegram_service
    await service.start()
    try:
        yield
    finally:
        await service.shutdown()


config = load_config()
service = TelegramBridgeService(config)
app = FastAPI(title="OpenHands Telegram Bridge", lifespan=lifespan)
app.state.telegram_service = service


def _require_api_key(
    x_session_api_key: str | None,
    current_config: AppConfig,
) -> None:
    expected = current_config.session_api_key
    if expected and x_session_api_key != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session API key")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok"}


@app.get("/api/integrations/telegram/status")
async def telegram_status(
    x_session_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_api_key(x_session_api_key, config)
    return service.status_payload()


@app.post("/api/integrations/telegram/reload")
async def telegram_reload(
    x_session_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_api_key(x_session_api_key, config)
    return await service.reload()


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> dict[str, bool]:
    payload = await request.json()
    if isinstance(payload, dict):
        await service.handle_webhook(payload)
    return {"ok": True}
