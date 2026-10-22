"""Campagne de clipping (§4 mode 5) : brief et contrôle de conformité des vidéos et des textes.

Le brief est enregistré dans le projet. Chaque export est contrôlé sur ce qui est réellement vérifiable :
format, durée, sous-titres, mots prononcés ou affichés, mots interdits ; chaque publication sur ses
hashtags, mentions et la mention de partenariat. Rien n'est déclaré conforme sans avoir été vérifié.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field


@dataclass
class Brief:
    name: str = ""
    client: str = ""
    deadline: str = ""                       # AAAA-MM-JJ
    platforms: list = field(default_factory=list)
    aspects: list = field(default_factory=list)          # formats acceptés ; vide = tous
    min_s: float = 0.0
    max_s: float = 0.0                                    # 0 = pas de limite
    target_count: int = 0                                 # nombre de vidéos attendues
    require_subtitles: bool = False
    required_words: list = field(default_factory=list)   # à prononcer ou afficher (marque, produit…)
    forbidden_words: list = field(default_factory=list)
    required_hashtags: list = field(default_factory=list)
    required_mentions: list = field(default_factory=list)
    disclosure: str = ""                                  # ex. « #pub » ou « Partenariat rémunéré »
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "Brief":
        d = d or {}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @property
    def defined(self) -> bool:
        return bool(self.name.strip())


@dataclass
class Check:
    rule: str
    ok: bool | None          # None = non vérifiable (information manquante)
    detail: str = ""


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9#@]+", " ", s)


def contains(text: str, word: str) -> bool:
    """Présence d'un mot ou d'une expression, sans tenir compte des accents ni de la casse."""
    w = _norm(word).strip()
    return bool(w) and re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", _norm(text)) is not None


def split_list(raw: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,;\n]+", raw or "") if x.strip()]


def check_video(brief: Brief, info: dict) -> list[Check]:
    """info : {"aspect", "duration", "subtitles": bool, "spoken": texte prononcé (extraits),
    "shown": texte affiché (sous-titres + textes), "transcribed": bool}."""
    out: list[Check] = []
    if brief.aspects:
        out.append(Check("Format", info.get("aspect") in brief.aspects,
                         f"{info.get('aspect')} (attendu : {', '.join(brief.aspects)})"))
    d = info.get("duration")
    if brief.min_s or brief.max_s:
        ok = d is not None and d >= brief.min_s and (not brief.max_s or d <= brief.max_s)
        lim = f"{brief.min_s:g}–{brief.max_s:g} s" if brief.max_s else f"≥ {brief.min_s:g} s"
        out.append(Check("Durée", ok if d is not None else None, f"{d:.1f} s (attendu : {lim})" if d else "inconnue"))
    if brief.require_subtitles:
        out.append(Check("Sous-titres", bool(info.get("subtitles")),
                         "incrustés" if info.get("subtitles") else "absents"))
    text = f"{info.get('spoken', '')} {info.get('shown', '')}"
    known = info.get("transcribed") or info.get("shown")
    for w in brief.required_words:
        ok = contains(text, w)
        out.append(Check(f"Mot requis « {w} »", True if ok else (False if known else None),
                         "présent" if ok else ("absent" if known else "transcrivez la vidéo pour vérifier")))
    for w in brief.forbidden_words:
        bad = contains(text, w)
        out.append(Check(f"Mot interdit « {w} »", False if bad else (True if known else None),
                         "présent !" if bad else ("absent" if known else "transcrivez la vidéo pour vérifier")))
    return out


def check_text(brief: Brief, text: str) -> list[Check]:
    """Texte de publication (titre + description + hashtags)."""
    out = []
    for h in brief.required_hashtags:
        tag = "#" + h.lstrip("#")
        out.append(Check(f"Hashtag {tag}", contains(text, tag), ""))
    for m in brief.required_mentions:
        at = "@" + m.lstrip("@")
        out.append(Check(f"Mention {at}", contains(text, at), ""))
    if brief.disclosure.strip():
        out.append(Check("Mention de partenariat", contains(text, brief.disclosure),
                         f"« {brief.disclosure} » {'présente' if contains(text, brief.disclosure) else 'absente'}"))
    for w in brief.forbidden_words:
        if contains(text, w):
            out.append(Check(f"Mot interdit « {w} »", False, "dans le texte de publication"))
    return out


def summary(checks: list[Check]) -> str:
    bad = sum(c.ok is False for c in checks)
    unk = sum(c.ok is None for c in checks)
    if bad:
        return f"⚠ {bad} non conforme(s)"
    if unk:
        return f"? {unk} à vérifier"
    return "✓ Conforme"


def video_info(tl_dict: dict, report: dict, transcripts: dict) -> dict:
    """Construit `info` à partir d'une timeline exportée, du rapport d'export et des transcriptions
    {asset_id: Transcript} (texte prononcé dans les plages réellement montées)."""
    from .model import Timeline
    tl = Timeline.from_dict(tl_dict)
    spoken, transcribed = [], False
    for sg in tl.segments:
        tr = transcripts.get(sg.asset_id)
        if tr is None:
            continue
        transcribed = True
        for s in tr.segments:
            if s.end > sg.src_in and s.start < sg.src_out:
                spoken.append(s.text)
    shown = [c.text for c in tl.subtitles.cues] if tl.subtitles.enabled else []
    shown += [t.text for t in tl.texts]
    return {"aspect": tl.format.aspect, "duration": report.get("duration") or tl.duration,
            "subtitles": bool(tl.subtitles.enabled and tl.subtitles.cues and tl.subtitles.burn),
            "spoken": " ".join(spoken), "shown": " ".join(shown), "transcribed": transcribed}
