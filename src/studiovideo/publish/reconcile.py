"""Rapprochement automatique du lien d'une vidéo publiée à la main (Atelier de publication, niveau 3).

Une publication « à confirmer » (envoyée par l'utilisateur dans son navigateur) n'a pas encore de lien. La page
publique du compte est lue avec yt-dlp (liste des dernières vidéos, sans téléchargement), puis chaque vidéo est
comparée à la publication : légende (similarité du texte), durée et date. Une correspondance sûre est confirmée
d'office ; un doute est proposé à l'utilisateur (« Est-ce cette vidéo ? »). Rien n'est confirmé sans ressemblance.

Seuls YouTube et TikTok listent leurs vidéos publiques sans session ; Instagram et Facebook demandent une connexion
(prévu avec les cookies du navigateur dédié) : d'ici là, le lien s'y colle à la main.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher

SUPPORTED = ("youtube", "tiktok")
SURE, DOUBT = 0.85, 0.5             # score d'une correspondance sûre / d'une proposition
MARGIN = 0.15                       # écart minimal avec la deuxième vidéo la plus ressemblante
EARLY_S = 2 * 86400                 # une vidéo mise en ligne plus de 2 jours avant la préparation n'est pas elle


def listing_urls(platform: str, public_url: str) -> list[str]:
    """Pages à lire pour lister les dernières vidéos du compte (vide : adresse inconnue ou réseau non pris en
    charge)."""
    u = (public_url or "").strip().split("?")[0].rstrip("/")
    if platform == "youtube":
        m = re.match(r"(https?://(?:www\.|m\.)?youtube\.com/(?:@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+))", u)
        return [m.group(1) + "/shorts", m.group(1) + "/videos"] if m else []
    if platform == "tiktok":
        m = re.match(r"(https?://(?:www\.)?tiktok\.com/@[^/]+)", u)
        return [m.group(1)] if m else []
    return []


def video_key(url: str) -> str:
    """Identifiant de la vidéo : une même vidéo a plusieurs adresses (/shorts/ et watch?v= sur YouTube)."""
    m = re.search(r"tiktok\.com/.*?/video/(\d+)", url or "") or \
        re.search(r"(?:youtu\.be/|[?&]v=|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{6,20})", url or "")
    return m.group(1) if m else (url or "").strip()


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"https?://\S+", " ", t)
    return " ".join(re.findall(r"\w+", t))


def text_score(a: str, b: str) -> float:
    """Ressemblance de deux légendes (0 à 1). Une légende coupée par la plateforme reste reconnue."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if len(min(na, nb, key=len)) >= 12 and (na.startswith(nb) or nb.startswith(na)):
        return 0.97
    return SequenceMatcher(None, na, nb).ratio()


def _epoch(entry: dict) -> float | None:
    if entry.get("timestamp"):
        return float(entry["timestamp"])
    d = entry.get("upload_date")
    if d and re.fullmatch(r"\d{8}", str(d)):
        return datetime.strptime(str(d), "%Y%m%d").replace(tzinfo=timezone.utc).timestamp()
    return None


def score(pub: dict, entry: dict) -> tuple[float, float]:
    """(score global, ressemblance du texte). `pub` : text, title, duration, since (epoch) ; `entry` : description,
    title, duration, timestamp ou upload_date."""
    ts, since = _epoch(entry), pub.get("since")
    if ts is not None and since is not None and ts < since - EARLY_S:
        return 0.0, 0.0
    txt = max(text_score(pub.get("text", ""), entry.get("description") or ""),
              text_score(pub.get("text", ""), entry.get("title") or ""),
              text_score(pub.get("title", ""), entry.get("title") or "") if pub.get("title") else 0.0)
    d1, d2 = pub.get("duration"), entry.get("duration")
    if d1 and d2:
        gap = abs(float(d1) - float(d2))
        dur = 1.0 if gap <= 1.0 else 0.5 if gap <= 3.0 else 0.0
        if not normalize(pub.get("text", "")):
            return 0.6 * dur * (1.0 if gap <= 0.5 else 0.5), txt      # sans légende : jamais sûr
        return 0.75 * txt + 0.25 * dur, txt
    return txt, txt


