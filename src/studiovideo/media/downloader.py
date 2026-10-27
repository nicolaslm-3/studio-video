"""Import par URL via yt-dlp (cahier des charges §5.2).

Aucun téléchargement n'est garanti : les plateformes changent souvent,
certaines vidéos sont privées, géo-restreintes ou protégées.
L'URL et la provenance sont conservées avec le média.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


class DownloadUnavailable(RuntimeError):
    pass


def available() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def yt_dlp_version() -> str | None:
    try:
        from yt_dlp.version import __version__
        return __version__
    except ImportError:
        return None


def js_runtimes() -> dict:
    """Moteurs JavaScript présents sur le PC : YouTube en exige un (défis de signature), sinon il coupe le
    téléchargement en cours de route (erreur 403)."""
    found = {name: {"path": path} for name in ("deno", "node", "bun") if (path := shutil.which(name))}
    return found or {"deno": {}}


def download(url: str, dest_dir: Path, progress: Callable[[float, str], None] | None = None,
             is_cancelled: Callable[[], bool] | None = None) -> tuple[Path, dict]:
    """Télécharge `url` dans `dest_dir`. Retourne (fichier, métadonnées utiles)."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise DownloadUnavailable("Le module de téléchargement (yt-dlp) n'est pas installé.") from exc

    dest_dir.mkdir(parents=True, exist_ok=True)

    def hook(d: dict) -> None:
        if is_cancelled and is_cancelled():
            raise yt_dlp.utils.DownloadCancelled("Annulé par l'utilisateur")
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            progress(0.9 * done / total if total else 0.0, "Téléchargement")

    opts = {
        "outtmpl": str(dest_dir / "%(extractor)s-%(id)s.%(ext)s"),
        "format": "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",   # 1080p suffit au montage
        "merge_output_format": "mp4",
        "noplaylist": True,
        "continuedl": True,          # reprise d'un téléchargement interrompu
        "progress_hooks": [hook],
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "js_runtimes": js_runtimes(),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info))
            if not path.exists():
                alt = path.with_suffix(".mp4")
                path = alt if alt.exists() else path
    except yt_dlp.utils.DownloadError as exc:
        msg = str(exc)
        if "Private" in msg or "login" in msg.lower():
            hint = "Contenu privé ou nécessitant une connexion."
        elif "geo" in msg.lower():
            hint = "Contenu non disponible dans votre pays."
        elif "Unsupported URL" in msg:
            hint = "Cette adresse n'est pas prise en charge."
        elif "403" in msg and set(js_runtimes()) == {"deno"} and not shutil.which("deno"):
            hint = ("La plateforme a refusé le téléchargement : installez Node.js (nodejs.org), dont YouTube a "
                    "besoin, puis réessayez.")
        else:
            hint = "Le téléchargement a échoué. Une mise à jour de yt-dlp peut être nécessaire."
        raise RuntimeError(f"{hint}\nDétail : {msg[:400]}") from exc
    if not path.exists():
        raise RuntimeError("Le fichier téléchargé est introuvable.")
    meta = {k: info.get(k) for k in ("id", "title", "uploader", "webpage_url", "extractor", "upload_date")}
    return path, meta
