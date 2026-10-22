"""Fichiers d'aperçu légers (proxies) : lecture fluide des vidéos lourdes dans le logiciel.

Créés en tâche de fond pour les vidéos plus grandes que 1080p, en HEVC/ProRes/AV1, ou à débit très élevé.
Ils ne servent qu'à la lecture : l'export utilise toujours le fichier original.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable

from . import ffmpeg

_NOWIN = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
HEAVY_CODECS = {"hevc", "h265", "prores", "av1", "vp9", "dnxhd", "mpeg2video"}


def needed(row) -> bool:
    """La vidéo mérite-t-elle un fichier d'aperçu ?"""
    if row["kind"] != "video":
        return False
    w, h = row["width"] or 0, row["height"] or 0
    if min(w, h) > 1080:
        return True
    return (row["video_codec"] or "").lower() in HEAVY_CODECS


def path_for(proxies_dir: Path, asset_id: int) -> Path:
    return Path(proxies_dir) / f"{asset_id}.mp4"


def preview_path(proxies_dir: Path, row) -> str:
    """Chemin à lire dans le logiciel : l'aperçu léger s'il existe, sinon l'original."""
    p = path_for(proxies_dir, row["id"])
    return str(p) if p.exists() else row["path"]


def make(src: Path, dest: Path, duration: float | None, run: Callable | None = None,
         progress: Callable[[float, str], None] = lambda f, m: None) -> Path:
    """540p, H.264 avec images clés fréquentes (navigation image par image fluide), son AAC."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".partial.mp4")
    cmd = [ffmpeg.ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-i", str(src),
           "-vf", "scale='if(gte(iw,ih),-2,540)':'if(gte(iw,ih),540,-2)':flags=bilinear,format=yuv420p",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-g", "12", "-c:a", "aac", "-b:a", "128k",
           "-movflags", "+faststart", "-progress", "pipe:2", str(tmp)]

    def on_line(line: str) -> None:
        if line.startswith("out_time_ms=") and duration:
            try:
                progress(min(0.99, int(line.split("=")[1]) / 1e6 / duration), "Création de l'aperçu léger")
            except ValueError:
                pass
    if run is not None:
        run(cmd, on_line)
    else:
        p = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NOWIN)
        if p.returncode != 0:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"Aperçu léger impossible : {p.stderr[-400:]}")
    tmp.replace(dest)
    return dest
