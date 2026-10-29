"""Atelier de publication sans API : navigateur dédié par compte, rapprochement automatique du lien."""
import sqlite3
import time

import pytest

from studiovideo.domain.models import Platform
from studiovideo.publish import browser, reconcile
from studiovideo.services.publications import PublicationError


# -- navigateur dédié -------------------------------------------------------------------------
def _cookies(profile, rows):
    db = profile / "Default" / "Network" / "Cookies"
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB, "
                "expires_utc INTEGER)")
    con.executemany("INSERT INTO cookies VALUES (?, ?, '', x'00', ?)", rows)
    con.commit()
    con.close()


def chrome_time(epoch):
    return int((epoch + 11_644_473_600) * 1_000_000)


def test_profile_and_command(tmp_path):
    p = browser.profile_dir(tmp_path, 7, tmp_path / "brave.exe")
    assert p == tmp_path / "browser" / "brave" / "7"
    cmd = browser.command(tmp_path / "msedge.exe", p, "https://www.tiktok.com/login")
    assert cmd[1] == f"--user-data-dir={p}" and cmd[-1] == "https://www.tiktok.com/login"
    assert "--no-first-run" in cmd
    ff = browser.command(tmp_path / "firefox.exe", p, "https://x.com")
    assert ff[1:3] == ["-profile", str(p)] and ff[-1] == "https://x.com"
    assert set(browser.LOGIN_URLS) == set(browser.SESSION_COOKIES) == {"youtube", "tiktok", "instagram",
                                                                          "facebook", "x"}


def test_find_browser_override(tmp_path, monkeypatch):
    exe = tmp_path / "msedge.exe"
    monkeypatch.setenv("STUDIOVIDEO_BROWSER", str(exe))
    assert browser.find_browser() is None                    # forcé mais absent : rien d'autre n'est pris
    exe.write_bytes(b"")
    assert browser.find_browser() == exe
    assert browser.browser_name(exe) == "Edge"
    assert browser.browser_name(tmp_path / "brave.exe") == "Brave"


def test_default_browser_first(tmp_path, monkeypatch):
    brave = tmp_path / "brave.exe"
    brave.write_bytes(b"")
    monkeypatch.delenv("STUDIOVIDEO_BROWSER", raising=False)
    monkeypatch.setattr(browser, "default_browser", lambda: brave)
    assert browser.find_browser() == brave                       # navigateur par défaut de Windows
    odd = tmp_path / "safari.exe"
    odd.write_bytes(b"")
    monkeypatch.setattr(browser, "default_browser", lambda: odd)
    assert browser.find_browser() != odd                         # sans profil séparé possible : Edge en secours


def test_firefox_session(tmp_path):
    prof = tmp_path / "ff"
    prof.mkdir()
    con = sqlite3.connect(prof / "cookies.sqlite")
    con.execute("CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT, expiry INTEGER)")
    con.execute("INSERT INTO moz_cookies VALUES ('.tiktok.com', 'sessionid', '', ?)", (int(time.time()) + 3600,))
    con.commit()
    con.close()
    assert browser.session_found(prof, "tiktok") is True
    assert browser.session_found(prof, "instagram") is False


def test_session_found_reads_names_only(tmp_path, monkeypatch):
    prof = tmp_path / "p"
    assert browser.session_found(prof, "tiktok") is False                # profil jamais ouvert
    now = time.time()
    _cookies(prof, [(".tiktok.com", "sessionid", chrome_time(now + 86400)),
                    (".instagram.com", "sessionid", chrome_time(now - 60)),      # expiré
                    (".facebook.com", "datr", chrome_time(now + 86400))])
    assert browser.session_found(prof, "tiktok") is True
    assert browser.session_found(prof, "instagram") is False
    assert browser.session_found(prof, "facebook") is False
    monkeypatch.setattr(browser, "is_open", lambda _p: True)
    assert browser.session_found(prof, "tiktok") is None                 # navigateur ouvert : on ne sait pas


def test_is_open_ignores_stale_lockfile(tmp_path):
    (tmp_path / "lockfile").write_text("")
    assert browser.is_open(tmp_path) is False
    assert browser.is_open(tmp_path / "absent") is False


# -- rapprochement ----------------------------------------------------------------------------
def test_listing_urls():
    assert reconcile.listing_urls("tiktok", "https://www.tiktok.com/@pousseypass/video/1?lang=fr") == [
        "https://www.tiktok.com/@pousseypass"]
    assert reconcile.listing_urls("youtube", "https://www.youtube.com/@pp/featured") == [
        "https://www.youtube.com/@pp/shorts", "https://www.youtube.com/@pp/videos"]
    assert reconcile.listing_urls("youtube", "https://youtu.be/abc") == []
    assert reconcile.listing_urls("instagram", "https://www.instagram.com/pp") == []
    assert reconcile.listing_urls("tiktok", "") == []


