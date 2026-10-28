"""Instagram / Facebook (V3) : connexion, choix de la Page / du compte, Reels, statistiques (serveur simulé)."""
import json
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from studiovideo.publish import meta


class M(BaseHTTPRequestHandler):
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
        if u.path == "/dialog":
            assert q["redirect_uri"] == M.st["redirect"] and "instagram_content_publish" in q["scope"]
            self.send_response(302)
            self.send_header("Location", f"{q['redirect_uri']}?code=CODE&state={q['state']}")
            self.end_headers()
        elif u.path == "/graph/oauth/access_token":
            if q.get("grant_type") == "fb_exchange_token":
                return self._json({"access_token": "LONG", "expires_in": 5184000})
            assert q["code"] == "CODE" and q["client_secret"] == "sec"
            return self._json({"access_token": "SHORT"})
        elif u.path == "/graph/me/accounts":
            assert q["access_token"] == "LONG"
            return self._json({"data": [
                {"id": "P1", "name": "Autre page", "access_token": "PT1"},
                {"id": "P2", "name": "Poussey Pass", "access_token": "PT2",
                 "instagram_business_account": {"id": "IG2", "username": "pousseypass"}}]})
        elif u.path == "/graph/C1":
            M.st["polls"] = M.st.get("polls", 0) + 1
            return self._json({"status_code": "FINISHED" if M.st["polls"] > 1 else "IN_PROGRESS"})
        elif u.path == "/graph/MEDIA9":
            return self._json({"permalink": "https://www.instagram.com/reel/abc/"} if q["fields"] == "permalink"
                              else {"like_count": 12, "comments_count": 3})
        elif u.path == "/graph/MEDIA9/insights":
            return self._json({"data": [{"name": "views", "values": [{"value": 456}]}]})
        else:
            self._json({"error": {"code": 100, "message": f"inconnu {u.path}"}}, 400)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/graph/IG2/media":
            assert q["media_type"] == "REELS" and q["upload_type"] == "resumable" and q["access_token"] == "PT2"
            M.st["caption"] = q["caption"]
            return self._json({"id": "C1", "uri": f"http://127.0.0.1:{self.server.server_port}/rupload/ig/C1"})
        if u.path.startswith("/rupload/"):
            assert self.headers["Authorization"].startswith("OAuth PT") and self.headers["offset"] == "0"
            assert int(self.headers["file_size"]) == len(raw)
            M.st.setdefault("uploads", []).append(raw)
            return self._json({"success": True, "message": "Upload successful."})
        if u.path == "/graph/IG2/media_publish":
            assert q["creation_id"] == "C1"
            return self._json({"id": "MEDIA9"})
        if u.path == "/graph/P2/video_reels":
            if q["upload_phase"] == "start":
                return self._json({"video_id": "V7", "upload_url":
                                   f"http://127.0.0.1:{self.server.server_port}/rupload/fb/V7"})
            assert q["upload_phase"] == "finish" and q["video_state"] in ("PUBLISHED", "DRAFT")
            M.st["description"] = q["description"]
            M.st["state"] = q["video_state"]
            return self._json({"success": True})
        self._json({"error": {"code": 190, "message": "token"}}, 400)


@pytest.fixture
def server(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), M)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    M.st = {}
    monkeypatch.setenv("STUDIOVIDEO_META_TEST_URL", f"http://127.0.0.1:{srv.server_port}")
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


def test_connect_pick_and_reels(server, tmp_path):
    port = _free_port()
    M.st["redirect"] = meta.redirect_uri(port)
    res = meta.connect("app", "sec", _browser, timeout=10, port=port)
    assert res["user_token"] == "LONG" and len(res["pages"]) == 2
    ig = meta.pick(res["pages"], "instagram", "PousseyPass")
    assert ig["ig_id"] == "IG2" and ig["token"] == "PT2"
    assert meta.pick(res["pages"], "facebook", "Poussey Pass")["id"] == "P2"
    assert meta.pick(res["pages"], "facebook", "Introuvable")["id"] == "P1"
    with pytest.raises(meta.MetaError, match="Instagram professionnel"):
        meta.pick([res["pages"][0]], "instagram", "x")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\2" * 50_000)
    r = meta.instagram_reel(video, "Sketch #humour", "IG2", "PT2", poll_s=0.01)
    assert r == {"id": "MEDIA9", "url": "https://www.instagram.com/reel/abc/"} and M.st["uploads"][0] == video.read_bytes()
    r = meta.facebook_reel(video, "Sketch", "P2", "PT2")
    assert r["url"] == "https://www.facebook.com/reel/V7" and M.st["description"] == "Sketch"
    assert M.st["state"] == "PUBLISHED"
    meta.facebook_reel(video, "Brouillon", "P2", "PT2", draft=True)       # brouillon de la Page
    assert M.st["state"] == "DRAFT"
    assert meta.instagram_stats(["MEDIA9"], "PT2") == {"MEDIA9": {"likes": 12, "comments": 3, "views": 456}}


