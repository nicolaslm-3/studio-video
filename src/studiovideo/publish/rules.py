"""Règles de texte par plateforme et publication assistée (cahier des charges §18, §19).

Limites indicatives des plateformes (à vérifier régulièrement, elles évoluent) :
- YouTube : titre 100 caractères, description 5 000 ; Shorts jusqu'à 3 minutes, format vertical ou carré.
- TikTok : légende 2 200 caractères (titre, texte et hashtags compris).
- Instagram : légende 2 200 caractères, 30 hashtags au plus.
- Facebook : texte long accepté.
- X : 280 caractères pour un compte standard (un lien compte pour 23).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

HASHTAG = re.compile(r"(?<!\w)#[\w\-À-ÿ]+", re.UNICODE)
URL = re.compile(r"https?://\S+")


@dataclass(frozen=True)
class PlatformRules:
    key: str
    label: str
    title_max: int | None          # None : pas de champ titre séparé (le titre ouvre la légende)
    text_max: int
    hashtags_max: int | None
    upload_url: str
    domains: tuple[str, ...]


RULES = {
    "youtube": PlatformRules("youtube", "YouTube", 100, 5000, 60, "https://www.youtube.com/upload",
                             ("youtube.com", "youtu.be")),
    "tiktok": PlatformRules("tiktok", "TikTok", None, 2200, None, "https://www.tiktok.com/upload",
                            ("tiktok.com",)),
    "instagram": PlatformRules("instagram", "Instagram", None, 2200, 30, "https://www.instagram.com/",
                               ("instagram.com",)),
    "facebook": PlatformRules("facebook", "Facebook", None, 63206, None, "https://www.facebook.com/",
                              ("facebook.com", "fb.watch")),
    "x": PlatformRules("x", "X", None, 280, None, "https://x.com/compose/post", ("x.com", "twitter.com")),
}


def normalize_hashtags(raw: str) -> str:
    """« humour, sketch #fr » → « #humour #sketch #fr » (sans doublon, ordre conservé)."""
    tags = []
    for t in re.split(r"[\s,;]+", raw or ""):
        t = t.strip()
        if not t:
            continue
        t = "#" + t.lstrip("#")
        if len(t) > 1 and t.lower() not in {x.lower() for x in tags}:
            tags.append(t)
    return " ".join(tags)


def compose(platform: str, title: str, description: str, hashtags: str) -> dict:
    """Texte prêt à coller : {"title": … (YouTube seulement), "text": …}."""
    r = RULES[platform]
    tags = normalize_hashtags(hashtags)
    body = (description or "").strip()
    if r.title_max is None:
        parts = [p for p in ((title or "").strip(), body, tags) if p]
        return {"title": "", "text": "\n\n".join(parts)}
    return {"title": (title or "").strip(), "text": "\n\n".join(p for p in (body, tags) if p)}


def text_length(platform: str, text: str) -> int:
    if platform == "x":
        return len(URL.sub("x" * 23, text))
    return len(text)


def check(platform: str, title: str, description: str, hashtags: str, video: dict | None = None) -> list[str]:
    """Problèmes détectés avant publication (liste vide : rien à signaler)."""
    r = RULES[platform]
    c = compose(platform, title, description, hashtags)
    out = []
    if r.title_max is not None:
        if not c["title"]:
            out.append(f"{r.label} : titre obligatoire.")
        elif len(c["title"]) > r.title_max:
            out.append(f"{r.label} : titre trop long ({len(c['title'])}/{r.title_max}).")
    n = text_length(platform, c["text"])
    if n > r.text_max:
        out.append(f"{r.label} : texte trop long ({n}/{r.text_max}).")
    tags = len(HASHTAG.findall(c["text"]))
    if r.hashtags_max is not None and tags > r.hashtags_max:
        out.append(f"{r.label} : {tags} hashtags (maximum {r.hashtags_max}).")
    if video:
        if not video.get("exists", True):
            out.append("Le fichier vidéo est introuvable.")
        dur, aspect = video.get("duration"), video.get("aspect")
        if platform == "youtube" and aspect in ("9:16", "1:1") and dur and dur > 180:
            out.append("YouTube : au-delà de 3 minutes, la vidéo ne sera pas classée en Short.")
        if platform in ("tiktok", "instagram") and aspect == "16:9":
            out.append(f"{r.label} : format horizontal, le vertical 9:16 est conseillé.")
    return out


def url_matches(platform: str, url: str) -> bool:
    """Le lien public fourni appartient-il bien à la plateforme ?"""
    try:
        u = urlparse(url.strip())
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme in ("http", "https") and any(host == d or host.endswith("." + d) for d in RULES[platform].domains)
