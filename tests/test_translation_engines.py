"""Moteurs de traduction ajoutés (Google gratuit, MyMemory, Microsoft, LibreTranslate), automatique avec reprise
par le moteur suivant, doublage sans coupure, mise à jour de la base (onglet Textes), emojis, timecode."""
import json
import sqlite3
import threading
import urllib.parse
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import numpy as np
import pytest

from studiovideo.ai import translate as trm
from studiovideo.transcribe.model import TSegment, Transcript


class Handler(BaseHTTPRequestHandler):
    calls = []
    deepl_quota = False

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        self.send_response(code)
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        Handler.calls.append(("GET", u.path, q))
        if u.path == "/translate_a/t":                                   # Google gratuit
            out = [f"{q['tl'][0]}:{t}" for t in q["q"]]
            self._send([out[0], q["sl"][0]] if len(out) == 1 else out)
        elif u.path == "/get":                                           # MyMemory
            if q["q"][0] == "quota":
                self._send({"responseStatus": 429, "quotaFinished": True, "responseData": {"translatedText": ""}})
            else:
                self._send({"responseStatus": 200, "responseData": {"translatedText": "mm:" + q["q"][0]}})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.calls.append(("POST", u.path, body))
        if u.path == "/translate" and "api-version" in u.query:          # Microsoft
            assert self.headers["Ocp-Apim-Subscription-Key"] == "mskey"
            assert self.headers["Ocp-Apim-Subscription-Region"] == "westeurope"
            self._send([{"translations": [{"text": "ms:" + x["Text"]}]} for x in body])
        elif u.path == "/translate":                                     # LibreTranslate
            self._send({"translatedText": ["lt:" + t for t in body["q"]]})
        elif u.path == "/v2/translate":                                  # DeepL
            if Handler.deepl_quota:
                self._send({"message": "Quota exceeded"}, 456)
            else:
                self._send({"translations": [{"text": "dl:" + t} for t in body["text"]]})


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Handler.calls.clear()
    Handler.deepl_quota = False
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_free_and_keyed_engines(server):
    texts = ["Bonjour à tous.", "Super rapide !"]
    assert trm.GoogleFreeTranslator(server).translate(texts, "fr", "en") == ["en:Bonjour à tous.", "en:Super rapide !"]
    assert trm.GoogleFreeTranslator(server).translate(["Seul."], None, "de") == ["de:Seul."]    # une seule phrase
    assert Handler.calls[-1][2]["sl"] == ["auto"]
    assert trm.MyMemoryTranslator(server).translate(texts, "fr", "en") == ["mm:Bonjour à tous.", "mm:Super rapide !"]
    with pytest.raises(trm.TranslationError, match="Quota quotidien"):
        trm.MyMemoryTranslator(server).translate(["quota"], "fr", "en")
    assert trm.MicrosoftTranslator("mskey", "westeurope", server).translate(texts, "fr", "en")[1] == "ms:Super rapide !"
    assert trm.LibreTranslator(server).translate(texts, "fr", "en")[0] == "lt:Bonjour à tous."
    with pytest.raises(trm.TranslationError):
        trm.MicrosoftTranslator("")
    with pytest.raises(trm.TranslationError):
        trm.LibreTranslator("")


def test_auto_uses_best_engine_then_falls_back(server):
    """Automatique : DeepL d'abord ; crédit épuisé, le suivant reprend là où il s'était arrêté, et l'écart est dit."""
    dl, gf = trm.DeepLTranslator("k:fx", server), trm.GoogleFreeTranslator(server)
    auto = trm.FallbackTranslator([dl, gf])
    assert auto.translate(["Un."], "fr", "en") == ["dl:Un."] and auto.used == ["deepl"]
    Handler.deepl_quota = True
    assert auto.translate(["Deux."], "fr", "en") == ["en:Deux."]
    assert auto.used == ["deepl", "google_free"] and "DeepL" in auto.failures[0]
    tr = Transcript([TSegment(0, 1, "Trois.", []), TSegment(1, 2, "Quatre.", [])], "fr", "t")
    out = trm.translate_transcript(tr, auto, "en", batch=1)
    assert [s.text for s in out.segments] == ["en:Trois.", "en:Quatre."] and out.engine.endswith("google_free")
    with pytest.raises(trm.TranslationError, match="Aucun moteur"):
        trm.FallbackTranslator([trm.DeepLTranslator("k:fx", server)]).translate(["x"], "fr", "en")


def test_auto_order_and_settings():
    keys = {"deepl_api_key": "k"}
    get = {"llm": {"kind": "openai", "model": "gpt"}, "libretranslate": {}}.get
    cfg = trm.settings_cfg(lambda k, d=None: get(k, d))
    assert cfg["engine"] == "auto"
    t = trm.make_translator(cfg, keys.get)
    assert [e.name.split(":")[0] for e in t.engines] == ["deepl", "llm", "google_free", "mymemory"]
    t = trm.make_translator(trm.settings_cfg(lambda k, d=None: None), {}.get)      # rien de réglé : gratuits
    assert [e.name for e in t.engines] == ["google_free", "mymemory"]
    assert trm.configured("microsoft", cfg, {"azure_translator_key": "x"}.get)
    assert not trm.configured("libre", cfg, keys.get)
    with pytest.raises(trm.TranslationError, match="inconnu"):
        trm.make_translator({"engine": "rien"}, keys.get)


