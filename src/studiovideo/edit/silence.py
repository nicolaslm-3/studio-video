"""Suppression des silences (« jump cut ») : retire les blancs d'une prise de parole.

Détection sur le son réel de chaque extrait (FFmpeg silencedetect, seuil relatif au niveau de la voix),
ou, si le média est transcrit, sur les intervalles entre les mots. Une petite marge est gardée autour
de la parole pour que les coupes restent naturelles. Les extraits sont découpés, jamais les fichiers.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from ..media import ffmpeg
from .model import MIN_SEGMENT, Segment, Timeline

_NOWIN = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def detect(path: Path, start: float, end: float, noise_db: float = -35.0, min_len: float = 0.6) -> list[tuple]:
    """Silences [(début, fin)] en temps source dans [start, end]."""
    p = subprocess.run([ffmpeg.ffmpeg_bin(), "-hide_banner", "-nostdin", "-ss", f"{start:.3f}", "-t",
                        f"{max(0.05, end - start):.3f}", "-i", str(path), "-map", "0:a:0", "-af",
                        f"silencedetect=noise={noise_db:g}dB:d={min_len:g}", "-f", "null", "-"],
                       capture_output=True, text=True, creationflags=_NOWIN)
    if p.returncode != 0:
        raise RuntimeError(f"Analyse du son impossible : {p.stderr[-300:]}")
    out, cur = [], None
    for line in p.stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            cur = start + max(0.0, float(m.group(1)))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and cur is not None:
            out.append((cur, start + float(m.group(1))))
            cur = None
    if cur is not None:
        out.append((cur, end))
    return out


def from_transcript(tr, start: float, end: float, min_len: float = 0.6) -> list[tuple]:
    """Blancs entre les mots (ou phrases) de la transcription, dans [start, end]."""
    marks = []
    for s in tr.segments:
        if s.words:
            marks += [(w.start, w.end) for w in s.words]
        else:
            marks.append((s.start, s.end))
    marks = sorted((a, b) for a, b in marks if b > start and a < end)
    gaps, t = [], start
    for a, b in marks:
        if a - t >= min_len:
            gaps.append((t, a))
        t = max(t, b)
    if end - t >= min_len:
        gaps.append((t, end))
    return gaps


def keep_ranges(start: float, end: float, silences: list[tuple], pad: float = 0.15) -> list[tuple]:
    """Plages conservées : tout sauf les silences (réduits de `pad` de chaque côté)."""
    keep, t = [], start
    for a, b in sorted(silences):
        a2, b2 = max(start, a + pad), min(end, b - pad)
        if b2 - a2 < 0.2:
            continue
        if a2 - t >= MIN_SEGMENT:
            keep.append((t, a2))
        t = b2
    if end - t >= MIN_SEGMENT:
        keep.append((t, end))
    return keep


def apply(tl: Timeline, silences_by_segment: dict[str, list[tuple]], pad: float = 0.15) -> dict:
    """Remplace chaque extrait par ses plages parlées. Retourne {"removed": secondes, "cuts": nombre}."""
    before = tl.duration
    out, cuts = [], 0
    for seg in tl.segments:
        sil = silences_by_segment.get(seg.id)
        if not sil:
            out.append(seg)
            continue
        ranges = keep_ranges(seg.src_in, seg.src_out, sil, pad)
        if not ranges:
            out.append(seg)
            continue
        cuts += len(ranges) - 1 + (ranges[0][0] > seg.src_in + 0.01) + (ranges[-1][1] < seg.src_out - 0.01)
        for k, (a, b) in enumerate(ranges):
            ns = Segment(asset_id=seg.asset_id, src_in=round(a, 3), src_out=round(b, 3), volume_db=seg.volume_db,
                         muted=seg.muted, reframe=list(seg.reframe))
            if k == 0:
                ns.id = seg.id
            out.append(ns)
    tl.segments = out
    return {"removed": round(before - tl.duration, 2), "cuts": cuts}
