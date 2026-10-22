"""Test de fumée de l'interface (mode sans affichage)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

pytest.importorskip("PySide6")

from studiovideo.core import build_context  # noqa: E402
from studiovideo.domain.models import Platform  # noqa: E402


@pytest.fixture
def window(qtbot, tmp_path):
    from studiovideo.ui.main_window import MainWindow
    ctx = build_context(tmp_path / "data")
    c = ctx.concepts.create_concept("Pokémon")
    ctx.concepts.link(c.id, ctx.concepts.create_account(Platform.X, "Pk").id)
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    yield w, ctx
    w.close()
    ctx.close()


def test_all_pages_open(window):
    w, _ = window
    for i in range(6):
        w.go(i)
        assert w.pages.currentIndex() == i


def test_update_button_disabled_outside_windows_install(window):
    w, _ = window
    assert not w.updater.available


def test_open_project_and_create_clip(window):
    w, ctx = window
    pid = ctx.projects.create(ctx.concepts.list_concepts()[0].id, "Projet")
    w.open_project(pid)
    assert w.pages.currentWidget() is w.studio and w.studio.project_id == pid
    w.studio._autosave()
    assert ctx.projects.load_data(pid)["timeline"]["segments"] == []


def test_studio_timeline_editing_persists(window, tmp_path):
    import subprocess

    from studiovideo.media import ffmpeg
    if not ffmpeg.available():
        pytest.skip("FFmpeg absent")
    w, ctx = window
    vid = tmp_path / "v.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=25:duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(vid)],
                   check=True)
    pid = ctx.projects.create(ctx.concepts.list_concepts()[0].id, "Montage")
    aid = ctx.importer().import_file(vid, project_id=pid).asset_id
    w.open_project(pid)
    st = w.studio
    st.asset_id = aid
    st.player.in_s, st.player.out_s = 1.0, 5.0
    st._add_media_to_timeline()
    assert st.tl.duration == 4.0
    st.mplayer.seek(2.0)
    st.split()
    assert len(st.tl.segments) == 2
    st._snapshot()
    st.undo()
    assert len(st.tl.segments) == 1
    st.redo()
    assert len(st.tl.segments) == 2
    st.panel._add_text("hook")
    assert st.tl.texts and st.tl.texts[0].start == 2.0
    st.flush()
    reloaded = ctx.montage.load(pid)
    assert len(reloaded.segments) == 2 and reloaded.texts[0].text == "Accroche"
    # réglages du concept : appliqués au projet suivant
    st.tl.set_aspect("1:1")
    st._save_concept()
    pid2 = ctx.projects.create(ctx.concepts.list_concepts()[0].id, "Suivant")
    assert ctx.montage.load(pid2).format.aspect == "1:1"
    assert st.tl.problems(ctx.montage.asset_row) == []


def test_translation_flow_in_ui(window, tmp_path):
    import threading
    import time
    from http.server import HTTPServer

    from test_translate import Handler, tr
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    w, ctx = window
    try:
        ctx.settings.set("llm", {"kind": "ollama", "model": "m", "base_url": f"http://127.0.0.1:{srv.server_port}"})
        pid = ctx.projects.create(ctx.concepts.list_concepts()[0].id, "Trad")
        con = ctx.db.connect()
        con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, "
                    "audio_tracks, duration_s) VALUES (1,?,'video','x.mp4','x.mp4','fx',1,8)", (pid,))
        aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        ctx.transcripts.store_original(aid, tr())
        w.open_project(pid)
        st = w.studio
        st.asset_id = aid
        st.transcript.set_asset(aid)
        from studiovideo.ui.translate_dialog import TranslateDialog
        dlg = TranslateDialog(ctx, aid, ["Poussey Pass"])
        dlg.langs["en"].setChecked(True)
        dlg.langs["es"].setChecked(True)
        dlg._go()
        st.transcript.translate_jobs.update(dlg.job_ids)
        end = time.time() + 20
        while time.time() < end and any(ctx.jobs.get(j).status in ("pending", "running") for j in dlg.job_ids):
            time.sleep(0.05)
        for j in dlg.job_ids:
            st.transcript.on_job(ctx.jobs.get(j))
        assert set(ctx.transcripts.languages(aid)) == {"en", "es"}
        assert st.transcript.view_lang.count() == 3
        # sous-titres en anglais sur le montage
        st.tl.add_segment(aid, 0, 7)
        st.tl.subtitles.language = "en"
        st._generate_subs()
        assert any("Hello Poussey Pass" in c.text for c in st.tl.subtitles.cues)
        # série multilingue
        from studiovideo.edit.batch import BatchOptions, plan_batch
        trs = {(aid, l): ctx.transcripts.for_language(aid, l) for l in ("", "en", "es")}
        out = plan_batch(st.tl, [{"asset_id": aid, "start": 0, "end": 7, "name": "c", "hook": "h"}],
                         BatchOptions(aspects=["9:16"], languages=["", "en", "es"]), trs)
        assert [a for _, a, _ in out] == ["9:16", "9:16 · en", "9:16 · es"]
        assert out[1][2].subtitles.language == "en" and out[1][2].texts == []
    finally:
        srv.shutdown()


def test_dub_dialog_and_voice_choice(window, tmp_path, monkeypatch):
    import threading
    import time
    from http.server import HTTPServer

    from test_dubbing import Fake, translated
    from studiovideo.services import secrets
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("STUDIOVIDEO_ELEVENLABS_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setattr(secrets, "get_secret", lambda n: "good" if n == "elevenlabs_api_key" else None)
    w, ctx = window
    try:
        pid = ctx.projects.create(ctx.concepts.list_concepts()[0].id, "Dub")
        con = ctx.db.connect()
        con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, "
                    "audio_tracks, duration_s) VALUES (1,?,'video','x.mp4','x.mp4','fd',1,8)", (pid,))
        aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        ctx.transcripts.store_original(aid, translated())
        ctx.transcripts.store_translation(aid, translated())
        from studiovideo.ui.dub_dialog import DubDialog
        dlg = DubDialog(ctx, aid, "en")
        assert not dlg.go.isEnabled()
        dlg._load_voices()
        assert dlg.go.isEnabled() and set(dlg.voice_boxes) == {"Alice", "Bob"}
        assert dlg.voice_boxes["Bob"].count() == 2
        dlg.consent.setChecked(True)
        dlg._launch()
        end = time.time() + 30
        while time.time() < end and ctx.jobs.get(dlg.job_ids[0]).status in ("pending", "running"):
            time.sleep(0.05)
        assert ctx.jobs.get(dlg.job_ids[0]).status == "succeeded", ctx.jobs.get(dlg.job_ids[0]).error
        # le montage propose maintenant « Doublage : anglais »
        w.open_project(pid)
        st = w.studio
        st.tl.add_segment(aid, 0, 7)
        mp = st.panel
        mp.refresh()
        i = mp.voice_combo.findData("en")
        assert i > 0
        mp.voice_combo.setCurrentIndex(i)
        assert st.tl.voice.mode == "dub" and st.tl.voice.language == "en"
        dlg2 = DubDialog(ctx, aid, "en")
        assert dlg2.lines.count() == 3
    finally:
        srv.shutdown()


def test_mask_controls(window):
    w, ctx = window
    pid = ctx.projects.create(ctx.concepts.list_concepts()[0].id, "Masques")
    w.open_project(pid)
    mp = w.studio.panel
    mp._add_mask(0.05, 0.78, 0.9, 0.16)
    assert w.studio.tl.video.masks == [{"x": 0.05, "y": 0.78, "w": 0.9, "h": 0.16, "mode": "blur"}]
    mp.mask_spins["h"].setValue(20)
    mp.mask_mode.setCurrentIndex(mp.mask_mode.findData("box"))
    assert w.studio.tl.video.masks[0]["h"] == 0.2 and w.studio.tl.video.masks[0]["mode"] == "box"
    w.studio.flush()
    assert ctx.montage.load(pid).video.masks[0]["mode"] == "box"
    mp._del_mask()
    assert w.studio.tl.video.masks == []


def test_calendar_publication_flow(window, monkeypatch):
    from studiovideo.domain.models import Platform
    from studiovideo.ui import publication_dialog as pd
    from studiovideo.ui.pages import calendar as calmod
    w, ctx = window
    c = ctx.concepts.create_concept("Poussey Pass")
    yt = ctx.concepts.create_account(Platform.YOUTUBE, "Chaîne")
    tk = ctx.concepts.create_account(Platform.TIKTOK, "Compte")
    other = ctx.concepts.create_account(Platform.X, "Autre")
    ctx.concepts.link(c.id, yt.id)
    ctx.concepts.link(c.id, tk.id)
    w.go(3)
    cal = w.calendar
    dlg = pd.PublicationDialog(ctx, day="2026-11-05")
    dlg.concept.setCurrentIndex(dlg.concept.findData(c.id))
    assert {r["account"].id for r in dlg.rows} == {yt.id, tk.id}
    assert other.id not in {r["account"].id for r in dlg.rows}          # hors concept : jamais proposé
    dlg.title.setText("Mon sketch")
    dlg.tags.setText("humour, sketch")
    assert "Textes conformes" in dlg.warn.text()
    dlg.title.setText("x" * 120)
    assert "titre trop long" in dlg.warn.text()
    dlg.title.setText("Mon sketch")
    dlg._save()
    m = dlg.master_id
    pubs = {p.account.id: p for p in ctx.publications.list_for_master(m)}
    assert set(pubs) == {yt.id, tk.id} and all(p.scheduled_at == "2026-11-05T18:00" for p in pubs.values())
    assert ctx.publications.master(m)["hashtags"] == "#humour #sketch"
    cal.year, cal.month = 2026, 11
    cal.refresh()
    from PySide6.QtWidgets import QPushButton
    texts = [b.text() for cell in cal.cells for b in cell.findChildren(QPushButton)]
    assert sum("Mon sketch" in t for t in texts) == 2
    # rappel : l'heure est passée → action manuelle demandée, badge sur la navigation
    monkeypatch.setattr("studiovideo.ui.main_window.now_local", lambda: "2026-11-05T18:01")
    assert w.check_reminders() == 2
    assert "● 2" in w.nav_group.button(3).text()
    cal.refresh()
    assert cal.todo.count() == 2
    # publication assistée puis confirmation par le lien public
    opened = []
    a = pd.AssistedPublishDialog(ctx, pubs[yt.id].id, open_url=opened.append)
    a._open()
    assert opened == ["https://www.youtube.com/upload"]
    a.url.setText("https://www.tiktok.com/@x/video/1")
    a._confirm()
    assert "plateforme" in a.msg.text()
    a.url.setText("https://youtu.be/abc")
    a._confirm()
    assert ctx.publications.get(pubs[yt.id].id)["status"] == "published"
    assert calmod.now_local()[:2] == "20"


def test_reminder_uploads_connected_youtube(window, monkeypatch, tmp_path):
    w, ctx = window
    c = ctx.concepts.create_concept("Auto")
    yt = ctx.concepts.create_account(Platform.YOUTUBE, "Connectée")
    ig = ctx.concepts.create_account(Platform.INSTAGRAM, "Assistée")
    for a in (yt, ig):
        ctx.concepts.link(c.id, a.id)
    with ctx.db.transaction() as con:
        con.execute("UPDATE social_account SET connection_status = 'connected' WHERE id = ?", (yt.id,))
        con.execute("INSERT INTO project(concept_id, name) VALUES (?, 'P')", (c.id,))
        pid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute("INSERT INTO clip(project_id, name, start_s, end_s) VALUES (?, 'Montage', 0, 1)", (pid,))
        clip = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute("INSERT INTO video_variant(clip_id, label, aspect, output_path) VALUES (?,'v','9:16',?)",
                    (clip, str(tmp_path / "v.mp4")))
        vid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    m = ctx.publications.create_master(c.id, "Titre", variant_id=vid)
    p_yt, p_ig = ctx.publications.set_destinations(m, [yt.id, ig.id])
    for p in (p_yt, p_ig):
        ctx.publications.schedule(p, "2026-01-01T10:00")
    submitted = []
    monkeypatch.setattr(ctx.jobs, "submit", lambda kind, lbl, params, **k: submitted.append((kind, params)) or 1)
    monkeypatch.setattr("studiovideo.ui.main_window.now_local", lambda: "2026-01-01T10:05")
    assert w.check_reminders() == 1                                        # seul Instagram demande une action
    assert submitted == [("publish_upload", {"publication_id": p_yt})]
    assert ctx.publications.get(p_yt)["status"] == "uploading"
    assert ctx.publications.get(p_ig)["status"] == "manual_action"


def test_analytics_page(window):
    from studiovideo.domain.models import PublicationStatus
    w, ctx = window
    c = ctx.concepts.create_concept("Stats")
    yt, tk = ctx.concepts.create_account(Platform.YOUTUBE, "A"), ctx.concepts.create_account(Platform.TIKTOK, "B")
    for a in (yt, tk):
        ctx.concepts.link(c.id, a.id)
    m = ctx.publications.create_master(c.id, "Vidéo 1")
    p1, p2 = ctx.publications.set_destinations(m, [yt.id, tk.id])
    ctx.publications.set_status(p1, PublicationStatus.PUBLISHED, remote_id="VIDabc123")
    ctx.publications.confirm_published(p2, "https://www.tiktok.com/@b/video/9")
    ctx.analytics.record(p1, {"views": 500, "likes": 20}, "api")
    w.go(4)
    pg = w.analytics
    assert pg.pubs.rowCount() == 2
    texts = {pg.pubs.item(i, 2).text(): (pg.pubs.item(i, 3).text(), pg.pubs.item(i, 9).text())
             for i in range(2)}
    assert texts["YouTube · A"] == ("500", "API") and texts["TikTok · B"] == ("—", "—")
    plat = {pg.groups["platform"].item(i, 0).text(): pg.groups["platform"].item(i, 2).text() for i in range(2)}
    assert plat == {"YouTube": "500", "TikTok": "—"}
    # saisie manuelle pour TikTok
    row = next(i for i in range(2) if pg.pubs.item(i, 2).text() == "TikTok · B")
    pid = pg.pubs.item(row, 0).data(0x0100)
    ctx.analytics.record(pid, {"views": 12000, "shares": 40}, "manuel")
    pg.refresh()
    plat = {pg.groups["platform"].item(i, 0).text(): pg.groups["platform"].item(i, 2).text() for i in range(2)}
    assert plat["TikTok"] == "12 k"


def test_detected_zones_are_added_once(window):
    w, ctx = window
    pid = ctx.projects.create(ctx.concepts.list_concepts()[0].id, "Détection")
    w.open_project(pid)
    zones = [{"kind": "logo", "mode": "blur", "x": 0.83, "y": 0.04, "w": 0.14, "h": 0.1, "confidence": 0.6},
             {"kind": "subtitles", "mode": "blur", "x": 0.17, "y": 0.81, "w": 0.64, "h": 0.12, "confidence": 0.4}]
    assert w.studio.panel.add_detected(zones) == 2
    assert w.studio.panel.add_detected(zones) == 0
    assert [m["mode"] for m in w.studio.tl.video.masks] == ["blur", "blur"]


def test_preview_aux_tracks_follow_video(qtbot):
    """Synchronisation du doublage dans l'aperçu, avec des lecteurs simulés (pas de carte son en CI)."""
    from studiovideo.edit.model import Timeline
    from studiovideo.ui import montage_player as mpmod

    class FakeOut:
        def __init__(self):
            self.v = 1.0

        def setVolume(self, v):
            self.v = v

        def volume(self):
            return self.v

    class FakePlayer:
        PlayingState, PausedState = 1, 2

        def __init__(self):
            self.src, self.pos, self.state = None, 0, 0

        def setSource(self, url):
            self.src = url.toLocalFile() or None

        def setPosition(self, ms):
            self.pos = ms

        def position(self):
            return self.pos

        def play(self):
            self.state = 1

        def pause(self):
            self.state = 2

        def stop(self):
            self.state = 0

        def playbackState(self):
            return self.state

    p = mpmod.MontagePlayer()
    qtbot.addWidget(p)
    p.player, p.audio = FakePlayer(), FakeOut()
    p.aux = [(FakePlayer(), FakeOut()), (FakePlayer(), FakeOut())]
    p.vol = type("V", (), {"value": lambda self: 100})()
    tl = Timeline()
    tl.add_segment(1, 10, 20)
    tl.voice.mode, tl.voice.language = "dub", "en"
    p.track_resolver = lambda aid: {"dub": "/x/dub.wav", "voice": None, "music": None}
    p.set_timeline(tl, {1: {"path": "/x/v.mp4", "width": 320, "height": 240}})
    p.loaded_asset, p._pending = 1, None        # le lecteur simulé n'émet pas « média chargé »
    p.seek(3.0)
    dub, dout = p.aux[0]
    assert dub.src == "/x/dub.wav" and dub.pos == 13000                 # même instant du média source
    assert p.audio.volume() == 0.0 and dout.volume() == 1.0             # voix d'origine coupée
    assert "doublage" in p.note.text()
    p._playing = True
    p.seek(4.0, keep_playing=True)
    assert dub.state == 1
    dub.pos = 14500                                                      # dérive de 500 ms
    p._on_pos(14000)
    assert dub.pos == 14000                                              # recalée
    p.pause()
    assert dub.state == 2
    tl.voice.mode = "original"
    p.set_timeline(tl, {1: {"path": "/x/v.mp4", "width": 320, "height": 240}})
    assert p.audio.volume() == 1.0 and dub.src is None                   # retour à la voix d'origine


