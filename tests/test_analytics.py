"""V4 : relevés horodatés, indicateurs non fournis jamais remplacés par zéro, agrégats, relevé YouTube réel (simulé)."""
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from studiovideo.domain.models import Platform, PublicationStatus
from studiovideo.services.analytics import AnalyticsService, youtube_id
from studiovideo.services.concepts import ConceptService
from studiovideo.services.publications import PublicationService


def test_youtube_id():
    assert youtube_id("https://youtu.be/abcDEF12345") == "abcDEF12345"
    assert youtube_id("https://www.youtube.com/watch?v=abcDEF12345&t=3") == "abcDEF12345"
    assert youtube_id("https://youtube.com/shorts/abcDEF12345") == "abcDEF12345"
    assert youtube_id("VID42abc") == "VID42abc" and youtube_id("https://x.com/a") is None


@pytest.fixture
def data(db):
    cs, ps, an = ConceptService(db), PublicationService(db), AnalyticsService(db)
    pp, pk = cs.create_concept("Poussey Pass"), cs.create_concept("Pokémon")
    yt, tk, x = (cs.create_account(Platform.YOUTUBE, "PP"), cs.create_account(Platform.TIKTOK, "PP"),
                 cs.create_account(Platform.X, "PK"))
    cs.link(pp.id, yt.id)
    cs.link(pp.id, tk.id)
    cs.link(pk.id, x.id)
    m1 = ps.create_master(pp.id, "Sketch")
    p_yt, p_tk = ps.set_destinations(m1, [yt.id, tk.id])
    m2 = ps.create_master(pk.id, "Carte")
    (p_x,) = ps.set_destinations(m2, [x.id])
    ps.set_status(p_yt, PublicationStatus.PUBLISHED, remote_id="VIDyt1", public_url="https://youtu.be/VIDyt1")
    ps.confirm_published(p_tk, "https://www.tiktok.com/@pp/video/1")
    ps.confirm_published(p_x, "https://x.com/pk/status/1")
    return an, ps, (pp, pk), (p_yt, p_tk, p_x)


def test_latest_and_aggregates(data):
    an, ps, (pp, pk), (p_yt, p_tk, p_x) = data
    an.record(p_yt, {"views": 100, "likes": 10}, "api")
    an.record(p_yt, {"views": 150, "likes": 12, "comments": 3}, "api")
    an.record(p_tk, {"views": 1000, "likes": 50, "shares": 7}, "manuel")
    rows = {r["id"]: r for r in an.latest()}
    assert rows[p_yt]["metrics"] == {"views": 150, "likes": 12, "comments": 3, "_source": "api"}
    assert rows[p_x]["metrics"] == {}                                    # aucun relevé : rien d'inventé
    assert len(an.history(p_yt)) == 2
    by_concept = {g["key"]: g for g in an.aggregate(list(rows.values()), "concept")}
    assert by_concept["Poussey Pass"]["views"] == 1150 and by_concept["Poussey Pass"]["n_comments"] == 1
    assert "views" not in by_concept["Pokémon"]                          # non fourni ≠ 0
    assert by_concept["Poussey Pass"]["engagement"] == pytest.approx((62 + 3 + 7) / 1150)
    assert [r["id"] for r in an.latest(platform="tiktok")] == [p_tk]
    assert an.youtube_ids() == [(p_yt, "VIDyt1")]


