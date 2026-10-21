"""Paramètres de l'application (table app_setting)."""
from __future__ import annotations

import json
from typing import Any

from ..db.database import Database

DEFAULTS: dict[str, Any] = {
    "theme": "dark",
    "update_channel": "stable",
    "max_workers": 2,
}


class SettingsService:
    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.query_one("SELECT value FROM app_setting WHERE key = ?", (key,))
        if row is None:
            return DEFAULTS.get(key, default)
        return json.loads(row["value"])

    def set(self, key: str, value: Any) -> None:
        self.db.connect().execute(
            "INSERT INTO app_setting(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)))
