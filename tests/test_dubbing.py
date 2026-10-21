"""Doublage : moteur ElevenLabs (serveur simulé), ajustement des durées, assemblage, export réel doublé."""
import json
import subprocess
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

from studiovideo.ai.tts import ElevenLabsTTS, TTSError, wav_duration
from studiovideo.edit import dubbing
from studiovideo.edit.model import Timeline
from studiovideo.edit.render import export, verify
from studiovideo.media import ffmpeg
from studiovideo.transcribe.model import TSegment, Transcript

pytestmark = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")

VOICES = {"v_alice": 600.0, "v_bob": 1000.0}
SEC_PER_CHAR = 0.06


class Fake(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def _auth(self) -> bool:
        if self.headers.get("xi-api-key") != "good":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"detail":"invalid key"}')
            return False
        return True

    def do_GET(self):
        if not self._auth():
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"voices": [{"voice_id": k, "name": k[2:].title(), "category": "premade"}
                                                for k in VOICES]}).encode())

    def do_POST(self):
        if not self._auth():
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        voice = self.path.split("/v1/text-to-speech/")[1].split("?")[0]
        rate = int(self.path.split("output_format=pcm_")[1])
        Fake.calls.append((voice, body))
        dur = SEC_PER_CHAR * len(body["text"])
        t = np.arange(int(dur * rate)) / rate
        x = (0.4 * np.sin(2 * np.pi * VOICES[voice] * t) * 32767).astype("<i2")
        self.send_response(200)
        self.end_headers()
        self.wfile.write(x.tobytes())


