"""Traduction : glossaire, moteurs DeepL / Google / LLM (serveurs simulés), stockage, sous-titres traduits."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from studiovideo.ai.translate import (DeepLTranslator, GoogleTranslator, LLMTranslator, TranslationError, protect,
                                      restore, translate_transcript)
from studiovideo.edit.model import Timeline
from studiovideo.edit.subtitles import generate
from studiovideo.transcribe.model import TSegment, Transcript, Word

FAKE = {"Bonjour Poussey Pass": "Hello [[0]]", "On parle de montage vidéo.": "We talk about video editing.",
        "Bonjour [[0]]": "Hello [[0]]", "C'est parti.": "Let's go."}


def tr():
    return Transcript([TSegment(0.5, 2.0, "Bonjour Poussey Pass", [Word(0.5, 1.0, " Bonjour")], "Alice"),
                       TSegment(2.5, 5.0, "On parle de montage vidéo.", [], "Alice"),
                       TSegment(5.5, 7.0, "C'est parti.", [], "Bob")], "fr", "test")


class Handler(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def _send(self, obj):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.calls.append((self.path, dict(self.headers), body))
        if self.path.startswith("/v2/translate"):                       # DeepL
            assert self.headers["Authorization"].startswith("DeepL-Auth-Key ")
            self._send({"translations": [{"text": FAKE.get(t, t.upper())} for t in body["text"]]})
        elif self.path.startswith("/language/translate/v2"):            # Google
            self._send({"data": {"translations": [{"translatedText": FAKE.get(t, t)} for t in body["q"]]}})
        elif self.path == "/api/chat":                                   # Ollama
            src = json.loads(body["messages"][0]["content"].split("\n\n", 1)[1])
            self._send({"message": {"content": json.dumps({k: FAKE.get(v, v) for k, v in src.items()})}})


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Handler.calls.clear()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_glossary_protect_restore():
    t, m = protect("Merci à Poussey Pass et poussey pass !", ["Poussey Pass"])
    assert "Poussey" not in t and restore(t.replace("Merci à", "Thanks to"), m).count("Poussey Pass") == 2


@pytest.mark.parametrize("engine", ["deepl", "google", "llm"])
def test_engines_translate_with_glossary(server, engine):
    if engine == "deepl":
        t = DeepLTranslator("cle:fx", server)
    elif engine == "google":
        t = GoogleTranslator("cle", server)
    else:
        t = LLMTranslator("ollama", "llama3.1:8b", server)
    out = translate_transcript(tr(), t, "en", ["Poussey Pass"], batch=2)
    assert out.language == "en" and [s.text for s in out.segments] == [
        "Hello Poussey Pass", "We talk about video editing.", "Let's go."]
    assert out.segments[2].speaker == "Bob" and out.segments[1].start == 2.5
    assert all(s.words for s in out.segments)
    assert len(Handler.calls) == 2                        # 3 phrases par lots de 2


def test_same_language_refused():
    with pytest.raises(TranslationError):
        translate_transcript(tr(), GoogleTranslator("k", "http://127.0.0.1:9"), "fr")


def test_missing_key():
    with pytest.raises(TranslationError):
        DeepLTranslator("")


def test_storage_and_translated_subtitles(tmp_path, server):
    from studiovideo.core import build_context
    app = build_context(tmp_path / "d")
    try:
        app.db.connect().execute("INSERT INTO source_asset(workspace_id, kind, path, file_name, fingerprint, "
                                 "audio_tracks) VALUES (1,'video','x','x.mp4','f',1)")
        app.transcripts.store_original(1, tr())
        app.settings.set("llm", {"kind": "ollama", "model": "m", "base_url": server})
        jid = app.jobs.submit("translate", "Traduction", {"asset_id": 1, "language": "en", "engine": {"engine": "llm"},
                                                          "glossary": ["Poussey Pass"]})
        end = time.time() + 20
        while app.jobs.get(jid).status in ("pending", "running") and time.time() < end:
            time.sleep(0.05)
        assert app.jobs.get(jid).status == "succeeded", app.jobs.get(jid).error
        assert app.transcripts.languages(1) == ["en"]
        en = app.transcripts.translation(1, "en")
        en.edit_segment(2, "Here we go.")
        app.transcripts.save_translation_edit(1, en)
        assert app.transcripts.for_language(1, "en").segments[2].text == "Here we go."
        assert app.transcripts.for_language(1, None).segments[2].text == "C'est parti."
        tl = Timeline()
        tl.add_segment(1, 0, 7)
        cues = generate(tl, {1: app.transcripts.for_language(1, "en")})
        assert any("Hello" in c.text for c in cues) and any("Here we go." in c.text for c in cues)
    finally:
        app.close()