def test_port_busy(server):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        with pytest.raises(meta.MetaError, match="occupé"):
            meta.connect("app", "sec", _browser, timeout=1, port=s.getsockname()[1])
    finally:
        s.close()


def test_jobs(server, tmp_path, monkeypatch):
    from studiovideo.core import build_context
    from studiovideo.domain.models import Platform
    from studiovideo.services import secrets
    store = {"meta_app_secret": "sec"}
    monkeypatch.setattr(secrets, "get_secret", lambda k: store.get(k))
    monkeypatch.setattr(secrets, "set_secret", lambda k, v: store.__setitem__(k, v))
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", _browser)
    port = _free_port()
    monkeypatch.setattr(meta, "PORT", port)
    monkeypatch.setattr(meta.connect, "__defaults__", (300, None, port, meta.connect.__defaults__[3]))
    M.st["redirect"] = meta.redirect_uri(port)
    app = build_context(tmp_path / "d")

    def wait(jid):
        end = time.time() + 30
        while app.jobs.get(jid).status in ("pending", "running") and time.time() < end:
            time.sleep(0.05)
        return app.jobs.get(jid)
    try:
        app.settings.set("meta_app_id", "app")
        c = app.concepts.create_concept("PP")
        ig = app.concepts.create_account(Platform.INSTAGRAM, "pousseypass")
        fb = app.concepts.create_account(Platform.FACEBOOK, "Poussey Pass")
        for a in (ig, fb):
            app.concepts.link(c.id, a.id)
            j = wait(app.jobs.submit("connect_account", "Connexion", {"account_id": a.id}))
            assert j.status == "succeeded", j.error
        assert app.concepts.get_account(ig.id).public_url == "https://www.instagram.com/pousseypass/"
        assert store[f"meta_token_{fb.id}"] == "PT2"
        video = tmp_path / "v.mp4"
        video.write_bytes(b"\0" * 10_000)
        con = app.db.connect()
        con.execute("INSERT INTO project(concept_id, name) VALUES (?, 'P')", (c.id,))
        con.execute("INSERT INTO clip(project_id, name, start_s, end_s) VALUES (1, 'Montage', 0, 1)")
        con.execute("INSERT INTO video_variant(clip_id, label, aspect, output_path) VALUES (1,'v','9:16',?)",
                    (str(video),))
        m = app.publications.create_master(c.id, "Sketch", "Texte", "#humour", variant_id=1)
        p_ig, p_fb = app.publications.set_destinations(m, [ig.id, fb.id])
        monkeypatch.setattr(meta, "instagram_reel", lambda *a, **k: _orig_reel(*a[:5], poll_s=0.01))
        for pid in (p_ig, p_fb):
            j = wait(app.jobs.submit("publish_upload", "Meta", {"publication_id": pid, "now": True}))
            assert j.status == "succeeded", j.error
        assert app.publications.get(p_ig)["public_url"] == "https://www.instagram.com/reel/abc/"
        assert app.publications.get(p_fb)["status"] == "published"
        assert M.st["caption"] == "Sketch\n\nTexte\n\n#humour"
        m2 = app.publications.create_master(c.id, "Brouillon", "", "", variant_id=1)
        d_ig, d_fb = app.publications.set_destinations(m2, [ig.id, fb.id])
        j = wait(app.jobs.submit("publish_upload", "Meta", {"publication_id": d_fb, "now": True, "draft": True}))
        assert j.status == "succeeded" and M.st["state"] == "DRAFT", j.error
        assert app.publications.get(d_fb)["status"] == "manual_action"           # brouillon, pas « publiée »
        j = wait(app.jobs.submit("publish_upload", "Meta", {"publication_id": d_ig, "now": True, "draft": True}))
        assert j.status == "failed" and "brouillon" in j.error                    # Instagram : pas de brouillon
        assert app.publications.get(d_ig)["status"] == "failed"
        j = wait(app.jobs.submit("refresh_stats", "Stats", {}))
        assert j.status == "succeeded" and j.result["updated"] == 1, (j.error, j.result)
    finally:
        app.close()


_orig_reel = meta.instagram_reel
