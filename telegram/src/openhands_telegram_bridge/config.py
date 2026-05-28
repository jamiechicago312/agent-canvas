from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
from typing import Literal

TELEGRAM_SECRET_NAME = "TELEGRAM_BOT_TOKEN"
TELEGRAM_SETTINGS_KEY = "telegram_integration"
DEFAULT_WORKING_DIR = "workspace/project"
DEFAULT_DB_PATH = Path.home() / ".openhands" / "agent-canvas" / "telegram.db"
DEFAULT_AGENT_SERVER_URL = "http://127.0.0.1:18000"
TelegramMode = Literal["polling", "webhook"]


@dataclass(frozen=True)
class AppConfig:
    agent_server_url: str
    session_api_key: str | None
    port: int
    working_dir: str
    db_path: Path


@dataclass(frozen=True)
class TelegramIntegrationConfig:
    enabled: bool = False
    token: str | None = None
    owner_chat_id: str | None = None
    mode: TelegramMode = "polling"
    webhook_url: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.token)


def _trim_to_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def load_config() -> AppConfig:
    agent_server_url = os.environ.get("AGENT_SERVER_URL", DEFAULT_AGENT_SERVER_URL)
    session_api_key = _trim_to_none(os.environ.get("SESSION_API_KEY"))
    port = int(os.environ.get("TELEGRAM_PORT", "18002"))
    working_dir = os.environ.get("OPENHANDS_WORKING_DIR", DEFAULT_WORKING_DIR).strip()
    db_path = Path(os.environ.get("TELEGRAM_DB_PATH", str(DEFAULT_DB_PATH))).expanduser()
    return AppConfig(
        agent_server_url=agent_server_url.rstrip("/"),
        session_api_key=session_api_key,
        port=port,
        working_dir=working_dir,
        db_path=db_path,
    )


def parse_telegram_config(
    agent_settings: object,
    token: str | None,
) -> TelegramIntegrationConfig:
    settings = agent_settings if isinstance(agent_settings, dict) else {}
    raw = settings.get(TELEGRAM_SETTINGS_KEY)
    telegram = raw if isinstance(raw, dict) else {}

    mode = telegram.get("mode")
    normalized_mode: TelegramMode = "webhook" if mode == "webhook" else "polling"

    owner_chat_id = telegram.get("owner_chat_id")
    owner_value = str(owner_chat_id).strip() if owner_chat_id is not None else None

    webhook_url = _trim_to_none(telegram.get("webhook_url"))

    return TelegramIntegrationConfig(
        enabled=telegram.get("enabled") is True,
        token=token,
        owner_chat_id=owner_value or None,
        mode=normalized_mode,
        webhook_url=webhook_url,
    )


def build_webhook_target(url: str) -> str:
    trimmed = url.rstrip("/")
    if trimmed.endswith("/telegram/webhook"):
        return trimmed
    return f"{trimmed}/telegram/webhook"
