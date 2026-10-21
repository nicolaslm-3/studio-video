"""Import non destructif des médias (cahier des charges §5).

Le fichier source n'est jamais copié ni modifié : on enregistre son chemin,
une empreinte, ses caractéristiques techniques et une miniature en cache.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..db.database import Database
from ..paths import AppPaths
from . import ffmpeg

log = logging.getLogger(__name__)

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mts", ".m2ts", ".wmv", ".flv", ".ts", ".mpg", ".mpeg"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aiff", ".aif"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
DOC_EXT = {".pdf", ".txt", ".md", ".docx", ".doc", ".pptx", ".odt", ".rtf", ".srt", ".vtt", ".ass", ".json"}

_CHUNK = 4 * 1024 * 1024


class DuplicateAsset(Exception):
    def __init__(self, existing_id: int, path: str):
        super().__init__(f"Ce média est déjà dans la bibliothèque : {path}")
        self.existing_id = existing_id


class InsufficientDiskSpace(Exception):
    pass


@dataclass
class ImportResult:
    asset_id: int
    kind: str
    warnings: list[str]


def guess_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMAGE_EXT:
        return "image"
    if ext in DOC_EXT:
        return "document"
    return "other"


def fingerprint(path: Path) -> str:
    """Empreinte rapide : taille + début + milieu + fin (adaptée aux fichiers de plusieurs heures)."""
    size = path.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with path.open("rb") as f:
        h.update(f.read(_CHUNK))
        if size > 3 * _CHUNK:
            f.seek(size // 2)
            h.update(f.read(_CHUNK))
            f.seek(max(0, size - _CHUNK))
            h.update(f.read(_CHUNK))
    return h.hexdigest()


def check_disk_space(target_dir: Path, needed_bytes: int, margin: int = 500 * 1024 * 1024) -> None:
    free = shutil.disk_usage(target_dir).free
    if free < needed_bytes + margin:
        raise InsufficientDiskSpace(
            f"Espace disque insuffisant : {free // 2**20} Mo libres, {(needed_bytes + margin) // 2**20} Mo requis.")


class MediaImporter:
    def __init__(self, db: Database, paths: AppPaths, workspace_id: int):
        self.db = db
        self.paths = paths
        self.workspace_id = workspace_id

    def find_duplicate(self, fp: str) -> int | None:
        row = self.db.query_one("SELECT id FROM source_asset WHERE workspace_id = ? AND fingerprint = ?",
                                (self.workspace_id, fp))
        return row["id"] if row else None

    def import_file(self, path: Path | str, *, project_id: int | None = None, allow_duplicate: bool = False,
                    origin_url: str = "", provenance: str = "", progress=None) -> ImportResult:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"Fichier introuvable : {path}")
        warnings: list[str] = []
        kind = guess_kind(path)
        size = path.stat().st_size
        if progress:
            progress(0.05, "Calcul de l'empreinte")
        fp = fingerprint(path)
        dup = self.find_duplicate(fp)
        if dup and not allow_duplicate:
            raise DuplicateAsset(dup, str(path))
        if dup and allow_duplicate:
            fp = f"{fp}:{path.resolve()}"
        check_disk_space(self.paths.cache, needed_bytes=5 * 1024 * 1024)

        info = None
        if kind in ("video", "audio", "image") or kind == "other":
            if progress:
                progress(0.3, "Analyse technique")
            try:
                info = ffmpeg.probe(path)
            except Exception as exc:
                if kind == "other":
                    info = None
                else:
                    raise ValueError(f"Média illisible : {exc}") from exc
            if info and kind == "other":
                kind = "video" if info.has_video else ("audio" if info.audio_tracks else "other")
            if info and kind == "video" and not info.has_video:
                kind = "audio" if info.audio_tracks else kind
                warnings.append("Aucune piste vidéo détectée.")
            if info and kind == "video" and info.audio_tracks == 0:
                warnings.append("Aucune piste audio : la transcription ne sera pas possible.")

        with self.db.transaction() as c:
            aid = c.execute(
                "INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, size_bytes, fingerprint,"
                " duration_s, width, height, fps, video_codec, audio_codec, audio_tracks, probe_json,"
                " origin_url, provenance) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.workspace_id, project_id, kind, str(path.resolve()), path.name, size, fp,
                 info.duration_s if info else None, info.width if info else None,
                 info.height if info else None, info.fps if info else None,
                 info.video_codec if info else None, info.audio_codec if info else None,
                 info.audio_tracks if info else 0,
                 json.dumps(info.raw if info else {}),
                 origin_url, provenance or ("url" if origin_url else "local"))).lastrowid

        if kind in ("video", "image") and info and info.has_video:
            if progress:
                progress(0.7, "Création de la miniature")
            try:
                at = min(1.0, (info.duration_s or 0) / 3) if info.duration_s else 0
                thumb = ffmpeg.make_thumbnail(path, self.paths.thumbnails / f"{aid}.jpg", at_s=at)
                self.db.connect().execute("UPDATE source_asset SET thumbnail_path = ? WHERE id = ?",
                                          (str(thumb), aid))
            except Exception as exc:
                warnings.append(f"Miniature non générée : {exc}")
        self.db.audit("asset.import", f"{aid}:{path.name}")
        if progress:
            progress(1.0, "Importé")
        return ImportResult(aid, kind, warnings)

    def refresh_missing(self) -> list[int]:
        """Détecte les fichiers sources déplacés ou supprimés."""
        missing = []
        for r in self.db.query("SELECT id, path, missing FROM source_asset"):
            gone = not Path(r["path"]).exists()
            if gone != bool(r["missing"]):
                self.db.connect().execute("UPDATE source_asset SET missing = ? WHERE id = ?", (int(gone), r["id"]))
            if gone:
                missing.append(r["id"])
        return missing
