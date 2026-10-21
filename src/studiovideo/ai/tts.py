"""Synthèse vocale (cahier des charges §11) : moteurs interchangeables.

- ElevenLabs (clé API personnelle) : voix réalistes, multilingues.
- Piper (local, facultatif) : voix hors ligne, si le module et une voix sont installés.
Chaque moteur écrit un fichier WAV mono pour une phrase.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_NOWIN = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class TTSError(RuntimeError):
    pass


@dataclass
class Voice:
    id: str
    name: str
    category: str = ""
    language: str = ""


class TTSEngine(Protocol):
    name: str

    def voices(self) -> list[Voice]: ...

    def synth(self, text: str, voice: str, language: str, dest: Path, speed: float = 1.0) -> Path: ...


def write_wav(dest: Path, pcm: bytes, rate: int) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return dest


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


class ElevenLabsTTS:
    name = "elevenlabs"
    RATE = 24000

    def __init__(self, api_key: str, model: str = "eleven_multilingual_v2", base_url: str = ""):
        if not api_key:
            raise TTSError("Clé API ElevenLabs absente (Paramètres → Matériel et moteurs).")
        self.key, self.model = api_key, model
        self.url = (base_url or "https://api.elevenlabs.io").rstrip("/")

    def _req(self, path: str, body: dict | None = None, timeout: float = 120) -> bytes:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, headers={
            "xi-api-key": self.key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", "replace")
            if exc.code == 401:
                raise TTSError("Clé ElevenLabs refusée.") from exc
            if exc.code == 429:
                raise TTSError("Quota ElevenLabs atteint ou trop de requêtes.") from exc
            raise TTSError(f"ElevenLabs a répondu {exc.code} : {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise TTSError(f"ElevenLabs injoignable : {exc}") from exc

    def voices(self) -> list[Voice]:
        data = json.loads(self._req("/v1/voices", timeout=30).decode())
        return [Voice(v["voice_id"], v.get("name", v["voice_id"]), v.get("category", ""))
                for v in data.get("voices", [])]

    def synth(self, text: str, voice: str, language: str, dest: Path, speed: float = 1.0) -> Path:
        body = {"text": text, "model_id": self.model,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75,
                                   "speed": max(0.7, min(1.2, speed))}}
        if language and self.model in ("eleven_flash_v2_5", "eleven_turbo_v2_5"):
            body["language_code"] = language
        pcm = self._req(f"/v1/text-to-speech/{urllib.parse.quote(voice)}?output_format=pcm_{self.RATE}", body)
        if len(pcm) < 200:
            raise TTSError("Audio vide reçu d'ElevenLabs.")
        return write_wav(dest, pcm, self.RATE)


class PiperTTS:
    """Piper (hors ligne). Voix : fichiers .onnx (+ .onnx.json) dans le dossier des modèles."""
    name = "piper"

    def __init__(self, voices_dir: Path):
        self.dir = voices_dir
        self.exe = shutil.which("piper")
        try:
            import piper  # noqa: F401
            self.module = True
        except Exception:
            self.module = False
        if not (self.module or self.exe):
            raise TTSError("Piper n'est pas installé dans cette version.")

    def voices(self) -> list[Voice]:
        out = []
        for f in sorted(self.dir.glob("*.onnx")):
            lang = f.stem.split("-")[0].split("_")[0]
            out.append(Voice(str(f), f.stem, "local", lang))
        return out

    def synth(self, text: str, voice: str, language: str, dest: Path, speed: float = 1.0) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if self.module:
            from piper import PiperVoice  # type: ignore
            v = PiperVoice.load(voice)
            with wave.open(str(dest), "wb") as w:
                v.synthesize_wav(text, w)
            return dest
        p = subprocess.run([self.exe, "--model", voice, "--output_file", str(dest),
                            "--length_scale", f"{1.0 / max(0.5, speed):.3f}"], input=text, text=True,
                           capture_output=True, creationflags=_NOWIN)
        if p.returncode != 0 or not dest.exists():
            raise TTSError(f"Piper a échoué : {p.stderr[-300:]}")
        return dest


def make_engine(kind: str, secret, models_dir: Path) -> TTSEngine:
    if kind == "elevenlabs":
        import os
        return ElevenLabsTTS(secret("elevenlabs_api_key") or "",
                             base_url=os.environ.get("STUDIOVIDEO_ELEVENLABS_URL", ""))
    if kind == "piper":
        return PiperTTS(models_dir / "piper")
    raise TTSError(f"Moteur de voix inconnu : {kind}")