def test_translation_guided_project(window, tmp_path):
    from test_translate import tr
    from studiovideo.domain.models import ProjectMode
    from studiovideo.transcribe.model import TSegment, Transcript
    w, ctx = window
    pid = ctx.projects.create(ctx.concepts.list_concepts()[0].id, "Trad guidée", ProjectMode.TRANSLATION)
    w.open_project(pid)
    st = w.studio
    assert not st.guide.isHidden() and st.guide.next_step().key == "import"
    con = ctx.db.connect()
    con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, audio_tracks, "
                "duration_s, width, height) VALUES (1,?,'video','x.mp4','x.mp4','fg',1,8,640,360)", (pid,))
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    st.refresh()
    assert st.guide.next_step().key == "transcribe"
    ctx.transcripts.store_original(aid, tr())
    st.refresh()
    assert st.guide.next_step().key == "translate"
    ctx.transcripts.store_translation(aid, Transcript([TSegment(s.start, s.end, s.text.upper(), [], s.speaker)
                                                      for s in tr().segments], "en", "t"))
    st.refresh()
    assert st.guide.next_step().key == "build"
    assert {s.key: s.done for s in st.guide.steps}["dub"] is False      # facultatif
    st.setup_translation(aid, "en")
    assert st.tl.segments and st.tl.subtitles.language == "en" and st.tl.subtitles.cues
    assert any("BONJOUR" in c.text for c in st.tl.subtitles.cues)
    assert st.tl.voice.mode == "original"                                # pas de doublage : voix d'origine
    assert st.guide.next_step().key == "export"
    assert ctx.projects.load_data(pid)["target_lang"] == "en"


