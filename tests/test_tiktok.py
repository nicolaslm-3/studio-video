"""TikTok (V3) : connexion PKCE hexadécimale, envoi par blocs, suivi, cas privé, rotation du jeton (simulé)."""
import hashlib
import json
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from studiovideo.publish import tiktok


class T(BaseHTTPRequestHandler):
    st: dict = {}

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/auth":
            assert q["code_challenge_method"] == "S256" and "video.publish" in q["scope"]
            assert q["redirect_uri"].startswith("http://127.0.0.1:") and q["redirect_uri"].endswith("/callback/")
            T.st["challenge"] = q["code_challenge"]
            self.send_response(302)
            self.send_header("Location", f"{q['redirect_uri']}?code=CODE&scopes=video.publish&state={q['state']}")
            self.end_headers()
        elif u.path == "/v2/user/info/":
            self._json({"data": {"user": {"open_id": "OPEN", "display_name": "Moi"}}, "error": {"code": "ok"}})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        u = urllib.parse.urlparse(self.path)
        if u.path == "/v2/oauth/token/":
            f = dict(urllib.parse.parse_qsl(raw.decode()))
            if f["grant_type"] == "authorization_code":
                assert hashlib.sha256(f["code_verifier"].encode()).hexdigest() == T.st["challenge"]
                return self._json({"access_token": "A1", "refresh_token": "R1", "open_id": "OPEN",
                                   "scope": "video.publish", "expires_in": 86400})
            if f.get("refresh_token") in ("R1", "R2"):
                return self._json({"access_token": "A2", "refresh_token": "R2", "open_id": "OPEN"})
            return self._json({"error": "invalid_grant", "error_description": "expired"}, 400)
        assert self.headers["Authorization"].startswith("Bearer A")
        if u.path == "/v2/post/publish/creator_info/query/":
            opts = T.st.get("options", ["PUBLIC_TO_EVERYONE", "SELF_ONLY"])
            return self._json({"data": {"creator_username": "moi", "privacy_level_options": opts,
                                        "max_video_post_duration_sec": 600}, "error": {"code": "ok"}})
        if u.path == "/v2/post/publish/video/init/":
            body = json.loads(raw)
            T.st["init"] = body
            T.st["data"] = b""
            T.st["polls"] = 0
            return self._json({"data": {"publish_id": "PUB1",
                                        "upload_url": f"http://127.0.0.1:{self.server.server_port}/up?x=1"},
                               "error": {"code": "ok"}})
        if u.path == "/v2/post/publish/status/fetch/":
            T.st["polls"] += 1
            if T.st["polls"] < 2:
                return self._json({"data": {"status": "PROCESSING_UPLOAD"}, "error": {"code": "ok"}})
            ids = [] if T.st["init"]["post_info"]["privacy_level"] == "SELF_ONLY" else [7380000000000000001]
            return self._json({"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": ids},
                               "error": {"code": "ok"}})
        if u.path == "/v2/video/query/":
            ids = json.loads(raw)["filters"]["video_ids"]
            return self._json({"data": {"videos": [{"id": i, "view_count": 900, "like_count": 50,
                                                    "comment_count": 4, "share_count": 2} for i in ids]},
                               "error": {"code": "ok"}})

    def do_PUT(self):
        n = int(self.headers["Content-Length"])
        data = self.rfile.read(n)
        start = int(self.headers["Content-Range"].split()[1].split("-")[0])
        assert start == len(T.st["data"])
        T.st["data"] += data
        total = int(self.headers["Content-Range"].split("/")[1])
        self.send_response(201 if len(T.st["data"]) >= total else 206)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture
def server(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), T)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    T.st = {}
    monkeypatch.setenv("STUDIOVIDEO_TIKTOK_TEST_URL", f"http://127.0.0.1:{srv.server_port}")
    yield
    srv.shutdown()


def _browser(url):
    threading.Thread(target=lambda: urllib.request.urlopen(url, timeout=10).read(), daemon=True).start()


def test_chunk_plan():
    MB = tiktok.MB
    assert tiktok.chunk_plan(3 * MB) == (3 * MB, 1)
    assert tiktok.chunk_plan(64 * MB) == (64 * MB, 1)
    chunk, n = tiktok.chunk_plan(150 * MB + 123)
    assert chunk == 10 * MB and n == 15 and 150 * MB + 123 - (n - 1) * chunk < 128 * MB


def test_connect_and_publish(server, tmp_path):
    tok = tiktok.connect("key", "secret", _browser, timeout=10)
    assert tok["refresh_token"] == "R1"
    assert tiktok.refresh("key", "secret", "R1")["refresh_token"] == "R2"     # rotation
    with pytest.raises(tiktok.TikTokError, match="révoquée"):
        tiktok.refresh("key", "secret", "OLD")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\1" * 300_000)
    res = tiktok.upload(video, "Mon sketch #humour", "PUBLIC_TO_EVERYONE", "A2", poll_s=0.01)
    assert res["post_ids"] == ["7380000000000000001"] and T.st["data"] == video.read_bytes()
    assert T.st["init"]["source_info"] == {"source": "FILE_UPLOAD", "video_size": 300_000, "chunk_size": 300_000,
                                           "total_chunk_count": 1}
    T.st["options"] = ["SELF_ONLY"]                       # application non auditée
    res = tiktok.upload(video, "x", "PUBLIC_TO_EVERYONE", "A2", poll_s=0.01)
    assert res["privacy"] == "SELF_ONLY" and res["post_ids"] == []
    assert tiktok.video_stats("A2", ["1", "2"])["2"] == {"views": 900, "likes": 50, "comments": 4, "shares": 2}


