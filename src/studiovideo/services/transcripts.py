"""Stockage des transcriptions : résultat original conservé, corrections séparées (§6)."""
from __future__ import annotations

from ..db.database import Database
from ..transcribe.model import Transcript


class TranscriptService:
    def __init__(self, db: Database):
        self.db = db

    def _row(self, asset_id: int):
        return self.db.query_one("SELECT * FROM transcript WHERE source_asset_id = ? ORDER BY id DESC LIMIT 1",
                                 (asset_id,))

    def has(self, asset_id: int) -> bool:
        return self._row(asset_id) is not None

    def get(self, asset_id: int) -> Transcript | None:
        """Version courante : corrigée si elle existe, sinon originale."""
        r = self._row(asset_id)
        if r is None:
            return None
        return Transcript.from_json(r["edited_json"] or r["original_json"])

    def original(self, asset_id: int) -> Transcript | None:
        r = self._row(asset_id)
        return Transcript.from_json(r["original_json"]) if r else None

    def is_edited(self, asset_id: int) -> bool:
        r = self._row(asset_id)
        return bool(r and r["edited_json"])

    def store_original(self, asset_id: int, tr: Transcript) -> int:
        with self.db.transaction() as c:
            c.execute("DELETE FROM transcript WHERE source_asset_id = ?", (asset_id,))
            tid = c.execute("INSERT INTO transcript(source_asset_id, language, engine, original_json) VALUES (?,?,?,?)",
                            (asset_id, tr.language, tr.engine, tr.to_json())).lastrowid
        self.db.audit("transcript.create", f"asset={asset_id} engine={tr.engine}")
        return tid

    def save_edit(self, asset_id: int, tr: Transcript) -> None:
        r = self._row(asset_id)
        if r is None:
            self.store_original(asset_id, tr)
            return
        with self.db.transaction() as c:
            c.execute("UPDATE transcript SET edited_json = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')"
                      " WHERE id = ?", (tr.to_json(), r["id"]))

    def revert(self, asset_id: int) -> None:
        r = self._row(asset_id)
        if r:
            with self.db.transaction() as c:
                c.execute("UPDATE transcript SET edited_json = NULL WHERE id = ?", (r["id"],))

    def search_all(self, query: str, limit: int = 200) -> list[tuple[int, str, int, float, str]]:
        """Recherche dans toutes les transcriptions : (asset_id, fichier, n° de phrase, début, texte)."""
        out = []
        for r in self.db.query("SELECT t.source_asset_id, t.original_json, t.edited_json, a.file_name "
                               "FROM transcript t JOIN source_asset a ON a.id = t.source_asset_id"):
            tr = Transcript.from_json(r["edited_json"] or r["original_json"])
            for i in tr.search(query):
                s = tr.segments[i]
                out.append((r["source_asset_id"], r["file_name"], i, s.start, s.text))
                if len(out) >= limit:
                    return out
        return out
