"""Historique des transcriptions, traductions et voix doublées d'un média (table version_history).

Chaque génération est gardée et appliquée d'office ; on peut revenir à une ancienne (`activate`). La version
appliquée vit dans les tables transcript, translation et dub ; ses corrections suivent dans l'historique."""
from __future__ import annotations

import sqlite3

from ..db.database import Database


class HistoryService:
    def __init__(self, db: Database):
        self.db = db

    def add(self, asset_id: int, kind: str, language: str, engine: str, data_json: str, directory: str = "",
            label: str = "") -> int:
        """Nouvelle version, aussitôt appliquée (les autres restent dans l'historique)."""
        with self.db.transaction() as c:
            c.execute("UPDATE version_history SET active = 0 WHERE source_asset_id = ? AND kind = ? AND language = ?",
                      (asset_id, kind, language or ""))
            return c.execute("INSERT INTO version_history(source_asset_id, kind, language, engine, label, data_json, "
                             "directory, active) VALUES (?,?,?,?,?,?,?,1)",
                             (asset_id, kind, language or "", engine or "", label or "", data_json,
                              directory or "")).lastrowid

    def update_active(self, asset_id: int, kind: str, language: str, data_json: str) -> None:
        """Corrections de la version appliquée : gardées avec elle."""
        self.db.connect().execute("UPDATE version_history SET data_json = ? WHERE source_asset_id = ? AND kind = ? "
                                  "AND language = ? AND active = 1", (data_json, asset_id, kind, language or ""))

    def list(self, asset_id: int, kind: str, language: str = "") -> list[sqlite3.Row]:
        """Versions, la plus récente d'abord."""
        return self.db.query("SELECT * FROM version_history WHERE source_asset_id = ? AND kind = ? AND language = ? "
                             "ORDER BY id DESC", (asset_id, kind, language or ""))

    def get(self, hid: int) -> sqlite3.Row | None:
        return self.db.query_one("SELECT * FROM version_history WHERE id = ?", (hid,))

    def activate(self, hid: int) -> sqlite3.Row | None:
        r = self.get(hid)
        if r is None:
            return None
        with self.db.transaction() as c:
            c.execute("UPDATE version_history SET active = (id = ?) WHERE source_asset_id = ? AND kind = ? "
                      "AND language = ?", (hid, r["source_asset_id"], r["kind"], r["language"]))
        return r