def test_quota_message_is_clear():
    from studiovideo.ai.highlights import http_error_text
    body = json.dumps({"error": {"message": "You have no credits remaining.", "code": "credit_balance_exhausted"}})
    assert "Plus de crédit" in http_error_text(429, body.encode(), "gpt")
    assert "trop de demandes" in http_error_text(429, b'{"error": {"message": "Rate limit"}}', "gpt")


class LongVoice:
    """Voix factice : 0,12 s par caractère (une voix lente, comme Piper)."""
    name = "fake"

    def synth(self, text, voice, language, dest, speed=1.0):
        n = int(len(text) * 0.12 * 24000)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(dest), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes((0.3 * np.sin(np.arange(n) / 10) * 32767).astype("<i2").tobytes())
        return dest


def test_dub_overflow_is_shifted_never_cut(tmp_path):
    """Phrases trop longues : accélérées au plus, puis décalées ; aucune coupée, aucune ne chevauche la suivante,
    la piste se recale dans les silences et tient dans la vidéo."""
    from studiovideo.edit import dubbing
    from studiovideo.ai.tts import wav_duration
    tr = Transcript([TSegment(0.5, 1.0, "Une phrase bien trop longue.", []),       # 3,4 s pour 0,5 s
                     TSegment(1.2, 1.6, "Encore une phrase.", []),
                     TSegment(4.0, 4.5, "Court.", []),
                     TSegment(9.0, 10.0, "Après un silence.", [])], "fr", "t")
    man = dubbing.build(tr, LongVoice(), {}, "v", 1, 12.0, tmp_path)
    a, b, c, d = man.lines
    assert a.tempo == dubbing.MAX_TEMPO and b.placed_start >= a.placed_end      # décalée, pas coupée
    assert b.tempo == dubbing.CATCH_UP_TEMPO                                     # en retard : rattrape un peu
    assert c.placed_start >= b.placed_end and c.tempo == 1.0                     # le silence suffit
    assert d.placed_start == pytest.approx(9.0)                                  # recalée sur l'original
    for ln in man.lines:
        assert wav_duration(tmp_path / ln.file) == pytest.approx(ln.raw_duration / ln.tempo, abs=0.05)
    assert d.placed_end <= 12.0


def test_migration_texts_mode_keeps_everything(tmp_path, monkeypatch):
    """0007 reconstruit la table des projets : mêmes projets, médias rattachés, clés étrangères intactes."""
    from studiovideo.db import database
    all_migs = database.available_migrations()
    monkeypatch.setattr(database, "available_migrations", lambda: all_migs[:6])
    db = database.Database(tmp_path / "s.db", backups_dir=tmp_path / "bk")
    db.migrate()
    con = db.connect()
    con.execute("INSERT INTO workspace(id, name) VALUES (1, 'W')")
    con.execute("INSERT INTO concept(workspace_id, name) VALUES (1, 'C')")
    cid = con.execute("SELECT id FROM concept LIMIT 1").fetchone()
    con.execute("INSERT INTO project(concept_id, name, mode, data_json) VALUES (?, 'P', 'translation', '{\"a\": 1}')",
                (cid[0],))
    pid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint) "
                "VALUES (1, ?, 'video', 'v.mp4', 'v.mp4', 'f')", (pid,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE project SET mode = 'texts' WHERE id = ?", (pid,))
    monkeypatch.setattr(database, "available_migrations", lambda: all_migs[:7])
    assert db.migrate() == [7]
    assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    row = con.execute("SELECT name, mode, data_json FROM project WHERE id = ?", (pid,)).fetchone()
    assert tuple(row) == ("P", "translation", '{"a": 1}')
    assert con.execute("SELECT project_id FROM source_asset").fetchone()[0] == pid     # média toujours rattaché
    con.execute("UPDATE project SET mode = 'texts' WHERE id = ?", (pid,))
    con.execute("DELETE FROM project WHERE id = ?", (pid,))                             # ON DELETE SET NULL
    assert con.execute("SELECT project_id FROM source_asset").fetchone()[0] is None


def test_emoji_search_french_and_english():
    from studiovideo.ui.export_view import _norm, load_emojis
    groups, items = load_emojis()
    assert len(groups) == 9 and len(items) > 1500
    find = {e: k for e, _g, _n, k in items}
    assert "coeur" in find["❤️"] and "heart" in find["❤️"]            # français sans accent, anglais
    assert "feu" in find["🔥"] and "fire" in find["🔥"]
    assert _norm("Cœur Été") == "coeur ete"


def test_timecode_to_the_tenth():
    from studiovideo.media.ffmpeg import fmt_timecode
    assert fmt_timecode(83.46, True) == "1:23.5" and fmt_timecode(3725.04, True) == "1:02:05.0"
    assert fmt_timecode(59.97) == "0:59" and fmt_timecode(59.97, True) == "1:00.0"
