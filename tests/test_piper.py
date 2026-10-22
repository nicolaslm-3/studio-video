"""Voix hors ligne Piper : installation, catalogue, téléchargement vérifié, synthèse (simulée et réelle)."""
import hashlib
import io
import json
import os
import sys
import tarfile
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from studiovideo.ai import piper
from studiovideo.ai.tts import PiperTTS, TTSError

FAKE_EXE = """#!/usr/bin/env python3
import sys, wave, struct
args = sys.argv
out = args[args.index("-f") + 1]
text = sys.stdin.buffer.read().decode("utf-8")
n = int(22050 * 0.05 * max(1, len(text.split())))
with wave.open(out, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
    w.writeframes(struct.pack("<h", 1000) * n)
"""
VOICE = b"ONNX-FAKE" * 100
CONF = json.dumps({"audio": {"sample_rate": 22050}}).encode()


def _catalog():
    return {"fr_FR-siwis-medium": {
        "key": "fr_FR-siwis-medium", "name": "siwis", "quality": "medium", "num_speakers": 1,
        "language": {"code": "fr_FR", "family": "fr"},
        "files": {"fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx": {"size_bytes": len(VOICE),
                                                                     "md5_digest": hashlib.md5(VOICE).hexdigest()},
                  "fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx.json": {"size_bytes": len(CONF),
                                                                          "md5_digest": hashlib.md5(CONF).hexdigest()},
                  "fr/fr_FR/siwis/medium/MODEL_CARD": {"size_bytes": 10, "md5_digest": "x"}}},
        "en_US-bad-low": {"key": "en_US-bad-low", "name": "bad", "quality": "low", "language": {"code": "en_US",
                          "family": "en"}, "files": {"en/x.onnx": {"size_bytes": 3, "md5_digest": "0" * 32},
                                                      "en/x.onnx.json": {"size_bytes": 3, "md5_digest": "0" * 32}}}}


def _engine_tar():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        data = FAKE_EXE.encode()
        ti = tarfile.TarInfo("piper/piper")
        ti.size, ti.mode = len(data), 0o755
        t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path.lstrip("/")
        body = {"voices.json": json.dumps(_catalog()).encode(), "piper_linux_x86_64.tar.gz": _engine_tar(),
                "fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx": VOICE,
                "fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx.json": CONF,
                "en/x.onnx": b"bad", "en/x.onnx.json": b"bad"}.get(p)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="programme simulé pour Linux")
def test_install_catalog_download_and_synth(tmp_path, monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("STUDIOVIDEO_PIPER_TEST_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.delenv("STUDIOVIDEO_PIPER_EXE", raising=False)
    models = tmp_path / "models"
    try:
        with pytest.raises(TTSError, match="non installé"):
            PiperTTS(models)
        exe = piper.install_engine(models)
        assert exe.exists() and os.access(exe, os.X_OK)
        with pytest.raises(TTSError, match="Aucune voix"):
            PiperTTS(models)
        cat = piper.catalog(models, refresh=True)
        fr = next(v for v in cat if v.key == "fr_FR-siwis-medium")
        assert fr.family == "fr" and len(fr.files) == 2                  # la fiche n'est pas téléchargée
        piper.download_voice(models, fr)
        assert piper.installed_voices(models) == ["fr_FR-siwis-medium"]
        bad = next(v for v in cat if v.key == "en_US-bad-low")
        with pytest.raises(piper.PiperError, match="corrompu"):
            piper.download_voice(models, bad)
        assert "en_US-bad-low" not in piper.installed_voices(models)
        eng = PiperTTS(models)
        assert [v.id for v in eng.voices()] == ["fr_FR-siwis-medium"]
        out = eng.synth("Bonjour à tous", "fr_FR-siwis-medium", "fr", tmp_path / "o.wav")
        with wave.open(str(out)) as w:
            assert w.getframerate() == 22050 and w.getnframes() > 0
        piper.delete_voice(models, "fr_FR-siwis-medium")
        assert piper.installed_voices(models) == []
    finally:
        srv.shutdown()


REAL_EXE, REAL_VOICE = os.environ.get("STUDIOVIDEO_PIPER_EXE", ""), os.environ.get("STUDIOVIDEO_PIPER_VOICE", "")


@pytest.mark.skipif(not (REAL_EXE and REAL_VOICE and Path(REAL_VOICE).exists()),
                    reason="Piper réel non fourni (STUDIOVIDEO_PIPER_EXE, STUDIOVIDEO_PIPER_VOICE)")
def test_real_piper_voice(tmp_path):
    """Synthèse réelle ; si un modèle Whisper est fourni, la phrase est retranscrite pour vérification."""
    import shutil
    models = tmp_path / "models"
    (models / "piper").mkdir(parents=True)
    key = Path(REAL_VOICE).name[:-5]
    shutil.copy(REAL_VOICE, models / "piper" / f"{key}.onnx")
    shutil.copy(REAL_VOICE + ".json", models / "piper" / f"{key}.onnx.json")
    out = PiperTTS(models).synth("Bonjour et bienvenue dans le studio vidéo.", key, "fr", tmp_path / "real.wav")
    with wave.open(str(out)) as w:
        dur = w.getnframes() / w.getframerate()
    assert 1.5 < dur < 8
    wm = os.environ.get("STUDIOVIDEO_TEST_MODELS")
    if wm:
        from studiovideo.transcribe import engines
        eng = engines.FasterWhisperEngine(Path(wm), "tiny")
        tr = engines.transcribe_file(eng, out, "fr", lambda f, m: None, lambda: False)
        text = tr.text().lower()
        print(json.dumps({"piper_transcription": text}))
        assert "bienvenue" in text or "studio" in text