def test_campaign_project_flow(window, tmp_path):
    from studiovideo.domain.models import ProjectMode
    from studiovideo.edit import campaign as cp
    from studiovideo.edit.model import Timeline
    from studiovideo.edit.subtitles import Cue
    from studiovideo.transcribe.model import TSegment, Transcript
    from studiovideo.ui.campaign_dialog import BriefDialog, ComplianceDialog
    from studiovideo.ui.publication_dialog import PublicationDialog
    w, ctx = window
    c = ctx.concepts.create_concept("Marque")
    tk = ctx.concepts.create_account(Platform.TIKTOK, "Marque")
    ctx.concepts.link(c.id, tk.id)
    pid = ctx.projects.create(c.id, "Lancement", ProjectMode.CAMPAIGN)
    w.open_project(pid)
    st = w.studio
    assert st.guide.next_step().key == "brief"
    d = BriefDialog(ctx, pid)
    d.name.setText("Lancement Éclair")
    d.aspects["9:16"].setChecked(True)
    d.max_s.setValue(60)
    d.subs.setChecked(True)
    d.req.setText("Boisson Éclair")
    d.forb.setText("concurrent")
    d.tags.setText("#eclair")
    d.disc.setText("#pub")
    d._save()
    b = ctx.campaigns.brief(pid)
    assert b.required_hashtags == ["eclair"] and b.aspects == ["9:16"]
    con = ctx.db.connect()
    con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, audio_tracks, "
                "duration_s, width, height) VALUES (1,?,'video','s.mp4','s.mp4','fc',1,90,1920,1080)", (pid,))
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("INSERT INTO clip(project_id, source_asset_id, name, start_s, end_s) VALUES (?,?,'Clip 1',0,30)",
                (pid, aid))
    ctx.transcripts.store_original(aid, Transcript([TSegment(1, 4, "Goûtez la boisson éclair"),
                                                    TSegment(40, 45, "le concurrent")], "fr"))
    good, bad = tmp_path / "good.mp4", tmp_path / "bad.mp4"
    for f in (good, bad):
        f.write_bytes(b"x")
    tl = Timeline()
    tl.add_segment(aid, 0, 30)
    tl.subtitles.cues, tl.subtitles.enabled = [Cue(0, 2, "Goûtez")], True
    ctx.montage.record_export(pid, tl, {"path": str(good), "duration": 30.0}, None)
    tl2 = Timeline()
    tl2.set_aspect("16:9", 1080)
    tl2.add_segment(aid, 35, 50)
    ctx.montage.record_export(pid, tl2, {"path": str(bad), "duration": 15.0}, None)
    st.refresh()
    steps = {s.key: s.done for s in st.guide.steps}
    assert steps["brief"] and steps["import"] and steps["clips"] and steps["export"] and not steps["check"]
    rep = {r["path"]: r for r in ctx.campaigns.report(pid)}
    assert rep[str(good)]["summary"] == "✓ Conforme"
    bad_rules = {c.rule for c in rep[str(bad)]["checks"] if c.ok is False}
    assert {"Format", "Sous-titres", "Mot requis « Boisson Éclair »", "Mot interdit « concurrent »"} <= bad_rules
    dlg = ComplianceDialog(ctx, pid)
    assert dlg.table.rowCount() == 2 and "Lancement Éclair" in dlg.text_report()
    # publication de la vidéo conforme : le brief contrôle le texte
    assert st.guide.next_step().key == "check"
    vid = rep[str(good)]["variant_id"]
    pd = PublicationDialog(ctx, variant_id=vid)
    pd.title.setText("Nouveau goût")
    pd.tags.setText("#eclair")
    pd._check()
    assert "Mention de partenariat" in pd.warn.text()
    pd.tags.setText("#eclair #pub")
    pd._check()
    assert "Brief" not in pd.warn.text()


