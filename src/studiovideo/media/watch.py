"""Dossier surveillé : les médias déposés (copie depuis le téléphone, export d'un autre logiciel…) sont
importés automatiquement dans la bibliothèque.

Un fichier n'est pris qu'une fois stable (même taille et même date entre deux passages, et plus vieux de
quelques secondes) : une copie en cours n'est jamais importée à moitié. Les fichiers temporaires sont ignorés,
et un média déjà présent (même empreinte) n'est pas importé une seconde fois.
"""
from __future__ import annotations

import time
from pathlib import Path

from .importer import AUDIO_EXT, IMAGE_EXT, VIDEO_EXT, fingerprint

EXTS = VIDEO_EXT | AUDIO_EXT | IMAGE_EXT
TEMP_SUFFIXES = (".part", ".crdownload", ".tmp", ".download", ".partial")
MIN_AGE_S = 5.0


class FolderWatcher:
    def __init__(self, db, workspace_id: int = 1):
        self.db, self.workspace_id = db, workspace_id
        self._last: dict[str, tuple[int, float]] = {}
        self._done: set[str] = set()

    def _candidates(self, folder: Path):
        dirs = [folder] + [d for d in sorted(folder.iterdir()) if d.is_dir() and not d.name.startswith(".")]
        for d in dirs:
            try:
                for f in sorted(d.iterdir()):
                    n = f.name.lower()
                    if f.is_file() and f.suffix.lower() in EXTS and not n.startswith(("~", ".")) \
                            and not n.endswith(TEMP_SUFFIXES):
                        yield f
            except OSError:
                continue

    def scan(self, folder, now: float | None = None) -> list[Path]:
        """Fichiers prêts à importer depuis le dernier passage."""
        folder = Path(folder)
        if not folder.is_dir():
            return []
        now = time.time() if now is None else now
        ready = []
        for f in self._candidates(folder):
            key = str(f)
            if key in self._done:
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            sig = (st.st_size, st.st_mtime)
            prev = self._last.get(key)
            self._last[key] = sig
            if prev != sig or now - st.st_mtime < MIN_AGE_S or st.st_size == 0:
                continue                                    # encore en cours de copie
            self._done.add(key)
            if self.db.query_one("SELECT 1 FROM source_asset WHERE path = ?", (key,)):
                continue
            try:
                fp = fingerprint(f)
            except OSError:
                self._done.discard(key)
                continue
            if self.db.query_one("SELECT 1 FROM source_asset WHERE workspace_id = ? AND fingerprint = ?",
                                 (self.workspace_id, fp)):
                continue
            ready.append(f)
        return ready