@pytest.fixture
def server(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Fake.calls.clear()
    url = f"http://127.0.0.1:{srv.server_port}"
    monkeypatch.setenv("STUDIOVIDEO_ELEVENLABS_URL", url)
    yield url
    srv.shutdown()


def translated():
    # 2e phrase volontairement trop longue pour son créneau : elle doit être accélérée (≤ ×1,35)
    return Transcript([TSegment(0.5, 2.0, "Hello everyone.", [], "Alice"),            # 0.9 s dans 1.5 s
                       TSegment(2.5, 4.0, "We talk about video edits!", [], "Alice"),  # 1.56 s dans 1.5 s
                       TSegment(4.5, 6.5, "Let us go now, my friends.", [], "Bob")], "en", "test")


def test_engine_voices_and_errors(server):
    eng = ElevenLabsTTS("good", base_url=server)
    assert {v.id for v in eng.voices()} == set(VOICES)
    with pytest.raises(TTSError, match="refusée"):
        ElevenLabsTTS("bad", base_url=server).voices()
    with pytest.raises(TTSError):
        ElevenLabsTTS("")


def test_build_fits_slots_and_regenerates(server, tmp_path):
    eng = ElevenLabsTTS("good", base_url=server)
    man = dubbing.build(translated(), eng, {"Bob": "v_bob"}, "v_alice", 1, 7.0, tmp_path)
    assert [ln.voice for ln in man.lines] == ["v_alice", "v_alice", "v_bob"]
    assert Fake.calls[0][1]["model_id"] == "eleven_multilingual_v2"
    assert man.lines[0].tempo == 1.0 and 1.0 < man.lines[1].tempo <= dubbing.MAX_TEMPO
    for ln in man.lines:                      # aucune phrase ne chevauche la suivante
        assert ln.placed_end <= ln.end + 0.6
    track = tmp_path / man.track
    assert abs(wav_duration(track) - 8.0) < 0.05
    with wave.open(str(track)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(float)
    r = dubbing.RATE
    assert np.abs(x[int(0.1 * r):int(0.45 * r)]).max() < 10          # silence avant la 1re phrase
    assert np.abs(x[int(0.7 * r):int(1.2 * r)]).max() > 5000          # voix pendant la 1re phrase
    # persistance du manifeste puis régénération d'une seule phrase avec un autre texte
    man2 = dubbing.DubManifest.from_json(man.to_json())
    n = len(Fake.calls)
    dubbing.regenerate(man2, 2, "Go!", None, eng, tmp_path)
    assert len(Fake.calls) == n + 1 and man2.lines[2].text == "Go!" and man2.lines[2].raw_duration < 0.3


def _make(path, args):
    subprocess.run([ffmpeg.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *args, str(path)], check=True)
    return path


def _dominant(path, start, end):
    p = subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-ss", str(start), "-t", str(end - start), "-i", str(path),
                        "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True, check=True)
    x = np.frombuffer(p.stdout, "<i2").astype(float)
    spec = np.abs(np.fft.rfft(x * np.hanning(x.size)))
    return float(np.fft.rfftfreq(x.size, 1 / 16000)[int(np.argmax(spec))])


def _wait(app, jid, timeout=60):
    end = time.time() + timeout
    while app.jobs.get(jid).status in ("pending", "running") and time.time() < end:
        time.sleep(0.05)
    j = app.jobs.get(jid)
    assert j.status == "succeeded", j.error
    return j


def test_dub_job_and_dubbed_export(server, tmp_path, monkeypatch):
    from studiovideo.core import build_context
    from studiovideo.services import secrets
    monkeypatch.setattr(secrets, "get_secret", lambda name: "good" if name == "elevenlabs_api_key" else None)
    video = _make(tmp_path / "talk.mp4", ["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=7",
                                          "-f", "lavfi", "-i", "sine=frequency=300:duration=7",
                                          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"])
    app = build_context(tmp_path / "d")
    try:
        aid = app.importer().import_file(video).asset_id
        app.transcripts.store_original(aid, Transcript([TSegment(s.start, s.end, "x", [], s.speaker)
                                                        for s in translated().segments], "fr", "test"))
        app.transcripts.store_translation(aid, translated())
        jid = app.jobs.submit("dub", "Doublage EN", {"asset_id": aid, "language": "en", "engine": "elevenlabs",
                                                     "voices": {"Bob": "v_bob"}, "default_voice": "v_alice"})
        res = _wait(app, jid).result
        assert res["lines"] == 3 and res["errors"] == []
        assert app.dubs.languages(aid) == ["en"] and app.dubs.track(aid, "en").exists()
        # une phrase reprise
        _wait(app, app.jobs.submit("dub_line", "Reprise", {"asset_id": aid, "language": "en", "index": 0,
                                                           "text": "Hi all."}))
        assert app.dubs.get(aid, "en")[0].lines[0].text == "Hi all."

        tl = Timeline()
        tl.add_segment(aid, 0, 7)
        tl.audio.normalize = False
        tl.audio.highpass = False
        tl.voice.mode, tl.voice.language = "dub", "en"
        sources, music = app.montage.sources_for(tl)
        assert sources[aid].dub_path

        def run(cmd, on_line):
            p = subprocess.run(cmd, capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-1500:]
            return 0
        out = tmp_path / "dub_en.mp4"
        export(tl, sources, music, out, run)
        assert verify(out, tl)["audio_codec"]
        # voix d'origine remplacée : on entend la voix doublée (600 Hz) et plus le 300 Hz d'origine
        assert abs(_dominant(out, 0.6, 1.0) - 600) < 15
        assert abs(_dominant(out, 4.8, 5.8) - 1000) < 15
        # mode « voix d'origine en fond » : l'original revient, plus faible que le doublage
        tl.voice.original_level_db = -18
        export(tl, sources, music, tmp_path / "dub_bg.mp4", run)
        assert abs(_dominant(tmp_path / "dub_bg.mp4", 0.6, 1.0) - 600) < 15
        assert abs(_dominant(tmp_path / "dub_bg.mp4", 0.05, 0.4) - 300) < 15     # avant la 1re phrase
        # sans doublage enregistré dans la langue demandée : message clair
        tl.voice.language = "de"
        with pytest.raises(ValueError, match="doublage"):
            app.montage.sources_for(tl)
    finally:
        app.close()