def test_jobs(server, tmp_path, monkeypatch):
    from studiovideo.core import build_context
    from studiovideo.domain.models import Platform
    from studiovideo.services import secrets
    store = {"tiktok_client_secret": "secret"}
    monkeypatch.setattr(secrets, "get_secret", lambda k: store.get(k))
    monkeypatch.setattr(secrets, "set_secret", lambda k, v: store.__setitem__(k, v))
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", _browser)
    monkeypatch.setattr(tiktok, "upload", lambda *a, **k: _orig_upload(*a, **dict(k, poll_s=0.01)))
    app = build_context(tmp_path / "d")

    def wait(jid):
        end = time.time() + 30
        while app.jobs.get(jid).status in ("pending", "running") and time.time() < end:
            time.sleep(0.05)
        return app.jobs.get(jid)
    try:
        app.settings.set("tiktok_client_key", "key")
        c = app.concepts.create_concept("PP")
        acc = app.concepts.create_account(Platform.TIKTOK, "PP")
        app.concepts.link(c.id, acc.id)
        j = wait(app.jobs.submit("connect_account", "Connexion", {"account_id": acc.id}))
        assert j.status == "succeeded", j.error
        a = app.concepts.get_account(acc.id)
        assert a.connection_status == "connected" and a.public_url == "https://www.tiktok.com/@moi"
        video = tmp_path / "v.mp4"
        video.write_bytes(b"\0" * 100_000)
        con = app.db.connect()
        con.execute("INSERT INTO project(concept_id, name) VALUES (?, 'P')", (c.id,))
        con.execute("INSERT INTO clip(project_id, name, start_s, end_s) VALUES (1, 'Montage', 0, 1)")
        con.execute("INSERT INTO video_variant(clip_id, label, aspect, output_path) VALUES (1,'v','9:16',?)",
                    (str(video),))
        m = app.publications.create_master(c.id, "Sketch", "Texte", "#humour", variant_id=1)
        pid = app.publications.set_destinations(m, [acc.id])[0]
        app.publications.schedule(pid, "2099-01-01T10:00")
        j = wait(app.jobs.submit("publish_upload", "TikTok", {"publication_id": pid}))
        assert j.status == "failed" and "programmation" in j.error
        assert app.publications.get(pid)["status"] == "scheduled_local"          # rien n'est parti
        j = wait(app.jobs.submit("publish_upload", "TikTok", {"publication_id": pid, "now": True}))
        assert j.status == "succeeded", j.error
        r = app.publications.get(pid)
        assert r["status"] == "published" and r["public_url"] == "https://www.tiktok.com/@moi/video/7380000000000000001"
        assert store[f"tiktok_refresh_{acc.id}"] == "R2"                          # jeton renouvelé enregistré
        assert T.st["init"]["post_info"]["title"] == "Sketch\n\nTexte\n\n#humour"
        m2 = app.publications.create_master(c.id, "Privé", "", "", variant_id=1)
        p2 = app.publications.set_destinations(m2, [acc.id])[0]
        T.st["options"] = ["SELF_ONLY"]
        j = wait(app.jobs.submit("publish_upload", "TikTok", {"publication_id": p2, "now": True}))
        assert j.status == "succeeded" and app.publications.get(p2)["status"] == "manual_action"
        assert "auditée" in app.publications.get(p2)["last_error"]
    finally:
        app.close()


_orig_upload = tiktok.upload


def test_tiktok_stats_job(server, tmp_path, monkeypatch):
    from studiovideo.core import build_context
    from studiovideo.domain.models import Platform
    from studiovideo.services import secrets
    store = {"tiktok_client_secret": "s"}
    monkeypatch.setattr(secrets, "get_secret", lambda k: store.get(k))
    monkeypatch.setattr(secrets, "set_secret", lambda k, v: store.__setitem__(k, v))
    app = build_context(tmp_path / "d")
    try:
        c = app.concepts.create_concept("PP")
        acc = app.concepts.create_account(Platform.TIKTOK, "PP")
        app.concepts.link(c.id, acc.id)
        m = app.publications.create_master(c.id, "A")
        (pid,) = app.publications.set_destinations(m, [acc.id])
        app.publications.confirm_published(pid, "https://www.tiktok.com/@moi/video/7380000000000000009")
        jid = app.jobs.submit("refresh_stats", "Stats", {})
        end = time.time() + 20
        while app.jobs.get(jid).status in ("pending", "running") and time.time() < end:
            time.sleep(0.05)
        j = app.jobs.get(jid)
        assert j.status == "succeeded" and "non connecté" in j.result["errors"][0]
        store[f"tiktok_refresh_{acc.id}"] = "R1"
        with app.db.transaction() as con:
            con.execute("UPDATE social_account SET connection_status='connected' WHERE id=?", (acc.id,))
        jid = app.jobs.submit("refresh_stats", "Stats", {})
        end = time.time() + 20
        while app.jobs.get(jid).status in ("pending", "running") and time.time() < end:
            time.sleep(0.05)
        j = app.jobs.get(jid)
        assert j.status == "succeeded" and j.result["updated"] == 1, (j.error, j.result)
        assert app.analytics.latest()[0]["metrics"]["shares"] == 2
    finally:
        app.close()
