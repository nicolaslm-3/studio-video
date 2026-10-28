"""ElevenLabs (serveur simulé) : transcription Scribe v2 mise en phrases, choix du moteur de transcription,
doublage automatique à partir du son de la vidéo, extraits d'écoute des voix."""
import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from studiovideo.ai import tts
from studiovideo.media import ffmpeg
from studiovideo.transcribe import engines

pytestmark = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")

WORDS = [
    {"text": "Bonjour", "type": "word", "start": 0.1, "end": 0.5, "speaker_id": "s0", "logprob": -0.1},
    {"text": " ", "type": "spacing", "start": 0.5, "end": 0.6},
    {"text": "à", "type": "word", "start": 0.6, "end": 0.7, "speaker_id": "s0"},
    {"text": " ", "type": "spacing", "start": 0.7, "end": 0.75},
    {"text": "tous.", "type": "word", "start": 0.75, "end": 1.1, "speaker_id": "s0"},
    {"text": "(rires)", "type": "audio_event", "start": 1.1, "end": 1.5},
    {"text": "Merci", "type": "word", "start": 1.6, "end": 1.9, "speaker_id": "s1"},
    {"text": " ", "type": "spacing", "start": 1.9, "end": 2.0},
    {"text": "bien", "type": "word", "start": 2.0, "end": 2.3, "speaker_id": "s1"},
    {"text": "Ensuite", "type": "word", "start": 4.0, "end": 4.4, "speaker_id": "s1"},
]


class Fake(BaseHTTPRequestHandler):
    calls: list = []
    dubbed = b""

    def log_message(self, *a):
        pass

    def _json(self, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        Fake.calls.append((self.path, body[:4000]))
        if self.headers.get("xi-api-key") != "sk_ok":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"detail": {"status": "invalid_api_key"}}')
            return
        if self.path == "/v1/speech-to-text":
            self._json({"language_code": "fra", "text": "Bonjour à tous.", "words": WORDS})
        elif self.path == "/v1/dubbing":
            self._json({"dubbing_id": "d1", "expected_duration_sec": 1})

    def do_GET(self):  # noqa: N802
        Fake.calls.append((self.path, b""))
        if self.path == "/v1/dubbing/d1":
            self._json({"status": "dubbed", "target_languages": ["en"]})
        elif self.path == "/v1/dubbing/d1/audio/en":
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.end_headers()
            self.wfile.write(Fake.dubbed)
        elif self.path == "/v1/dubbing/d1/transcripts/en/format/srt":
            self._json({"transcript_format": "srt",
                        "srt": "1\n00:00:00,200 --> 00:00:01,400\nHello everyone.\n\n"
                               "2\n00:00:01,500 --> 00:00:02,800\nThanks\nfor watching.\n"})
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def server(monkeypatch, tmp_path):
    mp3 = tmp_path / "dub.mp3"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=500:d=3", str(mp3)],
                   check=True)
    Fake.dubbed = mp3.read_bytes()
    Fake.calls.clear()
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("STUDIOVIDEO_ELEVENLABS_URL", f"http://127.0.0.1:{srv.server_port}")
    yield
    srv.shutdown()


def _video(path):
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x120:d=3:r=25",
                    "-f", "lavfi", "-i", "sine=f=300:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(path)], check=True)
    return path


def _wait(app, jid, timeout=60):
    end = time.time() + timeout
    while app.jobs.get(jid).status in ("pending", "running") and time.time() < end:
        time.sleep(0.05)
    j = app.jobs.get(jid)
    assert j.status == "succeeded", j.error
    return j


def test_words_to_segments():
    segs = engines.words_to_segments(WORDS)
    assert [s.text for s in segs] == ["Bonjour à tous.", "Merci bien", "Ensuite"]   # point, intervenant, blanc
    assert segs[0].speaker == "s0" and segs[1].speaker == "s1"
    assert segs[0].start == 0.1 and segs[0].end == 1.1 and len(segs[0].words) == 3
    assert segs[0].words[1].text == " à" and 0.9 < segs[0].words[0].prob < 1.0


