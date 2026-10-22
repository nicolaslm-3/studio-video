"""X (V3) : connexion PKCE, envoi de vidéo par blocs, traitement, publication, statistiques (serveur simulé)."""
import base64
import hashlib
import json
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from studiovideo.publish import xapi


class X(BaseHTTPRequestHandler):
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
            X.st["challenge"] = q["code_challenge"]
            assert "media.write" in q["scope"] and "offline.access" in q["scope"]
            self.send_response(302)
            self.send_header("Location", f"{q['redirect_uri']}?code=CODE&state={q['state']}")
            self.end_headers()
        elif u.path == "/2/users/me":
            self._json({"data": {"id": "42", "username": "moi"}})
        elif u.path == "/2/media/upload":
            X.st["polls"] = X.st.get("polls", 0) + 1
            state = "succeeded" if X.st["polls"] > 1 else "in_progress"
            self._json({"data": {"processing_info": {"state": state, "check_after_secs": 0}}})
        elif u.path == "/2/tweets":
            self._json({"data": [{"id": i, "public_metrics": {"impression_count": 1000, "like_count": 10,
                                                              "reply_count": 2, "retweet_count": 3, "quote_count": 1}}
                                 for i in q["ids"].split(",")]})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        u = urllib.parse.urlparse(self.path)
        if u.path == "/2/oauth2/token":
            f = dict(urllib.parse.parse_qsl(raw.decode()))
            if f["grant_type"] == "authorization_code":
                v = base64.urlsafe_b64encode(hashlib.sha256(f["code_verifier"].encode()).digest()).rstrip(b"=")
                assert v.decode() == X.st["challenge"]
                assert self.headers["Authorization"].startswith("Basic ")
                return self._json({"access_token": "AT", "refresh_token": "RT1"})
            return self._json({"access_token": "AT2", "refresh_token": "RT2"})
        assert self.headers["Authorization"].startswith("Bearer AT")
        if u.path == "/2/media/upload/initialize":
            b = json.loads(raw)
            assert b["media_category"] == "tweet_video"
            X.st["total"], X.st["parts"] = b["total_bytes"], []
            return self._json({"data": {"id": "M1"}})
        if u.path == "/2/media/upload/M1/append":
            assert b'name="segment_index"' in raw and b'name="media"' in raw
            X.st["parts"].append(len(raw))
            return self._json({})
        if u.path == "/2/media/upload/M1/finalize":
            return self._json({"data": {"processing_info": {"state": "pending", "check_after_secs": 0}}})
        if u.path == "/2/tweets":
            X.st["tweet"] = json.loads(raw)
            return self._json({"data": {"id": "1789"}})


@pytest.fixture
def server(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), X)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    X.st = {}
    monkeypatch.setenv("STUDIOVIDEO_X_TEST_URL", f"http://127.0.0.1:{srv.server_port}")
    yield
    srv.shutdown()


def _browser(url):
    threading.Thread(target=lambda: urllib.request.urlopen(url, timeout=10).read(), daemon=True).start()


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_connect_post_and_stats(server, tmp_path):
    tok = xapi.connect("cid", "csec", _browser, timeout=10, port=_free_port())
    assert tok["refresh_token"] == "RT1"
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\3" * (xapi.CHUNK + 1000))
    res = xapi.post_video(video, "Sketch #humour", "AT")
    assert res == {"id": "1789"} and len(X.st["parts"]) == 2
    assert X.st["tweet"] == {"text": "Sketch #humour", "media": {"media_ids": ["M1"]}}
    assert xapi.post_stats("AT", ["1789"]) == {"1789": {"views": 1000, "likes": 10, "comments": 2, "shares": 4}}


def test_job_publish_x(server, tmp_path, monkeypatch):
    from studiovideo.core import build_context
    from studiovideo.domain.models import Platform
    from studiovideo.services import secrets
    store = {"x_client_secret": "csec"}
    monkeypatch.setattr(secrets, "get_secret", lambda k: store.get(k))
    monkeypatch.setattr(secrets, "set_secret", lambda k, v: store.__setitem__(k, v))
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", _browser)
    port = _free_port()
    monkeypatch.setattr(xapi.connect, "__defaults__", (300, None, port, xapi.connect.__defaults__[3]))
    app = build_context(tmp_path / "d")

    def wait(jid):
        end = time.time() + 30
        while app.jobs.get(jid).status in ("pending", "running") and time.time() < end:
            time.sleep(0.05)
        return app.jobs.get(jid)
    try:
        app.settings.set("x_client_id", "cid")
        c = app.concepts.create_concept("PK")
        acc = app.concepts.create_account(Platform.X, "PK")
        app.concepts.link(c.id, acc.id)
        j = wait(app.jobs.submit("connect_account", "Connexion", {"account_id": acc.id}))
        assert j.status == "succeeded", j.error
        assert app.concepts.get_account(acc.id).public_url == "https://x.com/moi"
        video = tmp_path / "v.mp4"
        video.write_bytes(b"\0" * 1000)
        con = app.db.connect()
        con.execute("INSERT INTO project(concept_id, name) VALUES (?, 'P')", (c.id,))
        con.execute("INSERT INTO clip(project_id, name, start_s, end_s) VALUES (1, 'Montage', 0, 1)")
        con.execute("INSERT INTO video_variant(clip_id, label, aspect, output_path) VALUES (1,'v','9:16',?)",
                    (str(video),))
        m = app.publications.create_master(c.id, "Nouvelle carte", "", "#pokemon", variant_id=1)
        (pid,) = app.publications.set_destinations(m, [acc.id])
        j = wait(app.jobs.submit("publish_upload", "X", {"publication_id": pid, "now": True}))
        assert j.status == "succeeded", j.error
        r = app.publications.get(pid)
        assert r["status"] == "published" and r["public_url"] == "https://x.com/moi/status/1789"
        assert store[f"x_refresh_{acc.id}"] == "RT2"
        j = wait(app.jobs.submit("refresh_stats", "Stats", {}))
        assert j.status == "succeeded" and j.result["updated"] == 1, (j.error, j.result)
        m2 = app.publications.create_master(c.id, "x" * 300, "", "", variant_id=1)
        (p2,) = app.publications.set_destinations(m2, [acc.id])
        j = wait(app.jobs.submit("publish_upload", "X", {"publication_id": p2, "now": True}))
        assert j.status == "failed" and "trop long" in j.error and app.publications.get(p2)["status"] == "failed"
    finally:
        app.close()
