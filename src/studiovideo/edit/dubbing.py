"""Doublage (cahier des charges §10.2 modes 2-3, §11).

À partir d'une transcription traduite :
1. chaque phrase est synthétisée (une voix par intervenant) ;
2. elle est placée à l'heure de la phrase d'origine et accélérée au besoin (×1,35 au plus) pour tenir avant la
   suivante ; trop longue malgré cela, elle n'est jamais coupée : elle déborde et les suivantes se décalent
   d'autant, puis se recalent dans les silences ;
3. une piste de voix complète, alignée sur le média source, est assemblée.
Le manifeste permet de régénérer une seule phrase.
"""
from __future__ import annotations

import json
import subprocess
import sys
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from ..ai.tts import TTSEngine, wav_duration
from ..media import ffmpeg
from ..transcribe.model import Transcript

_NOWIN = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
RATE = 48000
MAX_TEMPO = 1.35
CATCH_UP_TEMPO = 1.5                          # phrase en retard sur l'original : un peu plus vite pour rattraper


@dataclass
class DubLine:
    index: int
    start: float
    end: float
    text: str
    speaker: str
    voice: str
    file: str = ""
    raw_duration: float = 0.0
    tempo: float = 1.0
    placed_end: float = 0.0
    error: str = ""
    placed_start: float = -1.0               # début réel dans la piste (-1 : celui de la phrase d'origine)


@dataclass
class DubManifest:
    asset_id: int
    language: str
    engine: str
    voices: dict[str, str]
    duration: float
    lines: list[DubLine] = field(default_factory=list)
    track: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "DubManifest":
        d = json.loads(s)
        d["lines"] = [DubLine(**x) for x in d.get("lines", [])]
        return cls(**d)


def _fit(src: Path, dest: Path, tempo: float) -> Path:
    """Rééchantillonne en 48 kHz mono et accélère si besoin (sans changer la hauteur)."""
    af = f"aresample={RATE}"
    if tempo > 1.001:
        af = f"atempo={tempo:.4f},{af}"
    p = subprocess.run([ffmpeg.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(src),
                        "-af", af, "-ac", "1", "-c:a", "pcm_s16le", str(dest)], capture_output=True, text=True,
                       creationflags=_NOWIN)
    if p.returncode != 0:
        raise RuntimeError(f"Ajustement de la voix impossible : {p.stderr[-300:]}")
    return dest


GAP = 0.06                                   # silence minimal entre deux phrases doublées


def _raw(line: DubLine, engine: TTSEngine, language: str, work: Path, speed: float = 1.0) -> Path:
    raw = work / f"raw_{line.index:05d}.wav"
    engine.synth(line.text, line.voice, language, raw, speed)
    line.raw_duration = wav_duration(raw)
    return raw


def _place(line: DubLine, free_from: float, next_start: float | None) -> None:
    """Début (jamais avant la fin de la phrase précédente) et accélération pour tenir avant la suivante."""
    start = max(line.start, free_from)
    room = (next_start - start - GAP) if next_start is not None else (line.end - start) + 1.5
    room = max(0.1, room, line.end - start)
    limit = CATCH_UP_TEMPO if start - line.start > 0.25 else MAX_TEMPO
    tempo = 1.0 if line.raw_duration <= room else min(limit, line.raw_duration / room)
    line.placed_start, line.tempo = round(start, 3), round(tempo, 3)
    line.placed_end = round(start + line.raw_duration / tempo, 3)


def _finish(line: DubLine, raw: Path, work: Path) -> None:
    fitted = work / f"line_{line.index:05d}.wav"
    _fit(raw, fitted, line.tempo)
    line.file = fitted.name
    line.placed_end = round(line.placed_start + wav_duration(fitted), 3)
    raw.unlink(missing_ok=True)


def synth_line(line: DubLine, engine: TTSEngine, language: str, work: Path, next_start: float | None,
               speed: float = 1.0, free_from: float = 0.0) -> DubLine:
    """Une phrase : synthèse, placement, ajustement (régénération d'une seule phrase)."""
    raw = _raw(line, engine, language, work, speed)
    _place(line, free_from, next_start)
    _finish(line, raw, work)
    return line