def test_original_creation_flow(window):
    import time

    from studiovideo.domain.models import ProjectMode
    from studiovideo.edit.original import Scene
    from studiovideo.ui.original_dialog import OriginalDialog
    w, ctx = window
    pid = ctx.projects.create(ctx.concepts.list_concepts()[0].id, "Idée", ProjectMode.ORIGINAL)
    w.open_project(pid)
    st = w.studio
    assert st.guide.next_step().key == "script"
    d = OriginalDialog(ctx, pid, 360, 640)
    d.text.setPlainText("Le café vient d'Éthiopie. Il est bu partout. Deux milliards de tasses chaque jour !")
    d._split_text()
    assert d.table.rowCount() >= 1
    d._set_scenes([Scene("Le café vient d'Éthiopie.", "Le café"), Scene("Deux milliards de tasses.", "", "", 2.5)])
    d._create()
    assert d.params["scenes"][0]["text"] == "Le café" and d.params["tts"] is None
    jid = ctx.jobs.submit("create_original", "Création", d.params)
    st._jobs[jid] = "create_original"
    end = time.time() + 60
    while ctx.jobs.get(jid).status in ("pending", "running") and time.time() < end:
        time.sleep(0.05)
    st.on_job(ctx.jobs.get(jid))
    assert ctx.jobs.get(jid).status == "succeeded", ctx.jobs.get(jid).error
    assert len(st.tl.segments) == 1 and [t.text for t in st.tl.texts] == ["Le café"]
    assert st.tl.subtitles.enabled and any("tasses" in c.text for c in st.tl.subtitles.cues)
    assert st.guide.next_step().key == "export"