def test_scribe_job_with_chosen_engine(server, tmp_path, monkeypatch):
    from studiovideo.core import asr_params, build_context
    from studiovideo.services import secrets
    keys = {"elevenlabs_api_key": None}
    monkeypatch.setattr(secrets, "get_secret", lambda n: keys.get(n))
    app = build_context(tmp_path / "d")
    try:
        app.settings.set("transcribe_engine", "elevenlabs")
        assert "clé ElevenLabs absente" in asr_params(app)                 # pas de faux départ
        keys["elevenlabs_api_key"] = "sk_ok"
        assert asr_params(app) == {"engine": "elevenlabs"}
        aid = app.importer().import_file(_video(tmp_path / "v.mp4")).asset_id
        _wait(app, app.jobs.submit("transcribe", "T", {"asset_id": aid, **asr_params(app)}))
        tr = app.transcripts.get(aid)
        assert tr.engine == "elevenlabs:scribe_v2" and tr.language == "fr" and len(tr.segments) == 3
        sent = next(b for p, b in Fake.calls if p == "/v1/speech-to-text")
        assert b'name="model_id"\r\n\r\nscribe_v2' in sent and b"audio/mpeg" in sent    # son compressé
        # transcription corrigée : c'est elle que tout le logiciel reprend
        tr.edit_segment(0, "Bonsoir à tous.")
        app.transcripts.save_edit(aid, tr)
        assert app.transcripts.get(aid).segments[0].text == "Bonsoir à tous."
        assert app.transcripts.original(aid).segments[0].text == "Bonjour à tous."
    finally:
        app.close()


def test_eleven_dubbing_job(server, tmp_path, monkeypatch):
    from studiovideo.core import build_context
    from studiovideo.services import secrets
    monkeypatch.setattr(secrets, "get_secret", lambda n: "sk_ok" if n == "elevenlabs_api_key" else None)
    app = build_context(tmp_path / "d")
    try:
        aid = app.importer().import_file(_video(tmp_path / "v.mp4")).asset_id
        res = _wait(app, app.jobs.submit("eleven_dub", "D", {"asset_id": aid, "language": "en",
                                                             "source": "fr"})).result
        assert res["language"] == "en"
        track = app.dubs.track(aid, "en")
        assert track.exists() and abs(tts.wav_duration(track) - 3.0) < 0.2       # la piste rendue par ElevenLabs
        assert app.dubs.get(aid, "en")[0].engine == "elevenlabs-dubbing"
        sent = next(b for p, b in Fake.calls if p == "/v1/dubbing")
        assert b'name="target_lang"\r\n\r\nen' in sent and b'name="source_lang"\r\n\r\nfr' in sent
        # ses sous-titres deviennent la traduction anglaise (sous-titres et texte de la voix)
        assert res["subtitles"] == 2
        tr = app.transcripts.translation(aid, "en")
        assert [s.text for s in tr.segments] == ["Hello everyone.", "Thanks for watching."]
        assert tr.segments[1].start == 1.5 and tr.segments[1].words
    finally:
        app.close()


def test_translate_with_elevenlabs_engine(server, tmp_path, monkeypatch):
    """Moteur de traduction « ElevenLabs » : le texte traduit vient du doublage, la voix n'est pas gardée."""
    from studiovideo.ai import translate as trm
    from studiovideo.core import build_context
    from studiovideo.services import secrets
    from studiovideo.transcribe.model import TSegment, Transcript
    monkeypatch.setattr(secrets, "get_secret", lambda n: "sk_ok" if n == "elevenlabs_api_key" else None)
    assert "elevenlabs" in trm.CHOICES and "elevenlabs" not in trm.AUTO_ORDER     # jamais en automatique
    assert trm.configured("elevenlabs", {}, secrets.get_secret)
    app = build_context(tmp_path / "d")
    try:
        aid = app.importer().import_file(_video(tmp_path / "v.mp4")).asset_id
        app.transcripts.store_original(aid, Transcript([TSegment(0, 2, "Bonjour à tous.")], "fr", "test"))
        res = _wait(app, app.jobs.submit("translate", "T", {"asset_id": aid, "language": "en",
                                                           "engine": "elevenlabs"})).result
        assert res["engines"] == ["elevenlabs"] and res["segments"] == 2
        assert app.transcripts.translation(aid, "en").segments[0].text == "Hello everyone."
        assert app.dubs.get(aid, "en") is None                # traduction seule : pas de voix
    finally:
        app.close()


def test_voice_previews_are_published_samples():
    assert tts.piper_preview("fr_FR-siwis-medium") == \
        "https://rhasspy.github.io/piper-samples/samples/fr/fr_FR/siwis/medium/speaker_0.mp3"
    assert tts.OpenAITTS("k").voices()[0].preview.startswith("https://cdn.openai.com/")
