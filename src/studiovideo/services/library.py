"""Bibliothèque centralisée : recherche et consultation des médias."""
from __future__ import annotations

import sqlite3

from ..db.database import Database


class LibraryService:
    def __init__(self, db: Database):
        self.db = db

    def search(self, text: str = "", kind: str | None = None, concept_id: int | None = None,
               favorites_only: bool = False, limit: int = 500) -> list[sqlite3.Row]:
        where, params = [], []
        if text:
            where.append("(a.file_name LIKE ? OR a.tags LIKE ? OR a.origin_url LIKE ?)")
            params += [f"%{text}%"] * 3
        if kind:
            where.append("a.kind = ?")
            params.append(kind)
        if concept_id:
            where.append("p.concept_id = ?")
            params.append(concept_id)
        if favorites_only:
            where.append("a.favorite = 1")
        sql = ("SELECT a.*, p.name project_name, c.name concept_name FROM source_asset a "
               "LEFT JOIN project p ON p.id = a.project_id LEFT JOIN concept c ON c.id = p.concept_id")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY a.imported_at DESC, a.id DESC LIMIT ?"
        return self.db.query(sql, (*params, limit))

    def get(self, asset_id: int) -> sqlite3.Row | None:
        return self.db.query_one("SELECT * FROM source_asset WHERE id = ?", (asset_id,))

    def set_favorite(self, asset_id: int, fav: bool) -> None:
        self.db.connect().execute("UPDATE source_asset SET favorite = ? WHERE id = ?", (int(fav), asset_id))

    def set_tags(self, asset_id: int, tags: str) -> None:
        self.db.connect().execute("UPDATE source_asset SET tags = ? WHERE id = ?", (tags, asset_id))

    def remove(self, asset_id: int) -> None:
        """Retire le média de la bibliothèque. Le fichier source n'est jamais supprimé."""
        with self.db.transaction() as c:
            c.execute("DELETE FROM source_asset WHERE id = ?", (asset_id,))
        self.db.audit("asset.remove", str(asset_id))

    def counts(self) -> dict[str, int]:
        return {r["kind"]: r["n"] for r in self.db.query("SELECT kind, COUNT(*) n FROM source_asset GROUP BY kind")}