def test_settings_tab_shortcuts(window):
    w, _ = window
    sp = w.settings_page
    sp.show_models()
    assert sp.tabs.tabText(sp.tabs.currentIndex()) == "Matériel et moteurs"
    sp.show_about()
    assert sp.tabs.tabText(sp.tabs.currentIndex()) == "À propos"


def test_reframe_flow(window, monkeypatch):
    import time

    from studiovideo.edit import reframe
    w, ctx = window
    pid = ctx.projects.create(ctx.concepts.list_concepts()[0].id, "Recadrage")
    con = ctx.db.connect()
    con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, audio_tracks, "
                "duration_s, width, height) VALUES (1,?,'video',?,'v.mp4','fr',0,8,1280,720)", (pid, __file__))
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    monkeypatch.setattr(reframe, "analyze", lambda *a, **k: {"keys": [(0.0, 0.3), (8.0, 0.7)], "frames": 32,
                                                             "with_face": 32})
    w.open_project(pid)
    st = w.studio
    st.tl.add_segment(aid, 0, 8)
    st.tl.format.fit = "blur"
    st._reframe(True)
    assert st.tl.format.fit == "fill"
    jid = max(st._jobs)
    end = time.time() + 10
    while ctx.jobs.get(jid).status in ("pending", "running") and time.time() < end:
        time.sleep(0.05)
    st.on_job(ctx.jobs.get(jid))
    assert st.tl.segments[0].reframe == [[0.0, 0.3], [8.0, 0.7]]
    assert "1/1" in st.panel.reframe_note.text()
    st._reframe(False)
    assert st.tl.segments[0].reframe == []


