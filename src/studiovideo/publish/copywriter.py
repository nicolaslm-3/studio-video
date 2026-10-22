"""Textes de publication proposés à partir de la transcription (§18) : titre, description, hashtags.

- Avec un modèle de langage configuré : propositions rédigées, dans la langue de la vidéo, sans fait inventé.
- Sans modèle : proposition simple et transparente (première phrase forte, mots-clés les plus fréquents).
Ce sont des propositions : l'utilisateur relit et modifie toujours avant de publier.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter

from ..ai.lexicon import STOPWORDS


def _plain(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def keywords(text: str, n: int = 6, extra_stop: set[str] | None = None) -> list[str]:
    stop = {_plain(w) for w in STOPWORDS} | {_plain(w) for w in (extra_stop or set())}
    words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'-]{3,}", text)
    count = Counter()
    shown: dict[str, str] = {}
    for w in words:
        w = w.strip("'-")
        k = _plain(w)
        if len(k) < 4 or k in stop or "'" in w:
            continue
        count[k] += 1
        shown.setdefault(k, w.lower())
    return [shown[k] for k, _ in count.most_common(n)]


def hashtag(word: str) -> str:
    return "#" + re.sub(r"[^0-9A-Za-zÀ-ÿ]", "", word.title().replace(" ", ""))[:30]


def heuristic(transcript_text: str, concept: str = "", hook: str = "") -> dict:
    sents = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", transcript_text.strip()) if s.strip()]
    best = hook.strip() or next((s for s in sents if 25 <= len(s) <= 90), sents[0] if sents else "")
    title = best[:95].rstrip(" ,;:") if best else concept
    desc = " ".join(sents[:3])[:300]
    tags = [hashtag(k) for k in keywords(transcript_text, 5)]
    if concept:
        tags.insert(0, hashtag(concept))
    return {"title": title, "description": desc, "hashtags": " ".join(dict.fromkeys(tags))}


def with_llm(engine, transcript_text: str, concept: str = "", language: str = "fr", platforms: list[str] | None = None,
             hook: str = "") -> dict:
    """engine : objet avec _ask(prompt) -> str."""
    prompt = (f"Tu rédiges le texte de publication d'une vidéo courte pour {', '.join(platforms or ['les réseaux'])}"
              f", en langue « {language} »"
              + (f", pour la chaîne « {concept} »" if concept else "") + ". À partir de la transcription "
              "ci-dessous : un titre accrocheur (70 caractères max, sans guillemets), une description de 1 à 3 "
              "phrases fidèle au contenu, 3 à 6 hashtags pertinents. N'invente aucun fait, aucun chiffre, aucune "
              "promesse. Réponds UNIQUEMENT en JSON : "
              '{"title": "...", "description": "...", "hashtags": ["#..."]}'
              + (f"\n\nAccroche déjà repérée : {hook}" if hook else "")
              + f"\n\nTranscription :\n{transcript_text[:8000]}")
    content = engine._ask(prompt)
    try:
        d = json.loads(content[content.find("{"): content.rfind("}") + 1])
    except ValueError as exc:
        raise ValueError(f"Le modèle n'a pas répondu au format attendu : {content[:200]}") from exc
    tags = d.get("hashtags") or []
    if isinstance(tags, str):
        tags = tags.split()
    return {"title": str(d.get("title", "")).strip().strip('"«»  ').strip()[:100],
            "description": str(d.get("description", "")).strip()[:2000],
            "hashtags": " ".join(dict.fromkeys("#" + str(t).lstrip("#").replace(" ", "") for t in tags if str(t).strip()))}