class Stats(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.dumps({"access_token": "ACCESS"}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/v2/reports":
            assert q["ids"] == "channel==MINE" and q["filters"].startswith("video==")
            if q.get("dimensions") == "elapsedVideoTimeRatio":
                rep = {"columnHeaders": [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"}],
                       "rows": [[0.01, 1.0], [0.5, 0.62], [1.0, 0.35]]}
            else:
                rep = {"columnHeaders": [{"name": n} for n in q["metrics"].split(",")], "rows": [[120.5, 42, 61.3]]}
            body = json.dumps(rep).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
            return
        items = [{"id": i, "statistics": {"viewCount": "4321", "commentCount": "9"}} for i in q["id"].split(",")
                 if i != "GONE000"]
        body = json.dumps({"items": items}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)


def test_refresh_stats_job(tmp_path, monkeypatch):
    from studiovideo.core import build_context
    from studiovideo.services import secrets
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Stats)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("STUDIOVIDEO_YOUTUBE_TEST_URL", f"http://127.0.0.1:{srv.server_port}")
    app = build_context(tmp_path / "d")
    try:
        c = app.concepts.create_concept("PP")
        yt = app.concepts.create_account(Platform.YOUTUBE, "PP")
        app.concepts.link(c.id, yt.id)
        m = app.publications.create_master(c.id, "A")
        (p1,) = app.publications.set_destinations(m, [yt.id])
        app.publications.confirm_published(p1, "https://youtube.com/shorts/ABCdef12345")
        m2 = app.publications.create_master(c.id, "B")
        (p2,) = app.publications.set_destinations(m2, [yt.id])
        app.publications.set_status(p2, PublicationStatus.PUBLISHED, remote_id="GONE000")

        def run():
            jid = app.jobs.submit("refresh_stats", "Statistiques", {})
            end = time.time() + 20
            while app.jobs.get(jid).status in ("pending", "running") and time.time() < end:
                time.sleep(0.05)
            return app.jobs.get(jid)
        monkeypatch.setattr(secrets, "get_secret", lambda k: None)
        j = run()
        # sans compte connecté par API : pas une erreur ; la page publique sera lue à son heure (6 h après)
        assert j.status == "succeeded" and j.result["updated"] == 0, j.error
        monkeypatch.setattr(secrets, "get_secret", lambda k: "REFRESH" if k.startswith("youtube_refresh") else "s")
        with app.db.transaction() as con:
            con.execute("UPDATE social_account SET connection_status='connected' WHERE id=?", (yt.id,))
        j = run()
        assert j.status == "succeeded", j.error
        assert j.result["updated"] == 1                    # GONE000 : introuvable par l'API, sans erreur
        latest = {r["id"]: r["metrics"] for r in app.analytics.latest()}
        assert latest[p1]["views"] == 4321 and "likes" not in latest[p1]              # j'aime masqués : absents
        assert latest[p1]["avg_view_s"] == 42 and latest[p1]["avg_view_pct"] == 61.3
        assert latest[p1]["retention"] == [[0.01, 1.0], [0.5, 0.62], [1.0, 0.35]]
        assert j.result["detailed"] == 1
    finally:
        app.close()
        srv.shutdown()


def test_public_stats_schedule_and_metrics(tmp_path):
    from datetime import datetime, timedelta

    from studiovideo.publish import public_stats as ps
    pub = datetime(2026, 10, 1, 12, 0)
    assert ps.next_reading(pub, None) == pub + timedelta(hours=6)                    # 6 h après
    assert ps.next_reading(pub, pub + timedelta(hours=7)) == pub + timedelta(days=1)    # puis J+1
    assert ps.next_reading(pub, pub + timedelta(days=3, hours=1)) == pub + timedelta(days=7)
    late = pub + timedelta(days=31)
    assert ps.next_reading(pub, late) == late + timedelta(days=30)                   # ensuite chaque mois
    assert not ps.due("2026-10-01T12:00:00Z", None, pub + timedelta(hours=5))
    assert ps.due("2026-10-01T12:00:00Z", None, pub + timedelta(hours=6, minutes=1))
    assert not ps.due("2026-10-01T12:00:00Z", "2026-10-01T18:30:00Z", pub + timedelta(hours=20))
    assert ps.metrics({"view_count": 10, "like_count": None, "comment_count": 3, "repost_count": 0}) == \
        {"views": 10, "comments": 3, "shares": 0}                                     # absent : jamais inventé
    assert ps.cookies_from(tmp_path / "brave.exe") == ("brave", None, None, None)      # le profil habituel
    assert ps.cookies_from(tmp_path / "msedge.exe")[0] == "edge"
    assert ps.cookies_from(tmp_path / "safari.exe") is None


def test_public_stats_job(tmp_path, monkeypatch):
    """Sans API : la page publique de chaque vidéo publiée est lue ; Facebook / Instagram avec les cookies du
    navigateur habituel ; une vidéo publiée sur plusieurs réseaux est regroupée."""
    from studiovideo.core import build_context
    from studiovideo.publish import public_stats
    from studiovideo.services import secrets
    monkeypatch.setattr(secrets, "get_secret", lambda k: None)
    exe = tmp_path / "brave.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("STUDIOVIDEO_BROWSER", str(exe))
    calls = []

    def fake_read(url, cookies=None):
        calls.append((url, cookies))
        return {"views": 100 if "tiktok" in url else 7, "likes": 3}
    monkeypatch.setattr(public_stats, "read", fake_read)
    app = build_context(tmp_path / "d")
    try:
        c = app.concepts.create_concept("PP")
        tk = app.concepts.create_account(Platform.TIKTOK, "pp")
        fb = app.concepts.create_account(Platform.FACEBOOK, "pp")
        m = app.publications.create_master(c.id, "Ma vidéo")
        p1, p2 = app.publications.set_destinations(m, [tk.id, fb.id], confirmed_external={tk.id, fb.id})
        app.publications.confirm_published(p1, "https://www.tiktok.com/@pp/video/1")
        app.publications.confirm_published(p2, "https://www.facebook.com/reel/2")
        assert {t["id"] for t in app.analytics.public_targets()} == {p1, p2}

        def run(params):
            jid = app.jobs.submit("refresh_stats", "Statistiques", params)
            end = time.time() + 20
            while app.jobs.get(jid).status in ("pending", "running") and time.time() < end:
                time.sleep(0.05)
            return app.jobs.get(jid)
        j = run({})                                                   # automatique : rien avant 6 h
        assert j.status == "succeeded" and j.result["public"] == 0 and not calls
        j = run({"all": True, "delay": None})                         # bouton « Relever » : tout, tout de suite
        assert j.status == "succeeded" and j.result["public"] == 2, j.result
        by_url = dict(calls)
        assert by_url["https://www.tiktok.com/@pp/video/1"] is None                     # TikTok : sans session
        assert by_url["https://www.facebook.com/reel/2"] == ("brave", None, None, None)   # cookies de ton Brave
        latest = {r["id"]: r["metrics"] for r in app.analytics.latest()}
        assert latest[p1] == {"views": 100, "likes": 3, "_source": "public"}
        (g,) = app.analytics.by_video(app.analytics.latest())        # une vidéo, deux réseaux
        assert g["title"] == "Ma vidéo" and set(g["pubs"]) == {"tiktok", "facebook"} and g["views"] == 107
        calls.clear()
        assert run({"all": True, "delay": None}).result["public"] == 0 and not calls    # relevé il y a < 1 h
    finally:
        app.close()