def test_getting_started_card(window):
    w, ctx = window
    w.go(0)
    items = w.dashboard.setup_items()
    assert items[0][1] is True                      # le fixture a créé un compte
    assert not all(done for _, done, _, _ in items)
    w.dashboard.open_settings.emit("Publication")
    assert w.settings_page.tabs.tabText(w.settings_page.tabs.currentIndex()) == "Publication"
    w.dashboard._hide_start()
    assert ctx.settings.get("hide_getting_started") is True


def test_propose_copy_in_publication(window, tmp_path, monkeypatch):
    from test_copywriter import TEXT
    from studiovideo.edit.model import Timeline
    from studiovideo.transcribe.model import TSegment, Transcript
    from studiovideo.ui.publication_dialog import PublicationDialog
    w, ctx = window
    c = ctx.concepts.list_concepts()[0]
    pid = ctx.projects.create(c.id, "Copie")
    con = ctx.db.connect()
    con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, audio_tracks, "
                "duration_s) VALUES (1,?,'video','x','x.mp4','fcp',1,30)", (pid,))
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    ctx.transcripts.store_original(aid, Transcript([TSegment(0, 10, TEXT)], "fr"))
    tl = Timeline()
    tl.add_segment(aid, 0, 10)
    vid = ctx.montage.record_export(pid, tl, {"path": str(tmp_path / "o.mp4"), "duration": 10}, None)
    d = PublicationDialog(ctx, variant_id=vid)
    d._propose_copy()
    assert d.title.text().startswith("Aujourd'hui") and "#Citron" in d.tags.text()
    assert "relisez" in d.warn.text()


