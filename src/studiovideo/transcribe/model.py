"""Transcription horodatée (cahier des charges §6).

Une transcription appartient à un média source et utilise son temps (secondes).
Le résultat brut du moteur est conservé ; les corrections sont stockées à part.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field


@dataclass
class Word:
    start: float
    end: float
    text: str
    prob: float = 1.0


@dataclass
class TSegment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    speaker: str = ""

    def word_texts(self) -> list[str]:
        return [w.text.strip() for w in self.words if w.text.strip()]


@dataclass
class Transcript:
    segments: list[TSegment] = field(default_factory=list)
    language: str | None = None
    engine: str = ""

    # -- sérialisation --------------------------------------------------------
    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict | list | None) -> "Transcript":
        if isinstance(d, list):  # ancien format : liste de segments
            d = {"segments": d}
        d = d or {}
        segs = []
        for s in d.get("segments", []):
            words = [Word(**w) for w in s.get("words", [])]
            segs.append(TSegment(start=s["start"], end=s["end"], text=s.get("text", ""), words=words,
                                 speaker=s.get("speaker", "")))
        return cls(segments=segs, language=d.get("language"), engine=d.get("engine", ""))

    @classmethod
    def from_json(cls, s: str | None) -> "Transcript":
        return cls.from_dict(json.loads(s) if s else None)

    # -- accès ------------------------------------------------------------------
    @property
    def duration(self) -> float:
        return self.segments[-1].end if self.segments else 0.0

    def text(self) -> str:
        return "\n".join(s.text.strip() for s in self.segments)

    def all_words(self) -> list[Word]:
        out: list[Word] = []
        for s in self.segments:
            out.extend(s.words if s.words else _even_words(s))
        return out

    def search(self, query: str) -> list[int]:
        q = _fold(query)
        return [i for i, s in enumerate(self.segments) if q and q in _fold(s.text)]

    def segment_at(self, t: float) -> int | None:
        for i, s in enumerate(self.segments):
            if s.start <= t < s.end:
                return i
        return None

    def speakers(self) -> list[str]:
        return sorted({s.speaker for s in self.segments if s.speaker})

    # -- correction -----------------------------------------------------------------
    def edit_segment(self, i: int, new_text: str) -> None:
        """Corrige le texte d'une phrase. Les horodatages des mots sont réalignés au mieux."""
        s = self.segments[i]
        new_text = " ".join(new_text.split())
        s.text = new_text
        tokens = new_text.split(" ") if new_text else []
        if not tokens:
            s.words = []
            return
        if s.words and len(s.words) == len(tokens):
            for w, tok in zip(s.words, tokens):
                w.text = " " + tok
            return
        # nombre de mots différent : répartition proportionnelle à la longueur des mots
        s.words = _spread(tokens, s.start, s.end)

    def set_times(self, i: int, start: float, end: float) -> None:
        s = self.segments[i]
        if end - start < 0.05:
            raise ValueError("Durée trop courte.")
        old_s, old_e = s.start, s.end
        s.start, s.end = start, end
        if s.words and old_e > old_s:
            k = (end - start) / (old_e - old_s)
            for w in s.words:
                w.start = start + (w.start - old_s) * k
                w.end = start + (w.end - old_s) * k

    def rename_speaker(self, old: str, new: str) -> None:
        for s in self.segments:
            if s.speaker == old:
                s.speaker = new


def _fold(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def _spread(tokens: list[str], start: float, end: float) -> list[Word]:
    weights = [max(1, len(t)) for t in tokens]
    total = sum(weights)
    out, t = [], start
    for tok, w in zip(tokens, weights):
        d = (end - start) * w / total
        out.append(Word(t, t + d, " " + tok, 0.5))
        t += d
    return out


def _even_words(s: TSegment) -> list[Word]:
    return _spread(s.text.split(), s.start, s.end) if s.text.strip() else []


# -- exports (§6 : TXT, SRT, VTT, JSON, ASS) ----------------------------------------------------
def _ts(t: float, sep: str = ",") -> str:
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _ass_ts(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def export_text(tr: Transcript, fmt: str, with_speakers: bool = True) -> str:
    fmt = fmt.lower()
    lines: list[str] = []

    def label(s: TSegment) -> str:
        return f"{s.speaker} : " if with_speakers and s.speaker else ""
    if fmt == "txt":
        return "\n".join(label(s) + s.text.strip() for s in tr.segments) + "\n"
    if fmt == "json":
        return json.dumps(asdict(tr), ensure_ascii=False, indent=2)
    if fmt == "srt":
        for i, s in enumerate(tr.segments, 1):
            lines += [str(i), f"{_ts(s.start)} --> {_ts(s.end)}", label(s) + s.text.strip(), ""]
        return "\n".join(lines)
    if fmt == "vtt":
        lines = ["WEBVTT", ""]
        for s in tr.segments:
            spk = f"<v {s.speaker}>" if with_speakers and s.speaker else ""
            lines += [f"{_ts(s.start, '.')} --> {_ts(s.end, '.')}", spk + s.text.strip(), ""]
        return "\n".join(lines)
    if fmt == "ass":
        head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n[V4+ Styles]\n"
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
                "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                "Alignment, MarginL, MarginR, MarginV, Encoding\n"
                "Style: Default,Arial,56,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,0,"
                "2,60,60,60,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
                "Effect, Text\n")
        ev = []
        for s in tr.segments:
            txt = s.text.strip().replace("\n", "\\N").replace("{", "(").replace("}", ")")
            ev.append(f"Dialogue: 0,{_ass_ts(s.start)},{_ass_ts(s.end)},Default,{s.speaker},0,0,0,,{txt}")
        return head + "\n".join(ev) + "\n"
    raise ValueError(f"Format inconnu : {fmt}")


EXPORT_FORMATS = ("txt", "srt", "vtt", "json", "ass")

_SRT_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")


def _parse_time(s: str) -> float:
    m = _SRT_TIME.search(s)
    if not m:
        parts = s.strip().split(":")
        if len(parts) == 2:  # VTT mm:ss.mmm
            mm, rest = parts
            ss, _, ms = rest.partition(".")
            return int(mm) * 60 + int(ss) + int((ms or "0").ljust(3, "0")[:3]) / 1000
        raise ValueError(f"Horodatage invalide : {s}")
    h, m_, s_, ms = m.groups()
    return int(h) * 3600 + int(m_) * 60 + int(s_) + int(ms.ljust(3, "0")) / 1000


def import_subtitles(text: str) -> Transcript:
    """Importe un fichier SRT ou VTT existant comme transcription (sans moteur IA)."""
    text = text.replace("\r\n", "\n").replace("﻿", "")
    blocks = re.split(r"\n\s*\n", text.strip())
    segs = []
    for b in blocks:
        lines = [l for l in b.split("\n") if l.strip()]
        idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if idx is None:
            continue
        a, _, z = lines[idx].partition("-->")
        start, end = _parse_time(a), _parse_time(z.strip().split(" ")[0])
        body = " ".join(lines[idx + 1:])
        speaker = ""
        m = re.match(r"<v\s+([^>]+)>(.*)", body)
        if m:
            speaker, body = m.group(1).strip(), m.group(2)
        body = re.sub(r"<[^>]+>", "", body).strip()
        if body and end > start:
            segs.append(TSegment(start, end, body, _spread(body.split(), start, end), speaker))
    if not segs:
        raise ValueError("Aucun sous-titre reconnu dans ce fichier (formats acceptés : SRT, VTT).")
    return Transcript(segs, None, "import")