def _sure(best: tuple, second: float, pub: dict) -> bool:
    s, txt, entry = best
    d1, d2 = pub.get("duration"), entry.get("duration")
    dur_ok = (abs(float(d1) - float(d2)) <= 1.5) if d1 and d2 else txt >= 0.95
    return s >= SURE and txt >= 0.8 and dur_ok and s - second >= MARGIN


def match(pubs: list[dict], entries: list[dict], taken: set[str] | None = None) -> list[dict]:
    """Pour chaque publication : {"publication_id", "url", "title", "score", "sure"} (au plus une ligne, la vidéo la
    plus ressemblante). Une vidéo déjà reliée (`taken`) ou confirmée pour une autre publication est écartée."""
    taken = {video_key(u) for u in taken or ()}
    ranked = []
    for p in pubs:
        scores = sorted(((*score(p, e), e) for e in entries if e.get("url") and video_key(e["url"]) not in taken),
                        key=lambda x: -x[0])
        if scores and scores[0][0] >= DOUBT:
            second = scores[1][0] if len(scores) > 1 else 0.0
            ranked.append((scores[0][0], p, scores[0], second))
    out = []
    for s, p, best, second in sorted(ranked, key=lambda x: -x[0]):
        e = best[2]
        if video_key(e["url"]) in taken:
            continue
        sure = _sure(best, second, p)
        if sure:
            taken.add(video_key(e["url"]))
        out.append({"publication_id": p["id"], "url": e["url"], "title": (e.get("title") or e.get("description")
                                                                         or "")[:200], "score": round(s, 3),
                    "sure": sure})
    return out


def _entry(e: dict) -> dict:
    url = e.get("webpage_url") or e.get("url") or ""
    if url and not url.startswith("http") and e.get("ie_key") == "Youtube":
        url = "https://www.youtube.com/watch?v=" + url
    return {"url": url, "id": e.get("id"), "title": e.get("title") or "", "description": e.get("description") or "",
            "duration": e.get("duration"), "timestamp": e.get("timestamp"), "upload_date": e.get("upload_date")}


def list_recent(url: str, limit: int = 15) -> list[dict]:
    """Dernières vidéos publiques de la page (yt-dlp, liste seule, rien n'est téléchargé)."""
    import yt_dlp

    from ..media.downloader import js_runtimes
    opts = {"extract_flat": "in_playlist", "playlistend": limit, "quiet": True, "no_warnings": True,
            "skip_download": True, "js_runtimes": js_runtimes()}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    return [_entry(e) for e in (info.get("entries") or []) if e]


def details(url: str) -> dict:
    """Légende, durée et date d'une vidéo (YouTube ne les donne pas toutes dans la liste)."""
    import yt_dlp

    from ..media.downloader import js_runtimes
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "js_runtimes": js_runtimes()}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return _entry(ydl.extract_info(url, download=False) or {})


def youtube_channel_id(public_url: str) -> str:
    """Identifiant « UC… » de la chaîne YouTube (adresse /channel/UC…, sinon lu sur sa page publique avec yt-dlp) :
    YouTube Studio s'ouvre alors sur cette chaîne. "" si introuvable."""
    m = re.search(r"/channel/(UC[\w-]{20,})", public_url or "")
    if m:
        return m.group(1)
    urls = listing_urls("youtube", public_url)
    if not urls:
        return ""
    import yt_dlp

    from ..media.downloader import js_runtimes
    opts = {"extract_flat": "in_playlist", "playlistend": 1, "quiet": True, "no_warnings": True,
            "skip_download": True, "js_runtimes": js_runtimes()}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(urls[1], download=False) or {}
    except Exception:
        return ""
    cid = info.get("channel_id") or ""
    return cid if re.fullmatch(r"UC[\w-]{20,}", cid) else ""


def youtube_upload_url(channel_id: str) -> str:
    """Page d'envoi de YouTube Studio sur cette chaîne (Google demande de s'y connecter au besoin)."""
    return f"https://studio.youtube.com/channel/{channel_id}/videos/upload?d=ud"