def test_import_project_opens_it(window, tmp_path, monkeypatch):
    import time

    from test_archive import _setup
    from studiovideo.services import archive
    from PySide6.QtWidgets import QMessageBox
    w, ctx = window
    pid, aid, vid = _setup(ctx, tmp_path)
    archive.export_project(ctx, pid, tmp_path / "a.studioproj")
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    jid = ctx.jobs.submit("import_project", "Import", {"path": str(tmp_path / "a.studioproj"),
                                                       "media_dir": str(tmp_path / "Videos")})
    end = time.time() + 30
    while ctx.jobs.get(jid).status in ("pending", "running") and time.time() < end:
        time.sleep(0.05)
    w._on_project_imported(ctx.jobs.get(jid))
    assert w.studio.project_id == ctx.jobs.get(jid).result["project_id"] and len(w.studio.tl.segments) == 1


def test_remove_silences_regenerates_subtitles(window):
    from studiovideo.edit.subtitles import Cue
    from studiovideo.transcribe.model import TSegment, Transcript, Word
    w, ctx = window
    pid = ctx.projects.create(ctx.concepts.list_concepts()[0].id, "Silences")
    con = ctx.db.connect()
    con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, audio_tracks, "
                "duration_s) VALUES (1,?,'video','x','x.mp4','fsil',1,10)", (pid,))
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    ctx.transcripts.store_original(aid, Transcript([TSegment(0, 1, "un", [Word(0.2, 0.8, " un")]),
                                                    TSegment(6, 7, "deux", [Word(6.2, 6.8, " deux")])], "fr"))
    w.open_project(pid)
    st = w.studio
    seg = st.tl.add_segment(aid, 0, 8)
    st.tl.subtitles.cues = [Cue(6.2, 6.8, "deux")]
    st.tl.subtitles.enabled = True
    res = st.apply_silences({seg.id: [(0.8, 6.2)]})
    assert res["removed"] > 4 and len(st.tl.segments) == 2
    deux = next(c for c in st.tl.subtitles.cues if "deux" in c.text)
    assert deux.start < 2.0                                      # recalé après la coupe
    st.undo()
    assert len(st.tl.segments) == 1
    res = st.apply_silences({st.tl.segments[0].id: [(0.8, 6.2)]}, punch=True)
    assert res["zoomed"] == 1 and [s.zoom for s in st.tl.segments] == [1.0, 1.12]
    st.panel.sel_segment = st.tl.segments[1].id
    st.panel._load_segment()
    assert st.panel.seg_zoom.value() == 112
    st.panel.seg_zoom.setValue(130)
    assert st.tl.segments[1].zoom == 1.3


