from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from .config import AppConfig, TELEGRAM_SECRET_NAME

DEFAULT_LLM_MODEL = "openhands/minimax-m2.7"
DEFAULT_TOOLS = [
    {"name": "terminal", "params": {}},
    {"name": "file_editor", "params": {}},
    {"name": "task_tracker", "params": {}},
    {"name": "canvas_ui", "params": {}},
]
TOOL_MODULE_QUALNAMES = {"canvas_ui": "canvas_ui_tool"}
TERMINAL_STATUSES = {"finished", "idle", "error", "stuck", "paused"}


class AgentServerClient:
    def __init__(self, config: AppConfig):
        headers = {}
        if config.session_api_key:
            headers["X-Session-API-Key"] = config.session_api_key
        self._lookup_headers = dict(headers)
        self._working_dir = config.working_dir
        self._client = httpx.AsyncClient(
            base_url=config.agent_server_url,
            headers=headers,
            timeout=httpx.Timeout(60.0, connect=10.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get_settings(self, expose_secrets: str | None = None) -> dict[str, Any]:
        headers = {"X-Expose-Secrets": expose_secrets} if expose_secrets else None
        response = await self._client.get("/api/settings", headers=headers)
        response.raise_for_status()
        return response.json()

    async def list_secrets(self) -> list[dict[str, Any]]:
        response = await self._client.get("/api/settings/secrets")
        response.raise_for_status()
        payload = response.json()
        secrets = payload.get("secrets") if isinstance(payload, dict) else []
        return secrets if isinstance(secrets, list) else []

    async def get_secret_value(self, name: str) -> str | None:
        response = await self._client.get(f"/api/settings/secrets/{quote(name, safe='')}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.text.strip() or None

    async def get_telegram_token(self) -> str | None:
        return await self.get_secret_value(TELEGRAM_SECRET_NAME)

    async def create_conversation(self, initial_text: str | None = None) -> str:
        settings = await self.get_settings(expose_secrets="encrypted")
        secrets = await self.list_secrets()
        payload = self._build_start_conversation_payload(settings, secrets, initial_text)
        response = await self._client.post("/api/conversations", json=payload)
        response.raise_for_status()
        data = response.json()
        conversation_id = data.get("id")
        if not isinstance(conversation_id, str) or not conversation_id:
            raise RuntimeError("Agent server did not return a conversation id")
        return conversation_id

    async def send_message(self, conversation_id: str, text: str) -> None:
        response = await self._client.post(
            f"/api/conversations/{conversation_id}/events",
            json={
                "role": "user",
                "content": [{"type": "text", "text": text}],
                "run": True,
            },
        )
        response.raise_for_status()

    async def get_conversation_status(self, conversation_id: str) -> str | None:
        response = await self._client.get(f"/api/conversations/{conversation_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        status = data.get("execution_status") or data.get("status")
        return status if isinstance(status, str) else None

    async def search_events(
        self,
        conversation_id: str,
        since_timestamp: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": 200}
        if since_timestamp:
            params["timestamp__gte"] = since_timestamp
        response = await self._client.get(
            f"/api/conversations/{conversation_id}/events/search",
            params=params,
        )
        response.raise_for_status()
        data = response.json()
        items = data.get("items") if isinstance(data, dict) else []
        return items if isinstance(items, list) else []

    async def wait_for_response(
        self,
        conversation_id: str,
        since_timestamp: str,
        timeout_seconds: int = 600,
    ) -> str:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        fallback_message: str | None = None

        while asyncio.get_running_loop().time() < deadline:
            events = await self.search_events(conversation_id, since_timestamp)
            message = self._extract_message_from_events(events)
            if message:
                return message

            status = await self.get_conversation_status(conversation_id)
            if status is None:
                return "The conversation no longer exists."
            if status in {"error", "stuck"}:
                return fallback_message or "The agent stopped with an error."
            if status in TERMINAL_STATUSES and fallback_message:
                return fallback_message

            fallback_message = self._extract_fallback_message(events)
            await asyncio.sleep(2)

        raise TimeoutError("Timed out waiting for the agent response")

    def _build_start_conversation_payload(
        self,
        settings_response: dict[str, Any],
        secrets: list[dict[str, Any]],
        initial_text: str | None,
    ) -> dict[str, Any]:
        agent_settings = deepcopy(settings_response.get("agent_settings") or {})
        conversation_settings = deepcopy(settings_response.get("conversation_settings") or {})

        llm = agent_settings.get("llm") if isinstance(agent_settings.get("llm"), dict) else {}
        llm = deepcopy(llm)
        llm["model"] = (
            llm.get("model")
            or settings_response.get("llm_model")
            or DEFAULT_LLM_MODEL
        )
        if not self._non_empty_string(llm.get("api_key")):
            top_level_api_key = settings_response.get("llm_api_key")
            if self._non_empty_string(top_level_api_key):
                llm["api_key"] = top_level_api_key
            else:
                llm.pop("api_key", None)
        if not self._non_empty_string(llm.get("base_url")):
            top_level_base_url = settings_response.get("llm_base_url")
            if self._non_empty_string(top_level_base_url):
                llm["base_url"] = top_level_base_url
            else:
                llm.pop("base_url", None)
        agent_settings["llm"] = llm

        agent_settings["agent_context"] = {
            **(agent_settings.get("agent_context") or {}),
            "load_public_skills": True,
            "load_user_skills": True,
        }
        agent_settings["tools"] = DEFAULT_TOOLS
        agent_settings.pop("acp_server", None)
        for key in (
            "acp_command",
            "acp_args",
            "acp_env",
            "acp_model",
            "acp_session_mode",
            "acp_prompt_timeout",
        ):
            agent_settings.pop(key, None)

        mcp_config = agent_settings.get("mcp_config")
        if not isinstance(mcp_config, dict) or "mcpServers" not in mcp_config:
            agent_settings.pop("mcp_config", None)

        for key in (
            "schema_version",
            "agent_settings",
            "workspace",
            "conversation_id",
            "initial_message",
            "plugins",
        ):
            conversation_settings.pop(key, None)

        payload: dict[str, Any] = {
            "agent_settings": agent_settings,
            "workspace": {
                "kind": "LocalWorkspace",
                "working_dir": self._working_dir,
            },
            "confirmation_policy": self._build_confirmation_policy(conversation_settings),
            "max_iterations": conversation_settings.get("max_iterations") or 500,
            "stuck_detection": True,
            "autotitle": True,
            "worktree": True,
            "secrets_encrypted": True,
            "tool_module_qualnames": TOOL_MODULE_QUALNAMES,
        }

        security_analyzer = self._build_security_analyzer(conversation_settings)
        if security_analyzer:
            payload["security_analyzer"] = security_analyzer

        if initial_text:
            payload["initial_message"] = {
                "role": "user",
                "content": [{"type": "text", "text": initial_text}],
                "run": True,
            }

        lookup_secrets = self._build_lookup_secrets(secrets)
        if lookup_secrets:
            payload["secrets"] = lookup_secrets

        return payload

    def _build_lookup_secrets(
        self,
        secrets: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        result: dict[str, Any] = {}
        for secret in secrets:
            name = secret.get("name")
            if not isinstance(name, str) or not name or name == TELEGRAM_SECRET_NAME:
                continue
            entry: dict[str, Any] = {
                "kind": "LookupSecret",
                "url": f"/api/settings/secrets/{quote(name, safe='')}",
            }
            description = secret.get("description")
            if isinstance(description, str) and description.strip():
                entry["description"] = description.strip()
            if self._lookup_headers:
                entry["headers"] = self._lookup_headers
            result[name] = entry
        return result or None

    @staticmethod
    def _build_confirmation_policy(conversation_settings: dict[str, Any]) -> dict[str, Any]:
        if conversation_settings.get("confirmation_mode") is not True:
            return {"kind": "NeverConfirm"}
        if conversation_settings.get("security_analyzer") == "llm":
            return {"kind": "ConfirmRisky", "threshold": "HIGH", "confirm_unknown": True}
        return {"kind": "AlwaysConfirm"}

    @staticmethod
    def _build_security_analyzer(conversation_settings: dict[str, Any]) -> dict[str, str] | None:
        analyzer = conversation_settings.get("security_analyzer")
        if analyzer == "llm":
            return {"kind": "LLMSecurityAnalyzer"}
        if analyzer == "pattern":
            return {"kind": "PatternSecurityAnalyzer"}
        if analyzer == "policy_rail":
            return {"kind": "PolicyRailSecurityAnalyzer"}
        return None

    @staticmethod
    def _extract_message_from_events(events: list[dict[str, Any]]) -> str | None:
        latest: str | None = None
        for event in events:
            if not isinstance(event, dict):
                continue
            kind = event.get("kind")
            if kind == "ActionEvent":
                action = event.get("action") if isinstance(event.get("action"), dict) else {}
                if action.get("kind") == "FinishAction":
                    message = action.get("message")
                    if isinstance(message, str) and message.strip():
                        latest = message.strip()
            elif kind == "ObservationEvent":
                observation = event.get("observation") if isinstance(event.get("observation"), dict) else {}
                if observation.get("kind") == "FinishObservation":
                    parts = []
                    for content in observation.get("content") or []:
                        if isinstance(content, dict) and content.get("type") == "text":
                            text = content.get("text")
                            if isinstance(text, str) and text.strip():
                                parts.append(text.strip())
                    if parts:
                        latest = "\n\n".join(parts)
            elif kind in {"ConversationErrorEvent", "ServerErrorEvent"}:
                detail = event.get("detail")
                if isinstance(detail, str) and detail.strip():
                    latest = detail.strip()
        return latest

    @staticmethod
    def _extract_fallback_message(events: list[dict[str, Any]]) -> str | None:
        latest: str | None = None
        for event in events:
            if not isinstance(event, dict):
                continue
            detail = event.get("detail")
            if isinstance(detail, str) and detail.strip():
                latest = detail.strip()
        return latest

    @staticmethod
    def _non_empty_string(value: Any) -> bool:
        return isinstance(value, str) and bool(value.strip())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
