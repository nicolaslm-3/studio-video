"""Sous-titres du montage (cahier des charges §9).

Les sous-titres sont générés depuis les transcriptions des médias, convertis du
temps source vers le temps du montage, puis découpés selon des règles (mots,
caractères, lignes, pauses, ponctuation). Chaque sous-titre reste modifiable.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field, fields

ANIMATIONS = ("none", "highlight", "word", "pop", "fade")
_SENTENCE_END = re.compile(r"[.!?…]$")
_CLAUSE_END = re.compile(r"[,;:]$")


def _id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class SubtitleStyle:
    font: str = "Segoe UI"
    size: int = 76               # px pour une image de 1920 de haut
    bold: bool = True
    italic: bool = False
    uppercase: bool = False
    color: str = "#FFFFFF"
    highlight_color: str = "#FFD54A"
    outline_color: str = "#000000"
    outline: int = 7
    shadow: bool = True
    box: bool = False
    box_color: str = "#000000"
    box_opacity: float = 0.55
    position: str = "bottom"     # top | center | bottom | custom
    y: float = 0.68              # centre vertical si position = custom (0..1)
    max_width: float = 0.86
    line_spacing: float = 1.05


@dataclass
class SubtitleRules:
    max_words: int = 6
    max_chars: int = 28          # par ligne
    max_lines: int = 2
    min_duration: float = 0.7
    max_duration: float = 4.0
    pause_split: float = 0.55    # une pause plus longue démarre un nouveau sous-titre
    lead: float = 0.0            # avance d'affichage (s)


@dataclass
class Cue:
    start: float
    end: float
    text: str
    words: list[list] = field(default_factory=list)   # [début, fin, mot] en temps montage
    id: str = field(default_factory=_id)

    def word_list(self) -> list[tuple[float, float, str]]:
        if self.words and len(self.words) == len(self.text.split()):
            return [(float(a), float(b), str(c)) for a, b, c in self.words]
        toks = self.text.split()
        if not toks:
            return []
        step = (self.end - self.start) / len(toks)
        return [(self.start + i * step, self.start + (i + 1) * step, w) for i, w in enumerate(toks)]


@dataclass
class SubtitleTrack:
    enabled: bool = True
    burn: bool = True              # incruster dans la vidéo
    sidecar_srt: bool = False      # fichier .srt à côté de la vidéo
    animation: str = "highlight"
    language: str = ""             # "" = langue d'origine ; sinon code de la traduction utilisée
    style: SubtitleStyle = field(default_factory=SubtitleStyle)
    rules: SubtitleRules = field(default_factory=SubtitleRules)
    cues: list[Cue] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict | None) -> "SubtitleTrack":
        d = d or {}

        def pick(c, x):
            names = {f.name for f in fields(c)}
            return c(**{k: v for k, v in (x or {}).items() if k in names})
        t = pick(cls, {k: v for k, v in d.items() if k not in ("style", "rules", "cues")})
        t.style = pick(SubtitleStyle, d.get("style"))
        t.rules = pick(SubtitleRules, d.get("rules"))
        t.cues = [pick(Cue, c) for c in d.get("cues", [])]
        if t.animation not in ANIMATIONS:
            t.animation = "none"
        return t

    def to_dict(self) -> dict:
        return asdict(self)

    def cue_at(self, t: float) -> Cue | None:
        for c in self.cues:
            if c.start <= t < c.end:
                return c
        return None

    def cue(self, cue_id: str) -> Cue:
        for c in self.cues:
            if c.id == cue_id:
                return c
        raise KeyError(cue_id)

    def sort(self) -> None:
        self.cues.sort(key=lambda c: c.start)


# -- génération ------------------------------------------------------------------
def timeline_words(tl, transcripts: dict) -> list[tuple[float, float, str, int]]:
    """Mots de la timeline en temps montage : (début, fin, mot, index du segment)."""
    out = []
    t0 = 0.0
    for i, seg in enumerate(tl.segments):
        tr = transcripts.get(seg.asset_id)
        if tr is not None and not seg.muted:
            for w in tr.all_words():
                mid = (w.start + w.end) / 2
                if seg.src_in <= mid < seg.src_out:
                    a = t0 + max(0.0, w.start - seg.src_in)
                    b = t0 + min(seg.duration, w.end - seg.src_in)
                    txt = w.text.strip()
                    if txt and b > a:
                        out.append((a, b, txt, i))
        t0 += seg.duration
    return out


def wrap_lines(words: list[str], max_chars: int, max_lines: int) -> list[list[str]]:
    """Répartit des mots sur des lignes équilibrées (la dernière ligne n'est pas une « veuve »)."""
    if not words:
        return []
    text_len = len(" ".join(words))
    n_lines = min(max_lines, max(1, -(-text_len // max(8, max_chars))))
    if n_lines == 1:
        return [words]
    target = text_len / n_lines
    lines, cur, cur_len = [], [], 0
    for w in words:
        add = len(w) + (1 if cur else 0)
        if cur and cur_len + add > target + 2 and len(lines) < n_lines - 1:
            lines.append(cur)
            cur, cur_len = [], 0
            add = len(w)
        cur.append(w)
        cur_len += add
    lines.append(cur)
    return lines


def build_cues(words: list[tuple[float, float, str, int]], rules: SubtitleRules, total: float) -> list[Cue]:
    cues: list[Cue] = []
    cur: list[tuple[float, float, str, int]] = []
    cap = rules.max_chars * rules.max_lines

    def flush() -> None:
        if cur:
            cues.append(Cue(cur[0][0], cur[-1][1], " ".join(w[2] for w in cur),
                            [[round(a, 3), round(b, 3), t] for a, b, t, _ in cur]))
            cur.clear()

    for w in words:
        if cur:
            prev = cur[-1]
            length = len(" ".join(x[2] for x in cur)) + 1 + len(w[2])
            if (len(cur) >= rules.max_words or length > cap or w[0] - prev[1] > rules.pause_split
                    or w[3] != prev[3] or w[1] - cur[0][0] > rules.max_duration
                    or (_SENTENCE_END.search(prev[2]) and len(cur) >= 2)
                    or (_CLAUSE_END.search(prev[2]) and len(cur) >= max(2, rules.max_words // 2))):
                flush()
        cur.append(w)
    flush()
    # durées minimales sans chevauchement, avance d'affichage
    for i, c in enumerate(cues):
        nxt = cues[i + 1].start if i + 1 < len(cues) else total
        c.start = max(0.0, c.start - rules.lead)
        c.end = min(max(c.end, c.start + rules.min_duration), nxt, total)
        c.end = max(c.end, min(c.start + 0.2, total))
    return cues


def generate(tl, transcripts: dict) -> list[Cue]:
    return build_cues(timeline_words(tl, transcripts), tl.subtitles.rules, tl.duration)


# -- export texte --------------------------------------------------------------------
def to_srt(cues: list[Cue], max_chars: int = 42, max_lines: int = 2) -> str:
    from ..transcribe.model import _ts
    out = []
    for i, c in enumerate(sorted(cues, key=lambda x: x.start), 1):
        lines = wrap_lines(c.text.split(), max_chars, max_lines)
        out += [str(i), f"{_ts(c.start)} --> {_ts(c.end)}", "\n".join(" ".join(l) for l in lines), ""]
    return "\n".join(out)


# -- états d'affichage (pour l'aperçu et l'incrustation) -------------------------------------
@dataclass(frozen=True)
class State:
    cue_index: int          # -1 = rien à afficher
    highlight: int = -1     # mot mis en valeur
    visible: int = -1       # nombre de mots visibles (-1 = tous)
    scale: float = 1.0
    alpha: float = 1.0


def states(track: SubtitleTrack, total: float, fps: float = 30.0) -> list[tuple[float, float, State]]:
    """Découpe la durée en intervalles d'affichage constants."""
    out: list[tuple[float, float, State]] = []
    t = 0.0
    anim = track.animation
    frame = 1.0 / max(1.0, fps)
    for ci, c in enumerate(sorted(track.cues, key=lambda x: x.start)):
        if c.end <= t or c.start >= total:
            continue
        s, e = max(c.start, t), min(c.end, total)
        if s > t:
            out.append((t, s, State(-1)))
        words = c.word_list()
        parts: list[tuple[float, float, State]] = []
        if anim in ("highlight", "word", "pop") and words:
            for k, (ws, _we, _w) in enumerate(words):
                a = s if k == 0 else max(s, ws)
                b = e if k == len(words) - 1 else max(a, min(e, words[k + 1][0]))
                if b - a <= 0:
                    continue
                if anim == "word":
                    parts.append((a, b, State(ci, highlight=k, visible=k + 1)))
                else:
                    parts.append((a, b, State(ci, highlight=k)))
        else:
            parts.append((s, e, State(ci)))
        if anim in ("pop", "fade") and parts:
            a0, b0, st0 = parts[0]
            steps = ([0.82, 1.08, 1.0] if anim == "pop" else [1.0, 1.0, 1.0])
            alphas = ([1.0, 1.0, 1.0] if anim == "pop" else [0.25, 0.6, 1.0])
            dur = min(b0 - a0, 3 * max(frame, 0.04))
            sub = []
            for j in range(3):
                x0 = a0 + dur * j / 3
                x1 = a0 + dur * (j + 1) / 3 if j < 2 else b0
                sub.append((x0, x1, State(st0.cue_index, st0.highlight, st0.visible, steps[j], alphas[j])))
            parts = sub + parts[1:]
        out.extend(p for p in parts if p[1] > p[0])
        t = e
    if t < total:
        out.append((t, total, State(-1)))
    return out