def test_text_score():
    assert reconcile.text_score("Mon SKETCH génial #humour", "mon sketch genial #humour") == 1.0
    assert reconcile.text_score("Un très long texte de légende coupé", "Un très long texte") == 0.97
    assert reconcile.text_score("", "x") == 0.0
    assert reconcile.text_score("Le chat dort", "Recette de crêpes") < 0.5


NOW = 1_800_000_000.0


def entry(url, desc, dur=30.0, ts=NOW + 3600, title=""):
    return {"url": url, "title": title, "description": desc, "duration": dur, "timestamp": ts}


def test_match_sure_doubt_and_taken():
    pubs = [{"id": 1, "title": "", "text": "Le sketch du boulanger #humour", "duration": 30.2, "since": NOW},
            {"id": 2, "title": "", "text": "Recette secrète de mamie", "duration": 45.0, "since": NOW}]
    entries = [entry("https://t/1", "Le sketch du boulanger #humour", 30.0),
               entry("https://t/2", "Recette secrete de mamie (partie 2)", 52.0),
               entry("https://t/3", "Autre chose", 12.0)]
    out = {m["publication_id"]: m for m in reconcile.match(pubs, entries)}
    assert out[1]["sure"] and out[1]["url"] == "https://t/1"
    assert not out[2]["sure"] and out[2]["url"] == "https://t/2"          # durée différente : seulement proposée
    # vidéo déjà reliée à une autre publication : écartée
    assert 1 not in {m["publication_id"] for m in reconcile.match(pubs[:1], entries, {"https://t/1"})}
    yt = [entry("https://www.youtube.com/shorts/abcDEF12345", "Le sketch du boulanger #humour", 30.0)]
    assert reconcile.match(pubs[:1], yt, {"https://www.youtube.com/watch?v=abcDEF12345"}) == []
    assert reconcile.video_key("https://www.tiktok.com/@pp/video/123?lang=fr") == "123"


def test_match_rejects_older_videos_and_ambiguity():
    pub = {"id": 1, "title": "", "text": "Le sketch du boulanger", "duration": 30.0, "since": NOW}
    old = entry("https://t/old", "Le sketch du boulanger", 30.0, ts=NOW - 5 * 86400)
    assert reconcile.match([pub], [old]) == []                            # publiée avant la préparation
    twins = [entry("https://t/a", "Le sketch du boulanger", 30.0), entry("https://t/b", "Le sketch du boulanger", 30.0)]
    (m,) = reconcile.match([pub], twins)
    assert not m["sure"]                                                  # deux vidéos identiques : on demande


def test_match_without_caption_never_sure():
    pub = {"id": 1, "title": "", "text": "", "duration": 30.0, "since": NOW}
    (m,) = reconcile.match([pub], [entry("https://t/1", "n'importe quoi", 30.1)])
    assert not m["sure"] and m["score"] >= reconcile.DOUBT


# -- services et tâche ------------------------------------------------------------------------
@pytest.fixture
def app(tmp_path):
    from studiovideo.core import build_context
    a = build_context(tmp_path / "d")
    yield a
    a.close()


def _pub(app, platform, text, url=""):
    c = app.concepts.create_concept(f"C {platform} {text[:5]}")
    acc = app.concepts.create_account(platform, f"Compte {platform}", public_url=url)
    app.concepts.link(c.id, acc.id)
    m = app.publications.create_master(c.id, text)
    (pid,) = app.publications.set_destinations(m, [acc.id])
    app.publications.mark_manual(pid, "Envoyée depuis l'Atelier")
    return acc, pid


def test_proposals_service(app):
    acc, pid = _pub(app, Platform.TIKTOK, "Mon sketch", "https://www.tiktok.com/@pp")
    (row,) = app.publications.awaiting_link()
    assert row["id"] == pid and row["account_url"] == "https://www.tiktok.com/@pp" and row["since"]
    assert app.publications.propose_match(pid, "https://www.tiktok.com/@pp/video/1", "Mon sketch", 0.6)
    assert not app.publications.propose_match(pid, "https://www.tiktok.com/@pp/video/1", "Mon sketch", 0.6)
    (m,) = app.publications.matches(pid)
    app.publications.reject_match(m["id"])
    assert app.publications.matches(pid) == []
    assert not app.publications.propose_match(pid, "https://www.tiktok.com/@pp/video/1", "x", 0.9)  # déjà refusé
    app.publications.propose_match(pid, "https://www.youtube.com/watch?v=x", "Mauvais réseau", 0.7)
    (bad,) = app.publications.matches(pid)
    with pytest.raises(PublicationError):
        app.publications.accept_match(bad["id"])
    app.publications.propose_match(pid, "https://www.tiktok.com/@pp/video/2", "Mon sketch", 0.7)
    good = [m for m in app.publications.matches(pid) if m["url"].endswith("/2")][0]
    app.publications.accept_match(good["id"])
    r = app.publications.get(pid)
    assert r["status"] == "published" and r["public_url"].endswith("/video/2")
    assert app.publications.matches(pid) == [] and app.publications.awaiting_link() == []
    assert app.publications.linked_urls(acc.id) == {"https://www.tiktok.com/@pp/video/2"}