def assemble(manifest: DubManifest, work: Path) -> Path:
    """Piste de voix complète (48 kHz mono) alignée sur le média source."""
    import numpy as np
    total = int((manifest.duration + 1.0) * RATE)
    track = np.zeros(total, dtype=np.float32)
    for ln in manifest.lines:
        if not ln.file or ln.error:
            continue
        with wave.open(str(work / ln.file), "rb") as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        a = int((ln.placed_start if ln.placed_start >= 0 else ln.start) * RATE)
        if a + x.size > total:                  # dernière phrase qui déborde : la piste s'allonge (rien n'est coupé)
            track = np.concatenate([track, np.zeros(a + x.size - total, dtype=np.float32)])
            total = track.size
        b = a + x.size
        n = max(0, b - a)
        if n:
            fade = min(n, int(0.01 * RATE))
            seg = x[:n].copy()
            if fade:
                seg[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
            track[a:a + n] += seg
    peak = float(np.max(np.abs(track))) if track.size else 0.0
    if peak > 0.98:
        track *= 0.98 / peak
    out = work / "dub.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((track * 32767).astype("<i2").tobytes())
    manifest.track = out.name
    return out


def build(tr: Transcript, engine: TTSEngine, voices: dict[str, str], default_voice: str, asset_id: int,
          duration: float, work: Path, progress: Callable[[float, str], None] = lambda f, m: None,
          is_cancelled: Callable[[], bool] = lambda: False, speed: float = 1.0) -> DubManifest:
    work.mkdir(parents=True, exist_ok=True)
    man = DubManifest(asset_id, tr.language or "", engine.name, voices, duration)
    segs = tr.segments
    raws: dict[int, Path] = {}
    for i, s in enumerate(segs):
        if is_cancelled():
            raise RuntimeError("Annulé.")
        if not s.text.strip():
            continue
        ln = DubLine(i, s.start, s.end, s.text.strip(), s.speaker, voices.get(s.speaker) or default_voice)
        try:
            raws[i] = _raw(ln, engine, tr.language or "", work, speed)
        except Exception as exc:  # une phrase en échec n'arrête pas le doublage : elle est signalée
            ln.error = str(exc)[:300]
        man.lines.append(ln)
        progress(0.05 + 0.8 * (i + 1) / max(1, len(segs)), f"Voix : phrase {i + 1}/{len(segs)}")
    if man.lines and all(ln.error for ln in man.lines):
        raise RuntimeError("Aucune phrase n'a pu être synthétisée : " + man.lines[0].error)
    ok = [ln for ln in man.lines if not ln.error]
    free = 0.0
    for k, ln in enumerate(ok):               # placement : dans l'ordre, sans chevauchement ni coupure
        _place(ln, free, ok[k + 1].start if k + 1 < len(ok) else max(duration, ln.end + 0.3))
        try:
            _finish(ln, raws[ln.index], work)
        except Exception as exc:
            ln.error = str(exc)[:300]
            continue
        free = ln.placed_end + GAP
    progress(0.95, "Assemblage de la piste de voix")
    assemble(man, work)
    return man


def regenerate(man: DubManifest, index: int, text: str | None, voice: str | None, engine: TTSEngine,
               work: Path) -> DubManifest:
    """Régénère une seule phrase (§11.2) puis réassemble la piste."""
    lines = {ln.index: ln for ln in man.lines}
    ln = lines[index]
    if text is not None:
        ln.text = text
    if voice:
        ln.voice = voice
    later = [x for x in man.lines if x.start > ln.start and not x.error]
    nxt = min(later, key=lambda x: x.start) if later else None
    before = [x.placed_end for x in man.lines if x.start < ln.start and not x.error and x.placed_end]
    ln.error = ""
    synth_line(ln, engine, man.language, work,
               (nxt.placed_start if nxt.placed_start >= 0 else nxt.start) if nxt else None,
               free_from=(max(before) + GAP) if before else 0.0)
    assemble(man, work)
    return man
