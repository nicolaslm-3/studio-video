"""Doublages enregistrés : une piste de voix par média et par langue (§10.2, §11)."""
from __future__ import annotations

import shutil
from pathlib import Path

from ..db.database import Database
from ..edit.dubbing import DubManifest
from .history import HistoryService


class DubService:
    def __init__(self, db: Database, root: Path):
        self.db, self.root = db, root
        self.history = HistoryService(db)          # toutes les voix générées, chacune dans son dossier

    def directory(self, asset_id: int, language: str) -> Path:
        return self.root / f"a{asset_id}_{language}"

    def new_directory(self, asset_id: int, language: str) -> Path:
        """Dossier neuf pour une nouvelle voix : les précédentes restent (historique), et un fichier encore ouvert
        par l'aperçu n'empêche jamais d'en générer une autre."""
        from datetime import datetime
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        d, k = self.root / f"a{asset_id}_{language}_{stamp}", 1
        # l'horloge de Windows avance par paliers : deux voix d'affilée peuvent avoir la même heure
        while d.exists() or d.with_name(d.name + ".partial").exists():
            k += 1
            d = self.root / f"a{asset_id}_{language}_{stamp}-{k}"
        return d

    def languages(self, asset_id: int) -> list[str]:
        return [r["language"] for r in self.db.query(
            "SELECT language FROM dub WHERE source_asset_id = ? ORDER BY language", (asset_id,))]

    def get(self, asset_id: int, language: str) -> tuple[DubManifest, Path] | None:
        r = self.db.query_one("SELECT * FROM dub WHERE source_asset_id = ? AND language = ?", (asset_id, language))
        if r is None:
            return None
        return DubManifest.from_json(r["manifest_json"]), Path(r["directory"])

    def track(self, asset_id: int, language: str) -> Path | None:
        got = self.get(asset_id, language)
        if not got:
            return None
        man, d = got
        p = d / (man.track or "dub.wav")
        return p if p.exists() else None

    def store(self, man: DubManifest, directory: Path, label: str = "", history: bool = True) -> None:
        """Voix appliquée ; une nouvelle voix entre aussi dans l'historique (`history`)."""
        self._put(man, directory)
        if history:
            self.history.add(man.asset_id, "dub", man.language, man.engine, man.to_json(), str(directory), label)
        else:
            self.history.update_active(man.asset_id, "dub", man.language, man.to_json())

    def apply_version(self, hid: int) -> str | None:
        """Revenir à une voix de l'historique ; renvoie sa langue (None si elle n'existe plus)."""
        r = self.history.activate(hid)
        if r is None:
            return None
        self._put(DubManifest.from_json(r["data_json"]), Path(r["directory"]))
        return r["language"]

    def _put(self, man: DubManifest, directory: Path) -> None:
        with self.db.transaction() as c:
            c.execute("INSERT INTO dub(source_asset_id, language, engine, directory, manifest_json) VALUES (?,?,?,?,?) "
                      "ON CONFLICT(source_asset_id, language) DO UPDATE SET engine = excluded.engine, "
                      "directory = excluded.directory, manifest_json = excluded.manifest_json, "
                      "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')",
                      (man.asset_id, man.language, man.engine, str(directory), man.to_json()))
        self.db.audit("dub.store", f"asset={man.asset_id} lang={man.language} engine={man.engine}")

    def delete(self, asset_id: int, language: str) -> None:
        got = self.get(asset_id, language)
        with self.db.transaction() as c:
            c.execute("DELETE FROM dub WHERE source_asset_id = ? AND language = ?", (asset_id, language))
            if got:                                       # et cette voix sort de l'historique
                c.execute("DELETE FROM version_history WHERE kind = 'dub' AND source_asset_id = ? AND language = ? "
                          "AND directory = ?", (asset_id, language, str(got[1])))
        if got:
            shutil.rmtree(got[1], ignore_errors=True)
        self.db.audit("dub.delete", f"asset={asset_id} lang={language}")


class StemService:
    """Pistes séparées (voix seule, musique et ambiance) par média, sur disque."""

    def __init__(self, root: Path):
        self.root = root

    def directory(self, asset_id: int) -> Path:
        return self.root / f"a{asset_id}"

    def mark_done(self, asset_id: int, model: str) -> None:
        import json
        (self.directory(asset_id) / "stems.json").write_text(json.dumps({"model": model}), encoding="utf-8")

    def has(self, asset_id: int) -> bool:
        d = self.directory(asset_id)
        return all((d / f).exists() for f in ("stems.json", "voice.wav", "music.wav"))

    def voice(self, asset_id: int) -> Path | None:
        return self.directory(asset_id) / "voice.wav" if self.has(asset_id) else None

    def music(self, asset_id: int) -> Path | None:
        return self.directory(asset_id) / "music.wav" if self.has(asset_id) else None

    def delete(self, asset_id: int) -> None:
        shutil.rmtree(self.directory(asset_id), ignore_errors=True)