def _run(app, params):
    jid = app.jobs.submit("match_published", "Liens", params)
    end = time.time() + 20
    while app.jobs.get(jid).status in ("pending", "running") and time.time() < end:
        time.sleep(0.05)
    return app.jobs.get(jid)


def test_match_published_job(app, monkeypatch):
    tk, sure = _pub(app, Platform.TIKTOK, "Le sketch du boulanger", "https://www.tiktok.com/@pp")
    _, doubt = _pub(app, Platform.TIKTOK, "Une recette", "https://www.tiktok.com/@pp2")
    _, no_url = _pub(app, Platform.YOUTUBE, "Sans adresse")
    _, insta = _pub(app, Platform.INSTAGRAM, "Insta", "https://www.instagram.com/pp")
    listed = []
    future = time.time() + 600

    def fake_list(url, limit=15):
        listed.append(url)
        if url.endswith("@pp"):
            return [entry("https://www.tiktok.com/@pp/video/11", "Le sketch du boulanger", None, ts=future)]
        return [entry("https://www.tiktok.com/@pp2/video/22", "Une recette de crêpes au beurre", None, ts=future)]
    monkeypatch.setattr(reconcile, "list_recent", fake_list)
    monkeypatch.setattr(reconcile, "details", lambda url: {})
    j = _run(app, {"delay": None})
    assert j.status == "succeeded", j.error
    assert listed == ["https://www.tiktok.com/@pp", "https://www.tiktok.com/@pp2"]
    assert [c["publication_id"] for c in j.result["confirmed"]] == [sure]
    assert app.publications.get(sure)["public_url"] == "https://www.tiktok.com/@pp/video/11"
    assert j.result["proposed"] == 1 and app.publications.matches(doubt)
    assert app.publications.get(doubt)["status"] == "manual_action"            # un doute n'est jamais confirmé
    assert len(j.result["skipped"]) == 2                                       # sans adresse ; Instagram
    assert app.concepts.account_settings(tk.id)["reconcile"]["videos"] == 1
    # page illisible : l'erreur est notée, rien n'est inventé
    monkeypatch.setattr(reconcile, "list_recent", lambda url, limit=15: (_ for _ in ()).throw(RuntimeError("403")))
    j = _run(app, {"delay": None})
    assert j.status == "succeeded" and "403" in j.result["errors"][0]
    assert app.publications.get(doubt)["status"] == "manual_action"


