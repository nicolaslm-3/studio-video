"""ElevenLabs : appels communs (clé « sk_… » du coffre Windows), transcription Scribe v2 et doublage automatique.

- Transcription (Scribe v2) : le son est envoyé, le texte revient mot à mot avec ses horodatages.
- Doublage : le son de la vidéo est envoyé ; ElevenLabs transcrit, traduit et refait la voix (voix d'origine
  reproduite, fond sonore gardé), puis renvoie la piste doublée. Le logiciel n'y ajoute rien.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

SCRIBE_MODEL = "scribe_v2"
# codes ISO 639-3 renvoyés par ElevenLabs → codes à deux lettres du logiciel
ISO3 = {"eng": "en", "fra": "fr", "deu": "de", "spa": "es", "por": "pt", "ita": "it", "jpn": "ja", "rus": "ru",
        "kor": "ko", "ara": "ar", "tur": "tr", "pol": "pl", "nld": "nl", "ind": "id", "hin": "hi", "zho": "zh",
        "cmn": "zh", "ukr": "uk", "ron": "ro", "swe": "sv", "ces": "cs", "ell": "el", "vie": "vi"}


class ElevenLabsError(RuntimeError):
    pass


def base_url() -> str:
    return (os.environ.get("STUDIOVIDEO_ELEVENLABS_URL", "") or "https://api.elevenlabs.io").rstrip("/")


def short_lang(code: str | None) -> str:
    code = (code or "").lower()
    return ISO3.get(code, code)


def call(key: str, path: str, data: bytes | None = None, content_type: str = "application/json",
         timeout: float = 120, method: str | None = None, base: str = "") -> bytes:
    """Requête à l'API ; les erreurs courantes sont expliquées en clair."""
    if not key:
        raise ElevenLabsError("Clé API ElevenLabs absente (roue crantée : elle commence par « sk_ »).")
    req = urllib.request.Request((base.rstrip("/") or base_url()) + path, data=data, method=method,
                                 headers={"xi-api-key": key, "Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:400].decode("utf-8", "replace")
        if "api_key_id_used_as_api_key" in detail:
            raise ElevenLabsError("Vous avez collé l'identifiant de la clé ElevenLabs, pas la clé elle-même : la clé "
                                  "commence par « sk_ » et n'est affichée qu'à sa création (ElevenLabs → Developers → "
                                  "API Keys → Create). Collez-la dans Réglages.") from exc
        if exc.code == 401 or "invalid_api_key" in detail:
            raise ElevenLabsError("Clé ElevenLabs refusée : vérifiez-la dans Réglages (elle commence par « sk_ »).") \
                from exc
        if "missing_permissions" in detail:
            raise ElevenLabsError("La clé ElevenLabs n'a pas le droit d'utiliser cet outil : sur elevenlabs.io → "
                                  "Developers → API Keys, autorisez-le pour cette clé.") from exc
        if "quota_exceeded" in detail or "credits" in detail.lower():
            raise ElevenLabsError("Plus de crédit ElevenLabs ce mois-ci : attendez le renouvellement ou passez à "
                                  "un forfait supérieur.") from exc
        if exc.code == 429:
            raise ElevenLabsError("Quota ElevenLabs atteint ou trop de requêtes.") from exc
        raise ElevenLabsError(f"ElevenLabs a répondu {exc.code} : {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ElevenLabsError(f"ElevenLabs injoignable : {exc}") from exc


def multipart(fields: dict[str, str], file_path: Path, file_field: str = "file") -> tuple[bytes, str]:
    """Corps « multipart/form-data » : des champs texte et un fichier."""
    b = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    mime = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}.get(file_path.suffix.lower(),
                                                                                  "application/octet-stream")
    parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{file_path.name}\"\r\n"
                 f"Content-Type: {mime}\r\n\r\n".encode() + file_path.read_bytes() + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={b}"


def speech_to_text(key: str, audio: Path, language: str | None = None, model: str = SCRIBE_MODEL) -> dict:
    fields = {"model_id": model, "timestamps_granularity": "word", "tag_audio_events": "false", "diarize": "true"}
    if language:
        fields["language_code"] = language
    body, ctype = multipart(fields, audio)
    return json.loads(call(key, "/v1/speech-to-text", body, ctype, timeout=1800).decode())


def dub(key: str, audio: Path, target: str, source: str | None, progress: Callable[[float, str], None],
        is_cancelled: Callable[[], bool], poll: float = 5.0) -> bytes:
    """Doublage automatique : envoi, attente (ElevenLabs annonce sa durée), puis la piste doublée (MP3)."""
    fields = {"target_lang": target, "num_speakers": "0", "name": f"Studio Vidéo · {audio.stem} · {target}"}
    if source:
        fields["source_lang"] = source
    body, ctype = multipart(fields, audio)
    progress(0.05, "envoi du son à ElevenLabs")
    d = json.loads(call(key, "/v1/dubbing", body, ctype, timeout=1800).decode())
    did = d["dubbing_id"]
    expected = float(d.get("expected_duration_sec") or 60.0)
    t0 = time.monotonic()
    while True:
        if is_cancelled():
            raise InterruptedError("annulé")
        st = json.loads(call(key, f"/v1/dubbing/{urllib.parse.quote(did)}", timeout=60).decode())
        status = (st.get("status") or "").lower()
        if status == "dubbed":
            break
        if status == "failed" or st.get("error"):
            raise ElevenLabsError(f"Doublage ElevenLabs en échec : {st.get('error') or 'raison non donnée'}")
        el = time.monotonic() - t0
        progress(min(0.9, 0.1 + 0.8 * el / max(expected, 1.0)), f"doublage chez ElevenLabs ({int(el)} s)")
        time.sleep(poll)
    progress(0.92, "récupération de la voix doublée")
    return call(key, f"/v1/dubbing/{urllib.parse.quote(did)}/audio/{urllib.parse.quote(target)}", timeout=600)
