"""Séparation voix / musique (cahier des charges §9, §10.2 mode 3) — exécutée sur le PC.

Modèles MDX-Net au format ONNX (projet Ultimate Vocal Remover, licence MIT), téléchargés
à la demande depuis GitHub dans le dossier des modèles. L'inférence utilise onnxruntime
(déjà embarqué) et une STFT numpy : aucun envoi de données.

Sorties : voice.wav (voix seule) et music.wav (tout le reste : musique, ambiance),
44,1 kHz stéréo, alignées sur le média.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from ..media import ffmpeg

_NOWIN = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
RATE = 44100
HOP = 1024
BASE_URL = "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/"


@dataclass(frozen=True)
class SepModel:
    key: str
    label: str
    file: str
    n_fft: int
    dim_f: int
    dim_t: int
    compensate: float
    size_mb: int
    sha256: str


MODELS = [
    SepModel("kim_vocal_2", "Voix (Kim Vocal 2) — recommandé", "Kim_Vocal_2.onnx", 7680, 3072, 256, 1.009, 64,
             "ce74ef3b6a6024ce44211a07be9cf8bc6d87728cc852a68ab34eb8e58cde9c8b"),
]
BY_KEY = {m.key: m for m in MODELS}


class SeparationError(RuntimeError):
    pass


def model_path(models_dir: Path, m: SepModel) -> Path:
    return models_dir / "separation" / m.file


def installed(models_dir: Path) -> list[str]:
    return [m.key for m in MODELS if model_path(models_dir, m).exists()]


def available() -> bool:
    try:
        import onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def download_model(models_dir: Path, key: str, progress: Callable[[float, str], None] = lambda f, m: None,
                   is_cancelled: Callable[[], bool] = lambda: False, base_url: str = BASE_URL) -> Path:
    m = BY_KEY[key]
    dest = model_path(models_dir, m)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".partial")
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(base_url + m.file, timeout=60) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or m.size_mb * 1024 * 1024)
            done = 0
            while True:
                if is_cancelled():
                    raise SeparationError("Annulé.")
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                progress(min(0.99, done / total), f"Téléchargement {done >> 20}/{total >> 20} Mo")
    except (OSError, urllib.error.URLError) as exc:
        part.unlink(missing_ok=True)
        raise SeparationError(f"Téléchargement impossible : {exc}") from exc
    except SeparationError:
        part.unlink(missing_ok=True)
        raise
    if m.sha256 and h.hexdigest() != m.sha256:
        part.unlink(missing_ok=True)
        raise SeparationError("Fichier du modèle corrompu (empreinte SHA-256 différente).")
    part.replace(dest)
    return dest


# -- STFT compatible avec torch.stft(center=True, fenêtre de Hann périodique) ------------------
def _window(n_fft: int) -> np.ndarray:
    return (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n_fft) / n_fft)).astype(np.float32)


def stft(x: np.ndarray, n_fft: int, hop: int, win: np.ndarray) -> np.ndarray:
    """x : (C, N) → (C, n_fft//2+1, T) complexe."""
    pad = n_fft // 2
    xp = np.pad(x, ((0, 0), (pad, pad)), mode="reflect")
    t = 1 + (xp.shape[1] - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(t)[:, None]
    frames = xp[:, idx] * win                     # (C, T, n_fft)
    return np.fft.rfft(frames, axis=-1).transpose(0, 2, 1)


def istft(spec: np.ndarray, n_fft: int, hop: int, win: np.ndarray, length: int) -> np.ndarray:
    """(C, F, T) → (C, length), inverse de stft() (ajout-recouvrement normalisé)."""
    frames = np.fft.irfft(spec.transpose(0, 2, 1), n=n_fft, axis=-1) * win     # (C, T, n_fft)
    c, t, _ = frames.shape
    total = n_fft + hop * (t - 1)
    out = np.zeros((c, total), dtype=np.float64)
    norm = np.zeros(total, dtype=np.float64)
    for i in range(t):
        out[:, i * hop:i * hop + n_fft] += frames[:, i]
        norm[i * hop:i * hop + n_fft] += win ** 2
    out /= np.where(norm > 1e-8, norm, 1.0)
    pad = n_fft // 2
    return out[:, pad:pad + length].astype(np.float32)


class MDXSeparator:
    def __init__(self, models_dir: Path, key: str = "kim_vocal_2", threads: int = 0):
        if not available():
            raise SeparationError("Le moteur de séparation (onnxruntime) n'est pas installé dans cette version.")
        self.m = BY_KEY[key]
        path = model_path(models_dir, self.m)
        if not path.exists():
            raise SeparationError(f"Modèle « {self.m.label} » non installé (Paramètres → Modèles).")
        import onnxruntime as ort
        so = ort.SessionOptions()
        if threads:
            so.intra_op_num_threads = threads
        self.sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
        self.win = _window(self.m.n_fft)
        self.chunk = HOP * (self.m.dim_t - 1)
        self.trim = self.m.n_fft // 2
        self.gen = self.chunk - 2 * self.trim

    def _run_chunk(self, waves: np.ndarray) -> np.ndarray:
        m = self.m
        spec = stft(waves, m.n_fft, HOP, self.win)[:, :m.dim_f]          # (2, F, T)
        x = np.stack([spec[0].real, spec[0].imag, spec[1].real, spec[1].imag])[None].astype(np.float32)
        y = self.sess.run(None, {self.sess.get_inputs()[0].name: x})[0][0]
        full = np.zeros((2, m.n_fft // 2 + 1, y.shape[-1]), dtype=np.complex64)
        full[0, :m.dim_f] = y[0] + 1j * y[1]
        full[1, :m.dim_f] = y[2] + 1j * y[3]
        return istft(full, m.n_fft, HOP, self.win, waves.shape[1])

    def separate(self, mix: np.ndarray, progress: Callable[[float, str], None] = lambda f, m: None,
                 is_cancelled: Callable[[], bool] = lambda: False) -> tuple[np.ndarray, np.ndarray]:
        """mix : (2, N) float32 à 44,1 kHz → (voix, reste)."""
        n = mix.shape[1]
        pad = self.gen - n % self.gen
        mp = np.concatenate([np.zeros((2, self.trim), np.float32), mix, np.zeros((2, pad + self.trim), np.float32)],
                            axis=1)
        out = []
        steps = range(0, n + pad, self.gen)
        for j, i in enumerate(steps):
            if is_cancelled():
                raise SeparationError("Annulé.")
            y = self._run_chunk(mp[:, i:i + self.chunk])
            out.append(y[:, self.trim:self.trim + self.gen])
            progress((j + 1) / len(steps), f"Séparation {int(100 * (j + 1) / len(steps))} %")
        voice = np.concatenate(out, axis=1)[:, :n] * self.m.compensate
        return voice, mix - voice


def _read_mix(src: Path, start: float | None = None, dur: float | None = None) -> np.ndarray:
    cmd = [ffmpeg.ffmpeg_bin(), "-v", "error", "-nostdin"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", str(src), "-map", "0:a:0", "-ac", "2", "-ar", str(RATE), "-f", "f32le", "-"]
    p = subprocess.run(cmd, capture_output=True, creationflags=_NOWIN)
    if p.returncode != 0:
        raise SeparationError(f"Lecture du son impossible : {p.stderr[-300:].decode('utf-8', 'replace')}")
    return np.frombuffer(p.stdout, "<f4").reshape(-1, 2).T.copy()


def write_wav(dest: Path, x: np.ndarray, rate: int = RATE) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    y = np.clip(x.T, -1.0, 1.0)
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(x.shape[0])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((y * 32767).astype("<i2").tobytes())
    return dest


def separate_file(src: Path, work: Path, models_dir: Path, key: str = "kim_vocal_2",
                  progress: Callable[[float, str], None] = lambda f, m: None,
                  is_cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Sépare tout le son d'un média par blocs de 60 s (mémoire bornée)."""
    sep = MDXSeparator(models_dir, key)
    info = ffmpeg.probe(src)
    total = float(info.duration_s or 0)
    if info.audio_tracks < 1:
        raise SeparationError("Ce média ne contient pas de son.")
    work.mkdir(parents=True, exist_ok=True)
    block = 60.0
    voice_f = wave.open(str(work / "voice.wav"), "wb")
    music_f = wave.open(str(work / "music.wav"), "wb")
    for w in (voice_f, music_f):
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
    try:
        t, nblocks = 0.0, max(1, int(np.ceil(total / block)))
        k = 0
        while True:
            mix = _read_mix(src, t, block)
            if mix.shape[1] == 0:
                break
            v, r = sep.separate(mix, lambda f, m: progress((k + f) / nblocks, m), is_cancelled)
            voice_f.writeframes((np.clip(v.T, -1, 1) * 32767).astype("<i2").tobytes())
            music_f.writeframes((np.clip(r.T, -1, 1) * 32767).astype("<i2").tobytes())
            k += 1
            t += block
            if mix.shape[1] < int(block * RATE) - 10:
                break
    finally:
        voice_f.close()
        music_f.close()
    return {"voice": str(work / "voice.wav"), "music": str(work / "music.wav"), "model": key, "duration": total}
