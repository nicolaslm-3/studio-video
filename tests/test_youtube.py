"""YouTube (V3) : connexion OAuth avec PKCE, envoi reprenable, programmation, cas « resté privé » (serveur simulé)."""
import base64
import hashlib
import json
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from studiovideo.publish import youtube


class G(BaseHTTPRequestHandler):
    state: dict = {}

    def log_message(self, *a):
        pass

    def _json(self, code, obj, headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        body = json.dumps(obj).encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/auth":                       # consentement accepté : renvoi vers l'appli
            G.state["challenge"] = q["code_challenge"]
            assert q["code_challenge_method"] == "S256" and "youtube.upload" in q["scope"]
            self.send_response(302)
            self.send_header("Location", f"{q['redirect_uri']}?code=CODE&state={q['state']}")
            self.end_headers()
        elif u.path == "/youtube/v3/channels":
            assert self.headers["Authorization"] == "Bearer ACCESS"
            self._json(200, {"items": [{"id": "UC123", "snippet": {"title": "Ma chaîne"}}]})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        if self.path == "/token":
            f = dict(urllib.parse.parse_qsl(raw.decode()))
            if f["grant_type"] == "authorization_code":
                v = base64.urlsafe_b64encode(hashlib.sha256(f["code_verifier"].encode()).digest()).rstrip(b"=").decode()
                assert v == G.state["challenge"] and f["code"] == "CODE"
                self._json(200, {"access_token": "ACCESS", "refresh_token": "REFRESH", "scope": youtube.SCOPES})
            elif f.get("refresh_token") == "REFRESH":
                self._json(200, {"access_token": "ACCESS"})
            else:
                self._json(400, {"error": "invalid_grant"})
        elif self.path.startswith("/upload/youtube/v3/videos"):
            G.state["inits"] = G.state.get("inits", 0) + 1
            G.state["meta"] = json.loads(raw)
            G.state["total"] = int(self.headers["X-Upload-Content-Length"])
            G.state["data"] = b""
            self._json(200, {}, {"Location": f"http://127.0.0.1:{self.server.server_port}/session/1"})

    def do_PUT(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        rng = self.headers["Content-Range"]
        total = G.state["total"]
        if rng.startswith("bytes */"):                  # où en est l'envoi ?
            got = len(G.state["data"])
            if got >= total:
                return self._json(200, self._video())
            self.send_response(308)
            if got:
                self.send_header("Range", f"bytes=0-{got - 1}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        G.state["puts"] = G.state.get("puts", 0) + 1
        if G.state["puts"] == 2 and not G.state.get("failed"):   # une panne serveur au 2e bloc
            G.state["failed"] = True
            return self._json(503, {"error": "backend"})
        start = int(rng.split()[1].split("-")[0])
        assert start == len(G.state["data"])
        G.state["data"] += raw
        if len(G.state["data"]) >= total:
            return self._json(201, self._video())
        self.send_response(308)
        self.send_header("Range", f"bytes=0-{len(G.state['data']) - 1}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _video(self):
        st = G.state["meta"]["status"]
        return {"id": "VID42", "status": {"privacyStatus": G.state.get("force_privacy", st["privacyStatus"])}}


@pytest.fixture
def google(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), G)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    G.state = {}
    url = f"http://127.0.0.1:{srv.server_port}"
    monkeypatch.setenv("STUDIOVIDEO_YOUTUBE_TEST_URL", url)
    monkeypatch.setattr(youtube, "_BACKOFF", 0.0)
    yield url
    srv.shutdown()


def _browser(url):
    """Simule le navigateur : suit la redirection vers 127.0.0.1 (après consentement)."""
    threading.Thread(target=lambda: urllib.request.urlopen(url, timeout=10).read(), daemon=True).start()


def test_metadata_scheduling():
    m = youtube.metadata("Titre", "Texte", "humour sketch", "public", "2026-11-05T18:00")
    assert m["status"]["privacyStatus"] == "private" and m["status"]["publishAt"].endswith("Z")
    assert m["snippet"]["tags"] == ["humour", "sketch"] and m["snippet"]["description"].endswith("#humour #sketch")


def test_connect_and_resumable_upload(google, tmp_path):
    tok = youtube.connect("client", "secret", _browser, timeout=10)
    assert tok["refresh_token"] == "REFRESH"
    assert youtube.channel(youtube.access_token("client", "secret", "REFRESH")) == {"id": "UC123", "title": "Ma chaîne"}
    with pytest.raises(youtube.YouTubeError, match="révoquée"):
        youtube.access_token("client", "secret", "OLD")
    video = tmp_path / "v.mp4"
    payload = bytes(range(256)) * 4000            # ~1 Mo
    video.write_bytes(payload)
    seen = []
    res = youtube.upload(video, youtube.metadata("T", "D", "", "public"), "ACCESS",
                         lambda f, m: seen.append(f), chunk=256 * 1024)
    assert res["id"] == "VID42" and G.state["data"] == payload and G.state.get("failed")
    assert seen and seen[-1] < 1.0


def test_jobs_connect_publish_and_private_case(google, tmp_path, monkeypatch):
    from studiovideo.core import build_context
    from studiovideo.domain.models import Platform
    from studiovideo.services import secrets
    store = {"youtube_client_secret": "secret"}
    monkeypatch.setattr(secrets, "get_secret", lambda k: store.get(k))
    monkeypatch.setattr(secrets, "set_secret", lambda k, v: store.__setitem__(k, v))
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", _browser)
    app = build_context(tmp_path / "d")

    def wait(jid):
        end = time.time() + 30
        while app.jobs.get(jid).status in ("pending", "running") and time.time() < end:
            time.sleep(0.05)
        return app.jobs.get(jid)
    try:
        app.settings.set("youtube_client_id", "client")
        c = app.concepts.create_concept("PP")
        acc = app.concepts.create_account(Platform.YOUTUBE, "PP")
        app.concepts.link(c.id, acc.id)
        j = wait(app.jobs.submit("connect_account", "Connexion", {"account_id": acc.id}))
        assert j.status == "succeeded", j.error
        assert app.concepts.get_account(acc.id).connection_status == "connected"
        assert store["youtube_refresh_" + str(acc.id)] == "REFRESH"
        # une vidéo exportée, deux publications : immédiate, puis programmée
        video = tmp_path / "v.mp4"
        video.write_bytes(b"\0" * 300_000)
        con = app.db.connect()
        con.execute("INSERT INTO project(concept_id, name) VALUES (?, 'P')", (c.id,))
        con.execute("INSERT INTO clip(project_id, name, start_s, end_s) VALUES (1, 'Montage', 0, 1)")
        con.execute("INSERT INTO video_variant(clip_id, label, aspect, output_path) VALUES (1,'v','9:16',?)",
                    (str(video),))
        m = app.publications.create_master(c.id, "Sketch", "Desc", "#humour", variant_id=1)
        pid = app.publications.set_destinations(m, [acc.id])[0]
        j = wait(app.jobs.submit("publish_upload", "YouTube", {"publication_id": pid}))
        assert j.status == "succeeded", j.error
        r = app.publications.get(pid)
        assert r["status"] == "published" and r["public_url"] == "https://youtu.be/VID42"
        j = wait(app.jobs.submit("publish_upload", "YouTube", {"publication_id": pid}))
        assert j.status == "failed" and "doublon" in j.error                     # pas de second envoi
        # YouTube laisse la vidéo privée : signalé, pas « publiée »
        m2 = app.publications.create_master(c.id, "Autre", "", "", variant_id=1)
        p2 = app.publications.set_destinations(m2, [acc.id])[0]
        G.state["force_privacy"] = "private"
        j = wait(app.jobs.submit("publish_upload", "YouTube", {"publication_id": p2}))
        assert j.status == "succeeded" and app.publications.get(p2)["status"] == "manual_action"
        assert "restée « private »" in app.publications.get(p2)["last_error"]
        # programmée : publishAt envoyé, statut « programmée sur la plateforme »
        G.state.pop("force_privacy")
        m3 = app.publications.create_master(c.id, "Plus tard", "", "", variant_id=1)
        p3 = app.publications.set_destinations(m3, [acc.id])[0]
        app.publications.schedule(p3, "2099-01-01T10:00")
        j = wait(app.jobs.submit("publish_upload", "YouTube", {"publication_id": p3}))
        assert j.status == "succeeded" and app.publications.get(p3)["status"] == "scheduled_platform"
        assert G.state["meta"]["status"]["publishAt"].startswith("2099-01-01")
        # compte déconnecté entre-temps : la publication passe en échec (jamais bloquée « en cours d'envoi »)
        m4 = app.publications.create_master(c.id, "Encore", "", "", variant_id=1)
        p4 = app.publications.set_destinations(m4, [acc.id])[0]
        app.publications.set_status(p4, "uploading")
        store.pop("youtube_refresh_" + str(acc.id))
        j = wait(app.jobs.submit("publish_upload", "YouTube", {"publication_id": p4}))
        assert j.status == "failed" and app.publications.get(p4)["status"] == "failed"
    finally:
        app.close()


def test_resume_after_restart_no_duplicate(google, tmp_path):
    video = tmp_path / "v.mp4"
    payload = bytes(range(256)) * 3000
    video.write_bytes(payload)
    saved = {}
    G.state["failed"] = True                          # pas de panne simulée ici
    res = youtube.upload(video, youtube.metadata("T", "", "", "public"), "ACCESS", chunk=256 * 1024,
                         on_session=lambda url: saved.__setitem__("url", url))
    assert res["id"] == "VID42" and G.state["inits"] == 1 and saved["url"].endswith("/session/1")
    # relance après fermeture du logiciel : l'envoi était fini → même vidéo, aucun nouvel envoi
    again = youtube.upload(video, youtube.metadata("T", "", "", "public"), "ACCESS", session=saved)
    assert again["id"] == "VID42" and G.state["inits"] == 1
    # envoi coupé à mi-parcours : la relance reprend là où il s'était arrêté
    G.state["data"] = payload[:300_000]
    again = youtube.upload(video, youtube.metadata("T", "", "", "public"), "ACCESS", session=saved, chunk=256 * 1024)
    assert again["id"] == "VID42" and G.state["inits"] == 1 and G.state["data"] == payload
