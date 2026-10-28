"""Statistiques publiques d'une vidéo publiée, sans API de plateforme : la page de la vidéo est lue avec yt-dlp
(rien n'est téléchargé), comme un visiteur la verrait.

- YouTube : vues, j'aime, commentaires ; TikTok : vues, j'aime, commentaires, partages — sans session.
- Facebook et Instagram ne montrent leurs chiffres qu'à un visiteur connecté : les cookies du navigateur dédié du
  compte sont prêtés à yt-dlp (lus par yt-dlp lui-même, jamais par le logiciel), navigateur fermé. Un chiffre que
  la page ne montre pas reste « non fourni » (jamais 0).

Relevés espacés pour respecter les plateformes : 6 h après la publication, puis J+1, J+3, J+7, J+14, J+30, puis une
fois par mois ; au plus 20 relevés par passage (un passage par heure), avec une pause de 5 à 20 secondes entre deux.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

SCHEDULE_H = (6, 24, 72, 168, 336, 720)      # heures après la publication ; ensuite tous les 30 jours
MONTH_H = 720
LIMIT = 20                                   # relevés au plus par passage
DELAY = (5.0, 20.0)                          # pause entre deux relevés (secondes, au hasard)
NEEDS_SESSION = ("facebook", "instagram")
# nom du navigateur pour yt-dlp, d'après le programme
YTDLP_BROWSER = {"msedge": "edge", "chrome": "chrome", "google-chrome": "chrome", "brave": "brave",
                 "vivaldi": "vivaldi", "opera": "opera", "launcher": "opera", "chromium": "chromium",
                 "firefox": "firefox"}


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def next_reading(published: datetime, last: datetime | None) -> datetime:
    """Prochain relevé : la première étape du calendrier pas encore couverte par le dernier relevé."""
    for h in SCHEDULE_H:
        at = published + timedelta(hours=h)
        if last is None or last < at:
            return at
    base = max(last, published + timedelta(hours=SCHEDULE_H[-1]))
    return base + timedelta(hours=MONTH_H)


def due(published_at: str | None, last_at: str | None, now: datetime) -> bool:
    """`published_at` et `last_at` en temps universel (« 2026-10-28T09:05:00Z »), `now` aussi (sans fuseau)."""
    pub = _dt(published_at)
    if pub is None:
        return last_at is None
    return next_reading(pub, _dt(last_at)) <= now


def cookies_from(exe: Path | None, profile: Path) -> tuple | None:
    """Option « cookiesfrombrowser » de yt-dlp pour le profil dédié du compte (None : navigateur inconnu)."""
    name = YTDLP_BROWSER.get(exe.stem.lower()) if exe else None
    if not name:
        return None
    path = Path(profile) if name == "firefox" else Path(profile) / "Default"
    return (name, str(path), None, None)


def metrics(info: dict) -> dict:
    """Chiffres présents sur la page ; un chiffre absent n'est pas inventé."""
    out = {}
    for key, src in (("views", "view_count"), ("likes", "like_count"), ("comments", "comment_count"),
                     ("shares", "repost_count")):
        v = info.get(src)
        if isinstance(v, (int, float)) and v >= 0:
            out[key] = int(v)
    return out


def read(url: str, cookies: tuple | None = None) -> dict:
    """Chiffres publics de la vidéo (yt-dlp, page lue, rien n'est téléchargé)."""
    import yt_dlp

    from ..media.downloader import js_runtimes
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "js_runtimes": js_runtimes()}
    if cookies:
        opts["cookiesfrombrowser"] = cookies
    with yt_dlp.YoutubeDL(opts) as ydl:
        return metrics(ydl.extract_info(url, download=False) or {})
