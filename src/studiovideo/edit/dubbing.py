"""Doublage (cahier des charges §10.2 modes 2-3, §11).

À partir d'une transcription traduite :
1. chaque phrase est synthétisée (une voix par intervenant) ;
2. sa durée est ajustée au temps de la phrase d'origine (accélération limitée à ×1,35,
   sinon la phrase déborde légèrement sur le silence suivant, sans chevaucher la suivante) ;
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


def synth_line(line: DubLine, engine: TTSEngine, language: str, work: Path, next_start: float | None,
               speed: float = 1.0) -> DubLine:
    raw = work / f"raw_{line.index:05d}.wav"
    engine.synth(line.text, line.voice, language, raw, speed)
    line.raw_duration = wav_duration(raw)
    slot = line.end - line.start
    room = (next_start - line.start - 0.05) if next_start is not None else slot + 1.5
    tempo = 1.0
    if line.raw_duration > slot:
        tempo = min(MAX_TEMPO, line.raw_duration / max(0.1, slot))
    fitted = work / f"line_{line.index:05d}.wav"
    _fit(raw, fitted, tempo)
    line.tempo = round(tempo, 3)
    line.file = fitted.name
    dur = wav_duration(fitted)
    line.placed_end = line.start + min(dur, max(slot, room))
    raw.unlink(missing_ok=True)
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
        a = int(ln.start * RATE)
        b = min(total, a + x.size, int(ln.placed_end * RATE) if ln.placed_end else a + x.size)
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
    for i, s in enumerate(segs):
        if is_cancelled():
            raise RuntimeError("Annulé.")
        if not s.text.strip():
            continue
        ln = DubLine(i, s.start, s.end, s.text.strip(), s.speaker, voices.get(s.speaker) or default_voice)
        nxt = segs[i + 1].start if i + 1 < len(segs) else None
        try:
            synth_line(ln, engine, tr.language or "", work, nxt, speed)
        except Exception as exc:  # une phrase en échec n'arrête pas le doublage : elle est signalée
            ln.error = str(exc)[:300]
        man.lines.append(ln)
        progress(0.05 + 0.85 * (i + 1) / max(1, len(segs)), f"Voix : phrase {i + 1}/{len(segs)}")
    if man.lines and all(ln.error for ln in man.lines):
        raise RuntimeError("Aucune phrase n'a pu être synthétisée : " + man.lines[0].error)
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
    later = sorted(x.start for x in man.lines if x.start > ln.start)
    ln.error = ""
    synth_line(ln, engine, man.language, work, later[0] if later else None)
    assemble(man, work)
    return man
