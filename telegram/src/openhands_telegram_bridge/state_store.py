from __future__ import annotations

from pathlib import Path
import sqlite3


class TelegramStateStore:
    def __init__(self, path: Path):
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)"
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def get(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            conn.commit()

    def delete(self, key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM state WHERE key = ?", (key,))
            conn.commit()

    def get_conversation_id(self) -> str | None:
        return self.get("conversation_id")

    def set_conversation_id(self, conversation_id: str) -> None:
        self.set("conversation_id", conversation_id)

    def clear_conversation_id(self) -> None:
        self.delete("conversation_id")

    def get_update_offset(self) -> int | None:
        raw = self.get("update_offset")
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def set_update_offset(self, update_id: int) -> None:
        self.set("update_offset", str(update_id))
