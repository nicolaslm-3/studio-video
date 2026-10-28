"""Détection des meilleurs extraits (cahier des charges §7).

Quatre critères : Rire, Polarisant, Émotion, Politique (`lexicon.CATEGORIES`).

Deux moteurs interchangeables :
- « Analyse locale » : combine la transcription (lexiques, prises de position, répétitions,
  ponctuation) et le son (`AudioProfile` : volume, micro qui sature, éclats sonores hors parole,
  voix qui monte dans les aigus, débit). Fonctionne hors ligne, sans modèle à télécharger.
- « Modèle de langage » (optionnel) : Ollama en local ou une API compatible OpenAI,
  qui lit la transcription et propose des extraits. Ses propositions passent par les
  mêmes contrôles (bornes de phrases, durées, protection du contexte).

Le score est une ESTIMATION destinée au classement, jamais une prédiction de vues.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
import urllib.error
import urllib.request
import wave
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from ..transcribe.model import Transcript
from .lexicon import CATEGORIES, CLAIM_MARKERS, DISAGREE, FILLERS, LEXICON, RESERVATIONS, STOPWORDS

Progress = Callable[[float, str], None]


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


@dataclass
class Request:
    count: int = 10
    min_duration: float = 20.0
    max_duration: float = 60.0
    target_duration: float = 40.0
    categories: list[str] = field(default_factory=list)      # vide = toutes
    keywords: list[str] = field(default_factory=list)
    query: str = ""                                           # demande en langage naturel
    speakers_include: list[str] = field(default_factory=list)
    speakers_exclude: list[str] = field(default_factory=list)
    min_gap: float = 5.0                                      # écart minimal entre deux extraits
    avoid_repetition: bool = True
    intensity: int = 2                                        # 1 calme … 3 intense


# -- critères de l'écran Clipping (enregistrés dans le projet : `clip_criteria`) -------------------------
CLIP_DURATIONS = {"court": ("15 à 30 s", 15.0, 30.0, 22.0), "moyen": ("30 à 60 s", 30.0, 60.0, 42.0),
                  "long": ("60 à 90 s", 60.0, 90.0, 75.0)}
DEFAULT_CRITERIA = {"categories": [], "duration": "moyen", "count": 8, "reframe": False}

# Comment chaque critère est cherché (affiché au survol), fidèle à `LocalEngine.propose` et au modèle de langage.
CATEGORY_HELP_COMMON = ("Pour tous : la vidéo est découpée en passages de la durée choisie qui commencent et finissent "
           "sur une phrase complète ; malus si une phrase est coupée ou si une réserve (« mais », « cependant ») "
           "suit. Les meilleurs passages sont gardés sans chevauchement. Avec un modèle de langage (Réglages), il "
           "lit le texte et choisit lui-même, en suivant ce critère.")
CATEGORY_HELP = {
    "rire": "Rire : les rires notés dans la transcription (« haha », « (rires) »), ce qu'on dit quand c'est drôle "
            "(« c'est marrant », « mort de rire », « j'en peux plus », « je suis mort », « mdr »…), et dans le son "
            "les éclats hors parole (rires, réactions du chat ou du public), le micro qui sature et les éclats "
            "saccadés typiques du rire.",
    "polarisant": "Polarisant : les prises de position qui font réagir (« je pense que », « honnêtement », « je "
                  "l'assume », « ça va pas plaire », « c'est surcoté »…), les jugements tranchés (« ridicule », "
                  "« arnaque »…), les généralisations (« tous les », « les gens », « jamais ») et la "
                  "contradiction des autres juste après.",
    "emotion": "Émotion : la voix qui monte (cris, excitation, commentateurs qui s'emballent) mesurée sur le son — "
               "volume au-dessus de la moyenne de la vidéo, voix plus aiguë, micro qui sature, débit qui "
               "s'accélère —, les mots de la hype (« allez », « c'est pas possible », « il l'a fait », « let's "
               "go »…), les mots répétés (« non non non ») et les points d'exclamation.",
    "politique": "Politique : vocabulaire politique (institutions, partis, personnalités, sujets : retraites, "
                 "immigration, budget, élection…) — plus il y a de termes différents, plus le passage remonte —, "
                 "déclarations et propositions (« je propose », « il faut que », « je dénonce »), chiffres "
                 "avancés et contradiction.",
}
DEFAULT_CATEGORY = "politique"          # aucun critère coché : politique


def criteria_request(criteria: dict | None, video_duration: float) -> Request:
    """Demande de recherche d'extraits à partir des critères choisis (catégories, durée, nombre)."""
    c = {**DEFAULT_CRITERIA, **(criteria or {})}
    _n, a, b, t = CLIP_DURATIONS.get(c["duration"], CLIP_DURATIONS["moyen"])
    dur = float(video_duration or 0)
    count = int(c["count"])
    if dur and dur < b * 1.5:                           # vidéo courte : extraits plus courts, moins nombreux
        a, b, t = max(4.0, dur * 0.12), max(8.0, dur * 0.45), max(6.0, dur * 0.25)
        count = max(2, min(count, int(dur // 15) or 2))
    cats = [k for k in c["categories"] if k in CATEGORIES]          # anciens critères retirés : ignorés
    return Request(count=count, min_duration=a, max_duration=b, target_duration=t,
                   categories=cats or [DEFAULT_CATEGORY])


@dataclass
class Candidate:
    start: float
    end: float
    score: float                       # 0..100, estimation de classement
    category: str
    subject: str
    rationale: str
    hook: str
    speakers: list[str] = field(default_factory=list)
    context_flags: list[str] = field(default_factory=list)
    first: int = 0                     # indices de phrases (transcription)
    last: int = 0
    engine: str = "local"

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return asdict(self)


# -- caractéristiques audio ------------------------------------------------------------------
def energy_profile(wav: Path, step: float = 0.5) -> list[float]:
    """Niveau RMS (dBFS) par fenêtre de `step` secondes, lu en flux (vidéos de plusieurs heures)."""
    import numpy as np
    out: list[float] = []
    with wave.open(str(wav), "rb") as w:
        rate = w.getframerate()
        n = int(rate * step)
        while True:
            data = w.readframes(n)
            if not data:
                break
            x = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
            out.append(20 * math.log10(max(rms, 1e-6)))
    return out


@dataclass
class AudioProfile:
    """Mesures du son par fenêtre de `step` secondes : volume (dB), part d'échantillons saturés, centre de
    gravité du spectre (Hz : une voix qui crie monte dans les aigus) et variation rapide du volume (dB : le rire
    est fait d'éclats saccadés)."""
    step: float
    rms: list[float]
    clip: list[float]
    bright: list[float]
    burst: list[float]


def audio_profile(wav: Path, step: float = 0.5) -> AudioProfile:
    """Profil du son, lu en flux (vidéos de plusieurs heures)."""
    import numpy as np
    prof = AudioProfile(step, [], [], [], [])
    with wave.open(str(wav), "rb") as w:
        rate = w.getframerate()
        n = int(rate * step)
        sub = max(1, int(rate * 0.05))                      # tranches de 50 ms
        while True:
            data = w.readframes(n)
            if not data:
                break
            x = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
            if not x.size:
                break
            rms = float(np.sqrt(np.mean(x * x)))
            prof.rms.append(20 * math.log10(max(rms, 1e-6)))
            prof.clip.append(float(np.mean(np.abs(x) > 0.97)))
            if rms > 1e-4:
                mag = np.abs(np.fft.rfft(x * np.hanning(x.size)))
                freqs = np.fft.rfftfreq(x.size, 1.0 / rate)
                prof.bright.append(float((freqs * mag).sum() / max(mag.sum(), 1e-9)))
            else:
                prof.bright.append(0.0)
            k = x.size // sub
            if k >= 4:
                parts = np.sqrt(np.mean(x[:k * sub].reshape(k, sub) ** 2, axis=1))
                db = 20 * np.log10(np.maximum(parts, 1e-6))
                prof.burst.append(float(np.mean(np.abs(np.diff(db)))))
            else:
                prof.burst.append(0.0)
    return prof


def _window_energy(profile: list[float], step: float, a: float, b: float) -> tuple[float, float]:
    """(moyenne, maximum) en dB sur [a, b]."""
    if not profile:
        return -60.0, -60.0
    i, j = int(a / step), max(int(a / step) + 1, int(math.ceil(b / step)))
    seg = [v for v in profile[i:j] if v > -55] or [-60.0]
    return sum(seg) / len(seg), max(seg)


# -- analyse locale -----------------------------------------------------------------------------
def _lex_hits(text_f: str, words: list[str]) -> list[str]:
    hits = []
    for w in words:
        wf = fold(w)
        if " " in wf or "'" in wf:
            if wf in text_f:
                hits.append(w)
        elif re.search(rf"(?<![\w]){re.escape(wf)}(?![\w])", text_f):
            hits.append(w)
    return hits


def _keywords(texts: list[str], n: int = 5, boost: list[str] | None = None) -> list[str]:
    """Idées principales : les mots (et groupes de deux mots qui reviennent) les plus fréquents, sans les mots
    vides ; ceux du thème (`boost`) passent devant, les mots longs départagent."""
    c: Counter = Counter()
    pairs: Counter = Counter()
    for t in texts:
        toks = [re.sub(r"^(?:[ldjnsctm]|qu)'", "", x.strip("'-")) for x in re.findall(r"[\wÀ-ÿ'-]+", t.lower())]
        good = [len(x) >= 4 and "-" not in x and fold(x) not in STOPWORDS and x not in STOPWORDS
                and not x.isdigit() for x in toks]
        for k, tok in enumerate(toks):
            if good[k]:
                c[tok] += 1
                if k + 1 < len(toks) and good[k + 1]:
                    pairs[f"{tok} {toks[k + 1]}"] += 1
    theme = {fold(w) for w in boost or []}
    score = {w: k + (2 if fold(w) in theme else 0) + len(w) / 100 for w, k in c.items()}
    score.update({w: k * 1.5 + len(w) / 100 for w, k in pairs.items() if k >= 2})
    out: list[str] = []
    for w in sorted(score, key=score.get, reverse=True):
        if any(w in o or o in w for o in out):
            continue
        out.append(w)
        if len(out) >= n:
            break
    return out


def _punchline(sentences: list[str]) -> tuple[str, int]:
    """La phrase la plus clivante d'un passage (prise de position, jugement, généralisation) et sa force."""
    best, force = "", 0
    for s in sentences:
        sf = fold(s)
        f = 3 * len(_lex_hits(sf, LEXICON["polarisant"])) + len(_lex_hits(sf, LEXICON["absolus"])) \
            + s.count("!")
        if f > force and 4 <= len(s.split()) <= 30:
            best, force = s, f
    return best, force


def _short(sentence: str, n: int = 8) -> str:
    """Phrase ramenée à `n` mots au plus, sans les hésitations du début (« euh », « bah », « alors »)."""
    words = sentence.strip().split()
    while words and fold(words[0].strip(",.!?…")) in FILLERS:
        words.pop(0)
    out = " ".join(words[:n]).rstrip(",;:")
    return out + ("…" if len(words) > n else "")


def summary(texts: list[str], category: str = "") -> str:
    """Titre d'un extrait en 3 à 8 mots : sa parole la plus clivante quand il en a une (ou en Polarisant),
    sinon ses idées principales en mots-clés, sans forme de phrase."""
    quote, force = _punchline(texts)
    if quote and (force >= 6 or (category == "polarisant" and force >= 3)):
        return _short(quote)
    kws = _keywords(texts, 4, LEXICON.get(category))
    words: list[str] = []
    for k in kws:
        if len(words) + len(k.split()) > 8:
            break
        words.append(k)
    title = " · ".join(words)
    if len(title.split()) < 3:                                # trop peu de mots-clés : le début de la phrase
        return _short(next((t for t in texts if t.strip()), ""))
    return title


def _hook(sentences: list[str]) -> str:
    for s in sentences[:3]:
        if s.strip().endswith("?") and len(s) <= 110:
            return s.strip()
    first = sentences[0].strip() if sentences else ""
    return first if len(first) <= 90 else first[:87].rsplit(" ", 1)[0] + "…"


def context_flags(tr: Transcript, first: int, last: int, start: float | None = None,
                  end: float | None = None) -> list[str]:
    """Alertes de protection du contexte (§7.4)."""
    segs = tr.segments
    flags = []
    a = segs[first].start if start is None else start
    b = segs[last].end if end is None else end
    if a > segs[first].start + 0.4:
        flags.append("Commence au milieu d'une phrase.")
    if b < segs[last].end - 0.4:
        flags.append("Se termine au milieu d'une phrase.")
    if last + 1 < len(segs):
        nxt = segs[last + 1]
        nf = fold(nxt.text)
        if any(nf.startswith(fold(r) + " ") or nf.startswith(fold(r) + ",") for r in RESERVATIONS) \
                and nxt.start - segs[last].end < 2.0:
            flags.append(f"La phrase suivante commence par une réserve (« {nxt.text[:50]}… ») : "
                         "l'extrait pourrait en changer le sens.")
        if nxt.speaker and segs[last].speaker and nxt.speaker != segs[last].speaker \
                and nxt.start - segs[last].end < 1.5 and segs[last].text.strip().endswith("?"):
            flags.append(f"La réponse de {nxt.speaker} à la dernière question est coupée.")
    if first > 0:
        prev = segs[first - 1]
        if prev.text.strip().endswith("?") and prev.speaker and prev.speaker != segs[first].speaker \
                and segs[first].start - prev.end < 1.5:
            flags.append(f"Réponse isolée : la question de {prev.speaker} n'est pas incluse.")
        if last == first and len(segs[first].text.split()) < 12:
            flags.append("Phrase très courte isolée de son contexte.")
    text_f = fold(" ".join(s.text for s in segs[first:last + 1]))
    if re.search(r"\d", text_f) or _lex_hits(text_f, CLAIM_MARKERS):
        flags.append("Contient des chiffres ou des affirmations à vérifier avant publication.")
    return flags


class LocalEngine:
    name = "local"
    label = "Analyse locale (hors ligne)"

    def __init__(self, energy: list[float] | None = None, step: float = 0.5, audio: AudioProfile | None = None):
        self.audio = audio
        self.energy, self.step = (audio.rms if audio else energy) or [], (audio.step if audio else step)

    def _windows(self, tr: Transcript, req: Request):
        segs = tr.segments
        n = len(segs)
        for i in range(n):
            if i > 0 and segs[i].start - segs[i - 1].end < 0.15 and not segs[i - 1].text.strip().endswith(
                    (".", "?", "!", "…")):
                continue  # ne pas commencer au milieu d'une phrase coupée en deux segments
            j = i
            while j < n and segs[j].end - segs[i].start <= req.max_duration:
                d = segs[j].end - segs[i].start
                if d >= req.min_duration:
                    yield i, j
                j += 1
            if j == i and segs[i].end - segs[i].start > req.max_duration:
                continue

    def propose(self, tr: Transcript, req: Request, progress: Progress = lambda f, m: None) -> list[Candidate]:
        segs = tr.segments
        if not segs:
            return []
        cats = req.categories or list(CATEGORIES)
        kw = [k.strip() for k in req.keywords if k.strip()]
        if req.query.strip():
            kw += [t for t in re.findall(r"[\wÀ-ÿ'-]{4,}", req.query.lower()) if fold(t) not in STOPWORDS]
        file_mean = (sum(v for v in self.energy if v > -55) / max(1, sum(1 for v in self.energy if v > -55))
                     if self.energy else -30.0)
        au = self.audio
        st = self.step
        speech = [False] * len(self.energy)                  # fenêtres où quelqu'un parle (transcription)
        for s in segs:
            for k in range(int(s.start / st), min(len(speech), int(math.ceil(s.end / st)))):
                speech[k] = True
        voiced = [v for v in self.energy if v > -55]
        bright_mean = (sum(b for b, e in zip(au.bright, au.rms) if e > -55) / max(1, len(voiced))) if au else 0.0
        burst_mean = (sum(b for b, e in zip(au.burst, au.rms) if e > -55) / max(1, len(voiced))) if au else 0.0
        total_words = sum(len(s.text.split()) for s in segs)
        rate_mean = total_words / max(1.0, sum(s.end - s.start for s in segs))     # débit moyen (mots / s)
        cands: list[Candidate] = []
        windows = list(self._windows(tr, req))
        for k, (i, j) in enumerate(windows):
            if k % 200 == 0:
                progress(0.1 + 0.8 * k / max(1, len(windows)), "Analyse des passages")
            chunk = segs[i:j + 1]
            a, b = chunk[0].start, chunk[-1].end
            dur = b - a
            texts = [s.text for s in chunk]
            text = " ".join(texts)
            tf = fold(text)
            speakers = [s.speaker for s in chunk if s.speaker]
            spk_set = list(dict.fromkeys(speakers))
            if req.speakers_include and not set(spk_set) & set(req.speakers_include):
                continue
            if req.speakers_exclude and set(spk_set) and set(spk_set) <= set(req.speakers_exclude):
                continue
            words = len(text.split())
            density = words / max(1.0, dur)                       # mots / seconde
            changes = sum(1 for x, y in zip(speakers, speakers[1:]) if x != y)
            exclam = text.count("!")
            e_mean, e_max = _window_energy(self.energy, self.step, a, b)
            e_rel = e_mean - file_mean
            # le son du passage : cris, saturation, éclats hors parole, voix plus aiguë
            i0, i1 = int(a / st), max(int(a / st) + 1, int(math.ceil(b / st)))
            win = range(i0, min(i1, len(self.energy)))
            loud = sum(1 for k in win if speech[k] and self.energy[k] > file_mean + 8)   # crie, s'emballe
            spoken = [self.energy[k] for k in win if speech[k] and self.energy[k] > -55]
            e_voice = (sum(spoken) / len(spoken) - file_mean) if spoken else e_rel     # voix seule, sans le public
            sat = sum(1 for k in win if au and au.clip[k] > 0.002)               # micro qui sature
            outside = sum(1 for k in win if not speech[k] and self.energy[k] > file_mean + 2)   # rires, public
            bursty = sum(1 for k in win if au and self.energy[k] > file_mean and au.burst[k] > burst_mean * 1.6)
            bright = ([au.bright[k] for k in win if self.energy[k] > file_mean + 3] if au else [])
            bright_rel = (sum(bright) / len(bright) - bright_mean) if bright else 0.0
            rate_rel = density / max(0.5, rate_mean)
            repeats = len(re.findall(r"\b(\w{2,})(?:[ ,!]+\1\b){2,}", tf))     # « non non non », « allez allez allez »
            disagree = _lex_hits(tf, DISAGREE)

            cat_scores: dict[str, float] = {}
            reasons: dict[str, list[str]] = {}
            for c in cats:
                hits = _lex_hits(tf, LEXICON.get(c, []))
                r = [f"mots : {', '.join(dict.fromkeys(hits[:4]))}"] if hits else []
                if c == "rire":
                    noted = len(re.findall(r"\b(?:ha){2,}|\brires?\b|\blaugh", tf))
                    sc = min(4, len(hits)) * 7.0 + min(3, noted) * 5.0 + min(5, exclam) * 1.5
                    sc += min(5, outside) * 2.5 + min(4, sat) * 2.0 + min(5, bursty) * 1.5
                    if outside >= 2:
                        r.append("éclats sonores hors parole (rires, réactions)")
                    if bursty >= 2:
                        r.append("éclats saccadés, typiques du rire")
                    if sat:
                        r.append("micro qui sature")
                elif c == "polarisant":
                    quote, force = _punchline(texts)
                    absol = _lex_hits(tf, LEXICON["absolus"])
                    sc = min(5, len(hits)) * 6.0 + min(4, len(absol)) * 2.0 + min(3, len(disagree)) * 3.0
                    sc += min(4, changes) * 1.5 if disagree else 0.0
                    sc += min(6.0, force)
                    if absol:
                        r.append(f"généralisations : {', '.join(dict.fromkeys(absol[:3]))}")
                    if disagree:
                        r.append("contradiction juste après")
                elif c == "emotion":
                    sc = min(4, len(hits)) * 5.0 + min(3, repeats) * 4.0 + min(5, exclam) * 2.0
                    sc += max(0.0, e_voice) * 1.5 + min(6, loud) * 2.5 + min(4, sat) * 1.0
                    sc += max(0.0, min(6.0, bright_rel / 150)) + max(0.0, rate_rel - 1.15) * 8.0
                    if loud >= 2 or e_voice > 3:
                        r.append("la voix monte (cris, excitation)")
                    if bright_rel > 300:
                        r.append("voix plus aiguë que d'habitude")
                    if sat:
                        r.append("micro qui sature")
                    if rate_rel > 1.3:
                        r.append("débit qui s'accélère")
                    if repeats:
                        r.append("mots répétés")
                else:                                           # politique
                    terms = set(fold(h) for h in hits)
                    decl = _lex_hits(tf, LEXICON["politique_declaration"])
                    sc = min(8, len(terms)) * 4.0 + min(3, len(decl)) * 5.0 + min(2, len(disagree)) * 2.0
                    sc += 3.0 if terms and re.search(r"\d", tf) else 0.0
                    if decl:
                        r.append(f"déclarations : {', '.join(dict.fromkeys(decl[:3]))}")
                cat_scores[c], reasons[c] = sc, r
            best = max(cat_scores, key=cat_scores.get)
            base = cat_scores[best]
            kw_hits = _lex_hits(tf, kw) if kw else []
            if kw and not kw_hits:
                continue
            score = base + len(set(kw_hits)) * 8.0
            # qualité générale : densité de parole, fin de phrase, durée proche de la cible
            score += min(3.2, density) * 3.0
            if chunk[-1].text.strip().endswith((".", "!", "?", "…")):
                score += 4
            score -= abs(dur - req.target_duration) / max(1.0, req.target_duration) * 8.0
            score += {1: -max(0.0, e_rel) * 1.0, 2: 0.0, 3: max(0.0, e_rel) * 1.5}.get(req.intensity, 0.0)
            flags = context_flags(tr, i, j)
            score -= 3 * sum(1 for f in flags if "coupée" in f or "réserve" in f or "isolée" in f)
            why = reasons[best][:]
            if kw_hits:
                why.insert(0, f"mots-clés : {', '.join(dict.fromkeys(kw_hits[:4]))}")
            if density >= 2.2:
                why.append("parole dense")
            if chunk[-1].text.strip().endswith((".", "!", "?")):
                why.append("se termine sur une phrase complète")
            cands.append(Candidate(a, b, score, best, summary(texts, best), "; ".join(why) or "passage cohérent",
                                   _hook(texts), spk_set, flags, i, j, "local"))
        return select(cands, req)


def select(cands: list[Candidate], req: Request) -> list[Candidate]:
    """Meilleurs candidats sans chevauchement ni répétition, scores ramenés sur 0–100."""
    if not cands:
        return []
    cands = sorted(cands, key=lambda c: c.score, reverse=True)
    chosen: list[Candidate] = []

    def free(c: Candidate, gap: float | None = None) -> bool:
        g = req.min_gap if gap is None else gap
        return not any(c.start < o.end + g and c.end > o.start - g for o in chosen)

    # 1er passage : sujets variés (au plus 2 extraits par sujet principal)
    seen_subjects: Counter = Counter()
    for c in cands:
        if len(chosen) >= req.count:
            break
        if not free(c):
            continue
        if req.avoid_repetition and c.subject:
            key = re.split(r"[,·]", c.subject)[0].strip()
            if seen_subjects[key] >= 2:
                continue
            seen_subjects[key] += 1
        chosen.append(c)
    # 2e passage : compléter jusqu'au nombre demandé si la variété ne suffit pas ;
    # 3e passage : extraits accolés autorisés (sans chevauchement) si l'écart minimal bloque encore
    for gap in (None, 0.0):
        for c in cands:
            if len(chosen) >= req.count:
                break
            if c not in chosen and free(c, gap):
                chosen.append(c)
    hi = max(c.score for c in chosen)
    lo = min(c.score for c in cands)
    for c in chosen:
        c.score = round(100 * (c.score - lo) / (hi - lo), 0) if hi > lo else 50.0
        c.score = max(5.0, min(100.0, c.score))
    return chosen


# -- moteur « modèle de langage » ----------------------------------------------------------------
class LLMError(RuntimeError):
    pass


def _http_json(url: str, payload: dict, headers: dict | None = None, timeout: float = 300) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        model = payload.get("model", "") if isinstance(payload, dict) else ""
        raise LLMError(http_error_text(exc.code, exc.read(), model)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"Service injoignable ({url}) : {exc}") from exc


def http_error_text(code: int, body: bytes, model: str = "") -> str:
    """Erreur du service de modèle de langage, en clair (au lieu de la réponse brute)."""
    try:
        err = json.loads(body.decode("utf-8", "replace"))
        err = err.get("error", err)
        detail = (err.get("message") if isinstance(err, dict) else str(err)) or ""
    except ValueError:
        detail = body.decode("utf-8", "replace")
    detail = " ".join(detail.split())[:200]
    if code == 404 and ("model" in detail.lower() or "not found" in detail.lower()):
        return (f"Le modèle « {model} » n'existe pas chez ce service, ou votre clé n'y a pas accès. Corrigez son "
                "nom dans Réglages → Modèle de langage (choisissez-en un dans la liste).")
    if code in (401, 403):
        return "Clé API refusée par le service : vérifiez-la dans Réglages → Modèle de langage."
    if code == 429 and any(w in detail.lower() for w in ("credit", "quota", "billing")):
        return ("Plus de crédit sur ce compte (quota épuisé) : ajoutez du crédit chez le fournisseur ou choisissez "
                "un autre moteur.")
    if code == 429:
        return "Le service refuse pour l'instant (trop de demandes) : réessayez dans quelques minutes."
    return f"Le service a répondu {code}" + (f" : {detail}" if detail else ".")


PROMPT = """Tu es monteur vidéo pour les réseaux sociaux. Voici une transcription horodatée (secondes) :
chaque ligne est « [début-fin] intervenant : texte ».

Propose jusqu'à {count} extraits de {min_d:.0f} à {max_d:.0f} secondes (cible {target:.0f} s){cats}{query}.
Chaque extrait doit commencer au début d'une phrase et finir à la fin d'une phrase, avoir une accroche
immédiate et une conclusion compréhensible, sans sortir une phrase de son contexte.
N'invente rien : l'accroche reprend ou résume fidèlement ce qui est dit.
« subject » : 3 à 8 mots, soit les idées principales en mots-clés (sans forme de phrase), soit la parole
la plus clivante de l'extrait, citée telle quelle.

Réponds UNIQUEMENT en JSON : {{"clips": [{{"start": 12.3, "end": 48.9, "subject": "...", "category": "{catlist}",
"score": 0-100, "rationale": "pourquoi ce passage", "hook": "accroche proposée"}}]}}

Transcription :
{transcript}
"""


LLM_HINTS = {
    "rire": "rire (category « rire ») : les moments où l'on rit ou où c'est hilarant, rires notés dans le texte, "
            "« c'est marrant », « mort de rire », « j'en peux plus », fou rire entre plusieurs personnes",
    "polarisant": "polarisant (category « polarisant ») : hot takes, opinions tranchées (« je pense que… »), "
                  "propos controversés que tout le monde n'appréciera pas, prises de position qui font réagir sur "
                  "les réseaux",
    "emotion": "émotion (category « emotion ») : cris, excitation, hype (commentateurs qui s'emballent, « allez », "
               "« c'est pas possible »), voix qui s'intensifie, moments forts et émouvants",
    "politique": "politique (category « politique ») : déclarations, propositions et prises de position "
                 "politiques, chiffres avancés, attaques et contradictions entre personnalités",
}


def _transcript_lines(tr: Transcript, a: int, b: int) -> str:
    return "\n".join(f"[{s.start:.1f}-{s.end:.1f}] {s.speaker or '?'} : {s.text.strip()}" for s in tr.segments[a:b])


class LLMEngine:
    """Ollama (http://localhost:11434) ou API compatible OpenAI (/v1/chat/completions)."""

    def __init__(self, kind: str, model: str, base_url: str = "", api_key: str = "",
                 chunk_seconds: float = 600.0):
        if kind not in ("ollama", "openai"):
            raise ValueError(kind)
        self.kind, self.model, self.api_key = kind, model, api_key
        self.base_url = (base_url or ("http://localhost:11434" if kind == "ollama" else "https://api.openai.com"))
        self.base_url = self.base_url.rstrip("/")
        self.chunk_seconds = chunk_seconds
        self.name = f"{kind}:{model}"
        self.label = f"Modèle de langage ({model})"

    def _ask(self, prompt: str) -> str:
        if self.kind == "ollama":
            r = _http_json(f"{self.base_url}/api/chat", {"model": self.model, "stream": False, "format": "json",
                                                         "messages": [{"role": "user", "content": prompt}],
                                                         "options": {"temperature": 0.2}})
            return r.get("message", {}).get("content", "")
        r = _http_json(f"{self.base_url}/v1/chat/completions",
                       {"model": self.model, "temperature": 0.2, "response_format": {"type": "json_object"},
                        "messages": [{"role": "user", "content": prompt}]},
                       {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})
        try:
            return r["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Réponse inattendue du service : {str(r)[:300]}") from exc

    def propose(self, tr: Transcript, req: Request, progress: Progress = lambda f, m: None) -> list[Candidate]:
        segs = tr.segments
        if not segs:
            return []
        # découpage de la transcription en blocs d'environ chunk_seconds
        blocks, a = [], 0
        for k in range(1, len(segs) + 1):
            if k == len(segs) or segs[k].end - segs[a].start > self.chunk_seconds:
                blocks.append((a, k))
                a = k
        cats = req.categories or list(CATEGORIES)
        raw: list[Candidate] = []
        for n, (a, b) in enumerate(blocks):
            progress(0.1 + 0.8 * n / len(blocks), f"Analyse par le modèle de langage ({n + 1}/{len(blocks)})")
            prompt = PROMPT.format(
                count=max(3, req.count // len(blocks) + 2), min_d=req.min_duration, max_d=req.max_duration,
                target=req.target_duration,
                cats=(". Cherche en priorité :\n" + "\n".join(f"- {LLM_HINTS[c]}" for c in cats if c in LLM_HINTS))
                if req.categories else "",
                query=(f". Demande : « {req.query} »" if req.query else "") +
                      (f". Mots-clés : {', '.join(req.keywords)}" if req.keywords else ""),
                catlist="|".join(cats), transcript=_transcript_lines(tr, a, b))
            content = self._ask(prompt)
            try:
                data = json.loads(content[content.find("{"): content.rfind("}") + 1])
            except ValueError as exc:
                raise LLMError(f"Le modèle n'a pas répondu en JSON : {content[:200]}") from exc
            for c in data.get("clips", []):
                try:
                    cand = self._snap(tr, float(c["start"]), float(c["end"]), req)
                except (KeyError, TypeError, ValueError):
                    continue
                if cand is None:
                    continue
                cand.subject = " ".join(str(c.get("subject", "")).split()[:10])[:120] or cand.subject
                cat = str(c.get("category", "")).lower()
                cand.category = cat if cat in CATEGORIES else cand.category
                cand.rationale = str(c.get("rationale", ""))[:400] or cand.rationale
                cand.hook = str(c.get("hook", ""))[:160] or cand.hook
                try:
                    cand.score = float(c.get("score", 50))
                except (TypeError, ValueError):
                    cand.score = 50.0
                raw.append(cand)
        return select(raw, req)

    def _snap(self, tr: Transcript, a: float, b: float, req: Request) -> Candidate | None:
        """Recale sur les phrases et contrôle la durée et le contexte."""
        segs = tr.segments
        first = min(range(len(segs)), key=lambda i: abs(segs[i].start - a))
        last = min(range(first, len(segs)), key=lambda i: abs(segs[i].end - b))
        start, end = segs[first].start, segs[last].end
        if not (req.min_duration * 0.8 <= end - start <= req.max_duration * 1.2):
            return None
        texts = [s.text for s in segs[first:last + 1]]
        spk = list(dict.fromkeys(s.speaker for s in segs[first:last + 1] if s.speaker))
        return Candidate(start, end, 50.0, req.categories[0] if req.categories else DEFAULT_CATEGORY,
                         summary(texts), "", _hook(texts), spk,
                         context_flags(tr, first, last), first, last, self.name)


def check_llm(kind: str, base_url: str = "", api_key: str = "") -> list[str]:
    """Liste des modèles disponibles (pour vérifier la connexion)."""
    if kind == "ollama":
        url = (base_url or "http://localhost:11434").rstrip("/") + "/api/tags"
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                return [m["name"] for m in json.loads(r.read().decode()).get("models", [])]
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise LLMError(f"Ollama ne répond pas sur {url} : {exc}") from exc
    url = (base_url or "https://api.openai.com").rstrip("/") + "/v1/models"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"} if api_key else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return [m["id"] for m in json.loads(r.read().decode()).get("data", [])]
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise LLMError(f"Service injoignable : {exc}") from exc


_NOT_CHAT = ("embed", "tts", "whisper", "transcribe", "audio", "realtime", "dall-e", "image", "moderation",
             "search", "davinci", "babbage", "computer-use", "sora")


def chat_models(models: list[str], base_url: str = "", kind: str = "openai") -> list[str]:
    """Modèles utilisables pour écrire du texte, triés (OpenAI liste aussi voix, images, embeddings…)."""
    if kind == "openai" and not base_url:
        models = [m for m in models if m.startswith(("gpt-", "o1", "o3", "o4", "chatgpt"))
                  and not any(x in m for x in _NOT_CHAT)]
    else:
        models = [m for m in models if not any(x in m.lower() for x in ("embed", "tts", "whisper"))]
    return sorted(set(models))


class Engine(Protocol):
    name: str

    def propose(self, tr: Transcript, req: Request, progress: Progress = ...) -> list[Candidate]: ...


# -- montage par le texte ---------------------------------------------------------------------
def ranges_from_sentences(tr: Transcript, keep: list[int], merge_gap: float = 0.35,
                          pad: float = 0.08) -> list[tuple[float, float]]:
    """Plages source à conserver quand on supprime des phrases (§6 : édition textuelle)."""
    out: list[tuple[float, float]] = []
    for i in sorted(set(keep)):
        s = tr.segments[i]
        a, b = max(0.0, s.start - pad), s.end + pad
        if out and a - out[-1][1] <= merge_gap and i - 1 in keep:
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out
