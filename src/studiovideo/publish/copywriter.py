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


# -- chapitres YouTube ---------------------------------------------------------------------------
MIN_CHAPTER_VIDEO = 120.0       # en dessous, des chapitres n'apportent rien
MIN_CHAPTER = 10.0              # règle YouTube : chaque chapitre dure au moins 10 s


def sentences_on_output(tl, transcripts) -> list[tuple[float, float, str]]:
    """Phrases de la transcription placées sur le temps de la vidéo montée : [(début, fin, texte)].

    transcripts : fonction asset_id -> Transcript | None."""
    out, offset = [], 0.0
    for sg in tl.segments:
        tr = transcripts(sg.asset_id)
        if tr:
            for s in tr.segments:
                if s.end > sg.src_in and s.start < sg.src_out and s.text.strip():
                    a = offset + max(0.0, s.start - sg.src_in)
                    b = offset + min(sg.src_out, s.end) - sg.src_in
                    out.append((round(a, 2), round(b, 2), s.text.strip()))
        offset += sg.src_out - sg.src_in
    return out


def stamp(t: float) -> str:
    t = int(t)
    h, m, s = t // 3600, t % 3600 // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def valid_chapters(chs: list[tuple[float, str]], total: float) -> list[tuple[float, str]]:
    """Normalise : tri, premier chapitre à 0, durée minimale, titres non vides. [] si moins de 3."""
    chs = sorted((max(0.0, float(t)), str(n).strip()[:80]) for t, n in chs if str(n).strip() and float(t) < total)
    out: list[tuple[float, str]] = []
    for t, n in chs:
        if not out:
            out.append((0.0, n))
        elif t - out[-1][0] >= MIN_CHAPTER and total - t >= MIN_CHAPTER:
            out.append((t, n))
    return out if len(out) >= 3 else []


def _title_for(text: str) -> str:
    kws = keywords(text, 3)
    if kws:
        return " · ".join(k.capitalize() if i == 0 else k for i, k in enumerate(kws))
    return text[:40]


def chapters_heuristic(sents: list[tuple[float, float, str]], total: float) -> list[tuple[float, str]]:
    """Découpe aux plus grands silences proches de parts égales ; titre = mots-clés du passage."""
    if total < MIN_CHAPTER_VIDEO or len(sents) < 3:
        return []
    n = int(max(3, min(10, total // 90)))
    gaps = [(sents[i + 1][0] - sents[i][1], sents[i + 1][0]) for i in range(len(sents) - 1)]
    cuts = [0.0]
    for k in range(1, n):
        target = total * k / n
        window = [g for g in gaps if abs(g[1] - target) <= total / n / 3 and g[1] - cuts[-1] >= MIN_CHAPTER]
        if window:
            cuts.append(max(window)[1])           # la pause la plus longue autour de la cible
    chs = []
    for i, c in enumerate(cuts):
        end = cuts[i + 1] if i + 1 < len(cuts) else total
        text = " ".join(s[2] for s in sents if c <= s[0] < end)
        chs.append((c, _title_for(text) if text else f"Partie {i + 1}"))
    return valid_chapters(chs, total)


def chapters_llm(engine, sents: list[tuple[float, float, str]], total: float, language: str = "fr") -> list:
    if total < MIN_CHAPTER_VIDEO or len(sents) < 3:
        return []
    lines = "\n".join(f"[{int(a)}] {t}" for a, _b, t in sents)[:12000]
    prompt = (f"Découpe cette vidéo de {int(total)} secondes en 3 à 10 chapitres, en langue « {language} ». "
              "Chaque chapitre commence au début d'une phrase (le nombre entre crochets est son temps en "
              "secondes), le premier à 0, au moins 10 secondes entre deux chapitres. Titres courts (40 "
              "caractères max), fidèles au contenu. Réponds UNIQUEMENT en JSON : "
              '{"chapters": [{"start": 0, "title": "..."}]}\n\nTranscription :\n' + lines)
    content = engine._ask(prompt)
    try:
        d = json.loads(content[content.find("{"): content.rfind("}") + 1])
        chs = [(float(c["start"]), c["title"]) for c in d.get("chapters", [])]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"Le modèle n'a pas répondu au format attendu : {content[:200]}") from exc
    return valid_chapters(chs, total)


def chapters_text(chs: list[tuple[float, str]]) -> str:
    return "\n".join(f"{stamp(t)} {n}" for t, n in chs)
