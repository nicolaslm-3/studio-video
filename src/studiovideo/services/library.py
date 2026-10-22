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

    def relink(self, asset_id: int, new_path, force: bool = False) -> dict:
        """Nouvel emplacement d'un média déplacé. Refuse un fichier différent sauf si force=True.

        Retourne {"same": bool} (même empreinte que le fichier d'origine)."""
        from pathlib import Path

        from ..media import ffmpeg
        from ..media.importer import fingerprint
        new_path = Path(new_path)
        a = self.get(asset_id)
        if a is None:
            raise ValueError("Média introuvable dans la bibliothèque.")
        if not new_path.is_file():
            raise FileNotFoundError(f"Fichier introuvable : {new_path}")
        fp = fingerprint(new_path)
        orig = a["fingerprint"].removesuffix("-absent")
        same = fp == orig
        if not same and not force:
            raise ValueError("Ce fichier n'est pas identique à l'original (contenu différent).")
        other = self.db.query_one("SELECT id FROM source_asset WHERE fingerprint = ? AND id != ?", (fp, asset_id))
        if other:
            raise ValueError(f"Ce fichier est déjà dans la bibliothèque (média {other['id']}).")
        updates = {"path": str(new_path), "file_name": new_path.name, "missing": 0, "fingerprint": fp,
                   "size_bytes": new_path.stat().st_size}
        if not same or a["duration_s"] is None:
            info = ffmpeg.probe(new_path)
            updates.update({"duration_s": info.duration_s, "width": info.width, "height": info.height,
                            "fps": info.fps, "video_codec": info.video_codec, "audio_codec": info.audio_codec,
                            "audio_tracks": info.audio_tracks})
        sets = ", ".join(f"{k} = ?" for k in updates)
        with self.db.transaction() as c:
            c.execute(f"UPDATE source_asset SET {sets} WHERE id = ?", (*updates.values(), asset_id))
        self.db.audit("asset.relink", f"{asset_id}:{new_path}")
        return {"same": same}

    def relink_folder(self, folder) -> list[int]:
        """Retrouve les médias introuvables dans un dossier (nom identique puis empreinte identique)."""
        from pathlib import Path

        from ..media.importer import fingerprint
        missing = self.db.query("SELECT id, file_name, fingerprint FROM source_asset WHERE missing = 1")
        if not missing:
            return []
        by_name: dict[str, list] = {}
        for f in Path(folder).rglob("*"):
            if f.is_file():
                by_name.setdefault(f.name.lower(), []).append(f)
        found = []
        for m in missing:
            for f in by_name.get(m["file_name"].lower(), []):
                try:
                    if fingerprint(f) == m["fingerprint"].removesuffix("-absent"):
                        self.relink(m["id"], f)
                        found.append(m["id"])
                        break
                except (OSError, ValueError):
                    continue
        return found

    def counts(self) -> dict[str, int]:
        return {r["kind"]: r["n"] for r in self.db.query("SELECT kind, COUNT(*) n FROM source_asset GROUP BY kind")}
