"""Synthèse vocale (cahier des charges §11) : moteurs interchangeables.

- ElevenLabs (clé API personnelle) : voix réalistes, multilingues (une même voix parle toutes les langues).
- OpenAI (clé du modèle de langage) : voix naturelles, multilingues, payant à l'usage.
- Piper (local, gratuit) : voix hors ligne, une voix par langue, si le moteur et la voix sont installés.
Chaque moteur écrit un fichier WAV mono pour une phrase.
"""

from __future__ import annotations

import json
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

# Moteurs de voix : (nom, qualité, coût, ce qu'il faut, adresse)
ENGINES = {
    "elevenlabs": ("ElevenLabs", "La plus réaliste ; une même voix parle toutes les langues",
                   "Forfait gratuit d'environ 10 minutes de voix par mois, puis abonnement",
                   "Une clé API ElevenLabs (elle commence par « sk_ »)",
                   "https://elevenlabs.io/app/settings/api-keys"),
    "openai": ("OpenAI", "Très naturelle, multilingue (11 voix)", "Payant à l'usage (environ 1,5 centime par minute)",
               "La clé OpenAI de Réglages → Modèle de langage, avec du crédit",
               "https://platform.openai.com/settings/organization/billing"),
    "piper": ("Piper (hors ligne)", "Correcte, un peu robotique", "Gratuit, sur ce PC, sans Internet",
              "Une voix téléchargée dans la langue de la traduction (Réglages → Voix)", ""),
}
OPENAI_VOICES = ("alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse")


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
            detail = exc.read()[:400].decode("utf-8", "replace")
            if "api_key_id_used_as_api_key" in detail:
                raise TTSError("Vous avez collé l'identifiant de la clé ElevenLabs, pas la clé elle-même : la clé "
                               "commence par « sk_ » et n'est affichée qu'à sa création (ElevenLabs → Developers → "
                               "API Keys → Create). Collez-la dans Réglages.") from exc
            if exc.code == 401 or "invalid_api_key" in detail:
                raise TTSError("Clé ElevenLabs refusée : vérifiez-la dans Réglages (elle commence par « sk_ »).") \
                    from exc
            if "quota_exceeded" in detail or "credits" in detail.lower():
                raise TTSError("Plus de crédit ElevenLabs ce mois-ci : attendez le renouvellement, passez à un "
                               "forfait supérieur ou choisissez une autre voix.") from exc
            if exc.code == 429:
                raise TTSError("Quota ElevenLabs atteint ou trop de requêtes.") from exc
            raise TTSError(f"ElevenLabs a répondu {exc.code} : {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise TTSError(f"ElevenLabs injoignable : {exc}") from exc

    def voices(self) -> list[Voice]:
        data = json.loads(self._req("/v1/voices", timeout=30).decode())
        return [Voice(v["voice_id"], v.get("name", v["voice_id"]), v.get("category", ""))
                for v in data.get("voices", [])]

    def credits(self) -> tuple[int, int] | None:
        """(caractères utilisés, caractères du forfait) ce mois-ci ; None si la clé n'a pas ce droit."""
        try:
            d = json.loads(self._req("/v1/user/subscription", timeout=20).decode())
        except TTSError as exc:
            if "sk_" in str(exc) or "refusée" in str(exc):
                raise
            return None
        return int(d.get("character_count") or 0), int(d.get("character_limit") or 0)

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


class OpenAITTS:
    """Voix OpenAI (même clé que le modèle de langage OpenAI)."""
    name = "openai"

    def __init__(self, api_key: str, base_url: str = "", model: str = "gpt-4o-mini-tts"):
        if not api_key:
            raise TTSError("Clé OpenAI absente (Réglages → Modèle de langage, type « API compatible OpenAI »).")
        self.key, self.model = api_key, model
        self.url = (base_url or "https://api.openai.com").rstrip("/")

    def voices(self) -> list[Voice]:
        return [Voice(v, v.capitalize(), "multilingue") for v in OPENAI_VOICES]

    def synth(self, text: str, voice: str, language: str, dest: Path, speed: float = 1.0) -> Path:
        body = {"model": self.model, "voice": voice, "input": text, "response_format": "pcm",   # 24 kHz, 16 bits
                "speed": max(0.5, min(2.0, speed))}
        req = urllib.request.Request(self.url + "/v1/audio/speech", data=json.dumps(body).encode(), headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
        except urllib.error.HTTPError as exc:
            from .highlights import http_error_text
            raise TTSError("Voix OpenAI : " + http_error_text(exc.code, exc.read(), self.model)) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise TTSError(f"OpenAI injoignable : {exc}") from exc
        if len(data) < 200:
            raise TTSError("Audio vide reçu d'OpenAI.")
        return write_wav(dest, data[: len(data) // 2 * 2], 24000)


class PiperTTS:
    """Piper (hors ligne, gratuit) : programme autonome + voix .onnx téléchargées (Paramètres → Modèles)."""
    name = "piper"

    def __init__(self, models_dir: Path):
        from . import piper
        self.models_dir = Path(models_dir)
        self.exe = piper.exe_path(self.models_dir)
        if self.exe is None:
            raise TTSError("Moteur de voix hors ligne non installé (Paramètres → Matériel et moteurs → Voix).")
        if not piper.installed_voices(self.models_dir):
            raise TTSError("Aucune voix hors ligne téléchargée (Paramètres → Matériel et moteurs → Voix).")

    def voices(self) -> list[Voice]:
        from . import piper
        out = []
        for key in piper.installed_voices(self.models_dir):
            lang = key.split("-")[0]
            out.append(Voice(key, key.replace("_", " ").replace("-", " · "), "hors ligne", lang.split("_")[0]))
        return out

    def synth(self, text: str, voice: str, language: str, dest: Path, speed: float = 1.0) -> Path:
        from . import piper
        dest.parent.mkdir(parents=True, exist_ok=True)
        model = piper.voice_path(self.models_dir, voice)
        if not model.exists():
            raise TTSError(f"Voix hors ligne absente : {voice}")
        p = subprocess.run([str(self.exe), "-q", "-m", str(model), "-f", str(dest),
                            "--length_scale", f"{1.0 / max(0.5, min(2.0, speed)):.3f}"],
                           input=text.replace("\n", " ").encode("utf-8"), capture_output=True,
                           cwd=str(self.exe.parent), creationflags=_NOWIN, timeout=300)
        if p.returncode != 0 or not dest.exists() or dest.stat().st_size < 100:
            raise TTSError(f"Piper a échoué : {p.stderr[-300:].decode('utf-8', 'replace')}")
        return dest


def make_engine(kind: str, secret, models_dir: Path, llm: dict | None = None) -> TTSEngine:
    if kind == "elevenlabs":
        import os
        return ElevenLabsTTS(secret("elevenlabs_api_key") or "",
                             base_url=os.environ.get("STUDIOVIDEO_ELEVENLABS_URL", ""))
    if kind == "openai":
        llm = llm or {}
        return OpenAITTS(secret("llm_api_key") or "", llm.get("base_url", "") if llm.get("kind") == "openai" else "")
    if kind == "piper":
        return PiperTTS(models_dir)
    raise TTSError(f"Moteur de voix inconnu : {kind}")
