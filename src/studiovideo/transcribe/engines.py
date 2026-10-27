"""Moteurs de transcription interchangeables (§6, §23).

Tout moteur expose `transcribe(audio_path, language, progress, is_cancelled) -> Transcript`.
Le moteur local par défaut est faster-whisper (CPU, int8) ; ses modèles sont
téléchargés après installation dans %LOCALAPPDATA%\\StudioVideo\\models.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from ..media import ffmpeg
from .model import TSegment, Transcript, Word

log = logging.getLogger(__name__)
# Téléchargement HTTPS classique des modèles : le protocole Xet de Hugging Face passe par un serveur
# (cas-server.xethub.hf.co) souvent bloqué par les pare-feux d'entreprise (« CAS Client Error »).
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
_NOWIN =subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

Progress = Callable[[float, str], None]


class Cancelled(Exception):
    pass


class EngineUnavailable(RuntimeError):
    pass


class TranscriptionEngine(Protocol):
    name: str

    def transcribe(self, audio: Path, language: str | None, progress: Progress,
                   is_cancelled: Callable[[], bool]) -> Transcript: ...


@dataclass(frozen=True)
class ModelInfo:
    key: str
    label: str
    size_mb: int
    min_ram_gb: float
    note: str


MODELS = [
    ModelInfo("tiny", "Très rapide (tiny)", 75, 2, "Qualité limitée, pour tester."),
    ModelInfo("base", "Rapide (base)", 145, 3, "Correct sur une voix claire."),
    ModelInfo("small", "Équilibré (small)", 485, 4, "Bon compromis sur la plupart des PC."),
    ModelInfo("medium", "Précis (medium)", 1530, 8, "Plus lent, meilleur sur les accents et le bruit."),
    ModelInfo("large-v3-turbo", "Très précis (large-v3-turbo)", 1620, 8, "Le meilleur, lent sans carte graphique."),
]
MODEL_KEYS = {m.key for m in MODELS}
REPOS = {k: f"Systran/faster-whisper-{k}" for k in ("tiny", "base", "small", "medium")}
REPOS["large-v3-turbo"] = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"


def recommended_model(ram_gb: float | None, cpu_count: int | None) -> str:
    if not ram_gb:
        return "small"
    if ram_gb >= 16 and (cpu_count or 0) >= 8:
        return "medium"
    if ram_gb >= 6:
        return "small"
    return "base"


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def model_dir(models_root: Path, key: str) -> Path:
    return models_root / "whisper" / key


def is_installed(models_root: Path, key: str) -> bool:
    d = model_dir(models_root, key)
    return (d / "model.bin").exists() and (d / "config.json").exists()


def installed_models(models_root: Path) -> list[str]:
    return [m.key for m in MODELS if is_installed(models_root, m.key)]


def delete_model(models_root: Path, key: str) -> None:
    shutil.rmtree(model_dir(models_root, key), ignore_errors=True)


def download_model(models_root: Path, key: str, progress: Progress, is_cancelled: Callable[[], bool]) -> Path:
    """Télécharge un modèle depuis Hugging Face (source officielle des modèles faster-whisper)."""
    if key not in MODEL_KEYS:
        raise ValueError(f"Modèle inconnu : {key}")
    try:
        from huggingface_hub import HfApi, hf_hub_download
    except ImportError as exc:
        raise EngineUnavailable("Le module de téléchargement des modèles est absent.") from exc
    repo = REPOS[key]
    dest = model_dir(models_root, key)
    tmp = dest.with_name(dest.name + ".partial")
    tmp.mkdir(parents=True, exist_ok=True)
    wanted = {"config.json", "model.bin", "tokenizer.json", "vocabulary.txt", "vocabulary.json",
              "preprocessor_config.json"}
    try:
        info = HfApi().model_info(repo, files_metadata=True)
    except Exception as exc:
        raise RuntimeError(f"Impossible de joindre le serveur des modèles : {exc}") from exc
    files = [(s.rfilename, s.size or 0) for s in info.siblings if s.rfilename in wanted]
    total = sum(sz for _, sz in files) or 1
    done = 0
    for name, size in sorted(files, key=lambda x: x[1]):
        if is_cancelled():
            raise Cancelled()
        progress(done / total, f"Téléchargement de {name}")
        hf_hub_download(repo, name, local_dir=str(tmp))
        done += size
    if not (tmp / "model.bin").exists():
        raise RuntimeError("Téléchargement incomplet : model.bin absent.")
    if dest.exists():
        shutil.rmtree(dest)
    tmp.rename(dest)
    progress(1.0, "Modèle installé")
    return dest


def extract_audio(src: Path, dest: Path, start: float | None = None, duration: float | None = None) -> Path:
    """Audio mono 16 kHz pour la reconnaissance vocale."""
    cmd = [ffmpeg.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dest)]
    p = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NOWIN)
    if p.returncode != 0 or not dest.exists():
        raise RuntimeError(f"Extraction audio impossible : {p.stderr[-400:]}")
    return dest


def load_wav(path: Path):
    """Lit un WAV PCM 16 bits mono 16 kHz (produit par extract_audio) en tableau float32.

    faster-whisper reçoit directement les échantillons : on évite ainsi son décodeur
    PyAV, dont l'interface change d'une version à l'autre.
    """
    import wave

    import numpy as np
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError("Format audio inattendu (PCM 16 bits mono requis).")
        if w.getframerate() != 16000:
            raise ValueError("Fréquence d'échantillonnage inattendue (16 kHz requis).")
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


class FasterWhisperEngine:
    name = "faster-whisper"

    def __init__(self, models_root: Path, model: str = "small", threads: int = 0):
        if not available():
            raise EngineUnavailable("Le moteur de transcription locale n'est pas installé.")
        if not is_installed(models_root, model):
            raise EngineUnavailable(f"Le modèle « {model} » n'est pas téléchargé (Paramètres → Matériel et moteurs).")
        self.models_root, self.model, self.threads = models_root, model, threads or max(1, (os.cpu_count() or 4) - 1)
        self.name = f"faster-whisper:{model}"

    def transcribe(self, audio: Path, language: str | None, progress: Progress,
                   is_cancelled: Callable[[], bool]) -> Transcript:
        from faster_whisper import WhisperModel
        progress(0.02, "Chargement du modèle")
        m = WhisperModel(str(model_dir(self.models_root, self.model)), device="cpu", compute_type="int8",
                         cpu_threads=self.threads, local_files_only=True)
        samples = load_wav(audio) if str(audio).lower().endswith(".wav") else str(audio)
        segments, info = m.transcribe(samples, language=language or None, word_timestamps=True,
                                      vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
                                      beam_size=5, condition_on_previous_text=False)
        total = info.duration or 1.0
        out = []
        for s in segments:  # générateur : la transcription avance au fil de la lecture
            if is_cancelled():
                raise Cancelled()
            words = [Word(float(w.start), float(w.end), w.word, float(w.probability)) for w in (s.words or [])]
            out.append(TSegment(float(s.start), float(s.end), s.text.strip(), words))
            progress(min(0.99, 0.05 + 0.94 * s.end / total), f"Transcription : {int(s.end)} / {int(total)} s")
        return Transcript(out, info.language, self.name)


def transcribe_file(engine: TranscriptionEngine, src: Path, language: str | None, progress: Progress,
                    is_cancelled: Callable[[], bool], tmp_root: Path | None = None) -> Transcript:
    """Transcription d'un média de n'importe quelle durée (le moteur découpe en interne)."""
    with tempfile.TemporaryDirectory(prefix="sv-asr-", dir=str(tmp_root) if tmp_root else None) as d:
        progress(0.01, "Extraction du son")
        wav = extract_audio(src, Path(d) / "audio.wav")
        if is_cancelled():
            raise Cancelled()
        return engine.transcribe(wav, language, progress, is_cancelled)
