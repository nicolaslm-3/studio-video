"""Forme d'onde des médias pour la timeline : un pic (0..1) par 1/50 s, calculé une fois et mis en cache.

Le son est lu par FFmpeg en mono 8 kHz et traité par blocs (mémoire constante, même pour une vidéo d'une
heure). Cache : <données>/waveforms/<empreinte>.npy (quelques dizaines de Ko par heure de média).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

from . import ffmpeg

RATE = 50                       # pics par seconde
_SR = 8000
_HOP = _SR // RATE
_NOWIN = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def cache_path(root: Path, fingerprint: str) -> Path:
    safe = "".join(c for c in fingerprint if c.isalnum() or c in "-_")[:80] or "media"
    return Path(root) / "waveforms" / f"{safe}.npy"


def compute(path: Path, is_cancelled=lambda: False) -> np.ndarray:
    proc = subprocess.Popen([ffmpeg.ffmpeg_bin(), "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a:0",
                             "-ac", "1", "-ar", str(_SR), "-f", "s16le", "-"], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, creationflags=_NOWIN)
    out, rest = [], b""
    try:
        while True:
            buf = proc.stdout.read(_HOP * 2 * 500)
            if not buf:
                break
            if is_cancelled():
                proc.kill()
                raise RuntimeError("Annulé.")
            buf = rest + buf
            n = len(buf) // (_HOP * 2) * (_HOP * 2)
            rest = buf[n:]
            if n:
                a = np.frombuffer(buf[:n], np.int16).reshape(-1, _HOP)
                out.append(np.abs(a.astype(np.int32)).max(axis=1))
        if rest:
            out.append(np.array([np.abs(np.frombuffer(rest[: len(rest) // 2 * 2], np.int16).astype(np.int32)).max(initial=0)]))
    finally:
        err = proc.stderr.read().decode(errors="replace")
        proc.wait()
    if proc.returncode != 0 and not out:
        raise RuntimeError(f"Lecture du son impossible : {err[-300:]}")
    peaks = np.concatenate(out).astype(np.float32) / 32768.0 if out else np.zeros(0, np.float32)
    return np.sqrt(np.clip(peaks, 0, 1)).astype(np.float16)     # racine : les voix faibles restent visibles


def load(root: Path, fingerprint: str) -> np.ndarray | None:
    p = cache_path(root, fingerprint)
    try:
        return np.load(p) if p.exists() else None
    except (OSError, ValueError):
        return None


def ensure(root: Path, fingerprint: str, path: Path, is_cancelled=lambda: False) -> np.ndarray:
    got = load(root, fingerprint)
    if got is not None:
        return got
    peaks = compute(Path(path), is_cancelled)
    p = cache_path(root, fingerprint)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp.npy")
    np.save(tmp, peaks)
    tmp.replace(p)
    return peaks


def columns(peaks: np.ndarray, src_in: float, src_out: float, width: int) -> np.ndarray:
    """Pic par colonne de pixels pour la plage source [src_in, src_out]."""
    width = max(1, int(width))
    a, b = int(src_in * RATE), max(int(src_in * RATE) + 1, int(np.ceil(src_out * RATE)))
    seg = peaks[a:b].astype(np.float32)
    if seg.size == 0:
        return np.zeros(width, np.float32)
    if seg.size >= width:
        edges = np.linspace(0, seg.size, width + 1).astype(int)[:-1]
        return np.maximum.reduceat(seg, edges)
    return seg[np.minimum((np.arange(width) * seg.size / width).astype(int), seg.size - 1)]