# -- interface --------------------------------------------------------------------------------
def test_workshop_ui(qtbot, app, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication

    from studiovideo.ui.publish_workshop import PublishWorkshop
    monkeypatch.setenv("STUDIOVIDEO_BROWSER", str(tmp_path / "absent.exe"))
    _, yt = _pub(app, Platform.YOUTUBE, "Titre YouTube", "https://www.youtube.com/@pp")
    _, tk = _pub(app, Platform.TIKTOK, "Légende TikTok", "https://www.tiktok.com/@pp")
    app.publications.propose_match(tk, "https://www.tiktok.com/@pp/video/5", "Légende TikTok", 0.7)
    opened = []
    w = PublishWorkshop(app, [yt, tk], open_url=opened.append)
    qtbot.addWidget(w)
    assert not hasattr(w, "text") and "toutes les heures" not in w.link.auto_note.text()   # phrase retirée
    QApplication.clipboard().setText("")
    w._open()                                                                  # page d'envoi : texte copié
    assert QApplication.clipboard().text() == "Titre YouTube"                  # YouTube : le titre d'abord
    assert opened == ["https://www.youtube.com/upload"] and "nouvel onglet" in w.msg.text()   # navigateur déjà ouvert
    w.group.button(1).click()
    assert w.link.props.count() == 1                                           # « Est-ce cette vidéo ? »
    (m,) = app.publications.matches(tk)
    w.link._accept(m["id"])
    assert app.publications.get(tk)["status"] == "published" and "✓ Publiée" in w.state.text()
    assert not w.open_btn.isEnabled()
    assert not hasattr(w, "login_btn")                                         # tes comptes : ton navigateur


def test_video_tile_drags_the_file(qtbot, tmp_path):
    from studiovideo.ui.publish_workshop import VideoTile
    f = tmp_path / "2026-04-02 Ma vidéo.mp4"
    f.write_bytes(b"x")
    t = VideoTile(str(f))
    qtbot.addWidget(t)
    assert t.isEnabled()
    assert [u.toLocalFile() for u in t.mime().urls()] == [str(f).replace("\\", "/")]
    assert not VideoTile(str(tmp_path / "absent.mp4")).isEnabled()


def test_account_dialog_browser_first(qtbot, app):
    from studiovideo.ui.account_setup import AccountSetupDialog
    acc = app.concepts.create_account(Platform.TIKTOK, "PP")
    d = AccountSetupDialog(app, "tiktok", acc.id)
    qtbot.addWidget(d)
    assert not d.adv_btn.isChecked() and d.adv.isHidden()                     # API repliée
    assert not hasattr(d, "login_btn")                                         # plus de profil séparé
    d.url.setText("https://www.tiktok.com/@pp")
    d._ok()
    assert app.concepts.get_account(acc.id).public_url == "https://www.tiktok.com/@pp"
    assert d.connect_job is None
    assert AccountSetupDialog(app, "tiktok", acc.id, advanced=True).adv_btn.isChecked()


def test_calendar_sync_published_videos(app, monkeypatch):
    """Vidéos mises en ligne ces 20 derniers jours sur les chaînes du concept : ajoutées au calendrier (titre,
    description) ; une publication du même jour qui leur ressemble est reliée et prend l'heure réelle (la plus tôt
    si les réseaux diffèrent) ; une vidéo déjà reliée n'est pas ajoutée deux fois."""
    from datetime import datetime, timedelta

    from studiovideo.publish import reconcile
    c = app.concepts.create_concept("Tous y passent")
    yt = app.concepts.create_account(Platform.YOUTUBE, "Tous y passent", "https://www.youtube.com/@tous")
    tk = app.concepts.create_account(Platform.TIKTOK, "Tous y passent", "https://www.tiktok.com/@tous")
    for a in (yt, tk):
        app.concepts.link(c.id, a.id)
    day = (datetime.now() - timedelta(days=3)).replace(hour=0, minute=0, second=0, microsecond=0)
    old = app.publications.plan(c.id, None, "Le vrai chiffre du chômage", day.strftime("%Y-%m-%dT") + "18:00",
                                {"youtube": False, "tiktok": False})
    ts_yt = (day + timedelta(hours=19, minutes=12)).timestamp()
    ts_tk = (day + timedelta(hours=17, minutes=40)).timestamp()
    ts_new = (day - timedelta(days=2) + timedelta(hours=12)).timestamp()
    ts_too_old = (datetime.now() - timedelta(days=25)).timestamp()

    def fake_list(url, limit=15):
        if "youtube" in url and url.endswith("/shorts"):
            return [entry("https://www.youtube.com/shorts/AAAAAAAAAAA", "Ce qu'on ne vous dit pas", None, ts=ts_yt, title="Le vrai chiffre du chômage"),
                    entry("https://www.youtube.com/shorts/BBBBBBBBBBB", "Tarte aux pommes #cuisine", None, ts=ts_new, title="Une recette"),
                    entry("https://www.youtube.com/shorts/CCCCCCCCCCC", "x", None, ts=ts_too_old, title="Trop vieille")]
        if "tiktok" in url:
            return [entry("https://www.tiktok.com/@tous/video/1", "Le vrai chiffre du chômage #politique", None, ts=ts_tk, title="")]
        return []
    monkeypatch.setattr(reconcile, "list_recent", fake_list)

    def wait_job(app, jid):
        end = time.time() + 20
        while app.jobs.get(jid).status in ("pending", "running") and time.time() < end:
            time.sleep(0.05)
        return app.jobs.get(jid)
    monkeypatch.setattr(reconcile, "details", lambda url: {})
    res = wait_job(app, app.jobs.submit("sync_calendar", "Sync", {"delay": None})).result
    assert res["matched"] == 2 and res["added"] == 1 and not res["errors"]
    p = app.publications.plan_of(old)
    assert p["networks"]["youtube"] and p["networks"]["tiktok"]                     # logos en couleur
    assert p["planned_at"] == day.strftime("%Y-%m-%dT") + "17:40"                 # la mise en ligne la plus tôt
    added = [r for r in app.publications.planned("2000-01-01T00:00", "9999-01-01T00:00", c.id) if r["id"] != old]
    assert len(added) == 1 and added[0]["text"] == "Une recette\nTarte aux pommes #cuisine"
    assert added[0]["links"]["youtube"].endswith("BBBBBBBBBBB")
    res = wait_job(app, app.jobs.submit("sync_calendar", "Sync", {"delay": None})).result
    assert res["added"] == 0 and res["matched"] == 0 and res["known"] == 3      # rien en double