def test_shortcuts_dialog(window):
    from studiovideo.ui.shortcuts import GROUPS, ShortcutsDialog
    w, _ctx = window
    d = ShortcutsDialog(w)
    texts = {d.table.item(r, 0).text() for r in range(d.table.rowCount())}
    assert {"Ctrl+N", "S", "I / O", "Studio (montage)"} <= texts
    assert d.table.rowCount() == sum(len(i) + 1 for _g, i in GROUPS)


def test_studio_draws_waveform(window, tmp_path):
    import subprocess
    import time

    from PySide6.QtWidgets import QApplication

    from studiovideo.media import ffmpeg
    if not ffmpeg.available():
        pytest.skip("FFmpeg absent")
    w, ctx = window
    vid = tmp_path / "w.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x120:d=3:r=25",
                    "-f", "lavfi", "-i", "sine=f=300:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(vid)], check=True)
    pid = ctx.projects.create(ctx.concepts.list_concepts()[0].id, "Onde")
    aid = ctx.importer().import_file(vid, project_id=pid).asset_id
    w.open_project(pid)
    st = w.studio
    st.tl.add_segment(aid, 0, 3)
    st._changed(panel=True)
    end = time.time() + 30
    while aid not in st.timeline.canvas.peaks and time.time() < end:
        QApplication.processEvents()
        time.sleep(0.05)
    assert aid in st.timeline.canvas.peaks
    st.timeline.canvas.grab()
    assert st.timeline.canvas._cols                            # colonnes réellement calculées au dessin
