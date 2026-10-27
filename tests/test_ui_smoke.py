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
    assert [(g.asset_id, g.src_in, g.src_out) for g in st.tl.segments] == [(aid, 0.0, 6.0)]   # vidéo placée d'office
    assert ctx.montage.load(pid).duration == 6.0
    st.tl.segments = []
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
    assert st.stack.currentWidget() is st.tr_view and st.topbar.isVisibleTo(st)   # écran Traduction, en-tête commun
    st._toggle_full(True)                                       # « Tous les outils » : éditeur, étapes guidées
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
    assert sp.tabs.tabText(sp.tabs.currentIndex()) == "Général"                 # « À propos » est dans Général


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
    st.tl.segments = []                                  # vidéo placée d'office : on repart à vide
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
    st.tl.segments = []                                  # vidéo placée d'office : on repart à vide
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


def test_timeline_drag_trims_and_moves(window):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent
    w, ctx = window
    pid = ctx.projects.create(ctx.concepts.list_concepts()[0].id, "Glisser")
    con = ctx.db.connect()
    con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, audio_tracks, "
                "duration_s) VALUES (1,?,'video','x','x.mp4','fdrag',1,20)", (pid,))
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    w.open_project(pid)
    st = w.studio
    st.tl.segments = []                                  # vidéo placée d'office : on repart à vide
    st.tl.add_segment(aid, 2, 6)
    st.tl.add_segment(aid, 10, 14)
    txt = st.tl.add_text("Titre", 1.0, 3.0)
    st._changed(panel=True)
    c = st.timeline.canvas
    c.resize(1200, c.minimumHeight())

    def drag(x0, x1, y):
        for typ, x, btns in ((QEvent.MouseButtonPress, x0, Qt.LeftButton), (QEvent.MouseMove, x1, Qt.LeftButton),
                             (QEvent.MouseButtonRelease, x1, Qt.NoButton)):
            ev = QMouseEvent(typ, QPointF(x, y), QPointF(x, y), Qt.LeftButton, btns, Qt.NoModifier)
            {QEvent.MouseButtonPress: c.mousePressEvent, QEvent.MouseMove: c.mouseMoveEvent,
             QEvent.MouseButtonRelease: c.mouseReleaseEvent}[typ](ev)

    y0 = c.track_rect(0).center().y()
    drag(c.x_of(4.0) - 2, c.x_of(3.0), y0)                      # bord droit du 1er extrait : 1 s plus court
    assert st.tl.segments[0].src_out == pytest.approx(5.0, abs=0.05) and st.tl.duration == pytest.approx(7, abs=0.05)
    drag(c.x_of(st.tl.duration) - 2, c.x_of(st.tl.duration + 50), y0)     # limité par la durée du média
    assert st.tl.segments[1].src_out == 20
    drag(c.x_of(0) + 1, c.x_of(1.0), y0)                         # bord gauche : début de source avancé
    assert st.tl.segments[0].src_in == pytest.approx(3.0, abs=0.05)
    y1 = c.track_rect(1).center().y()
    drag(c.x_of(2.0), c.x_of(4.0), y1)                           # texte déplacé
    assert (txt.start, txt.end) == (pytest.approx(3.0, abs=0.05), pytest.approx(5.0, abs=0.05))
    n = len(st._history)
    st.undo()
    assert st.tl.texts[0].start == pytest.approx(1.0, abs=0.05) and len(st._history) == n - 1


def test_watch_folder_imports(window, tmp_path):
    import os
    import subprocess
    import time

    from studiovideo.media import ffmpeg
    if not ffmpeg.available():
        pytest.skip("FFmpeg absent")
    w, ctx = window
    d = tmp_path / "surveille"
    d.mkdir()
    v = d / "rush.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x120:d=1:r=25",
                    "-pix_fmt", "yuv420p", str(v)], check=True)
    os.utime(v, (time.time() - 60, time.time() - 60))
    assert w.check_watch_folder() == 0                            # aucun dossier choisi
    w.settings_page._set_watch(str(d)) if hasattr(w, "settings_page") else ctx.settings.set("watch_folder", str(d))
    assert w.check_watch_folder() == 0 and w.check_watch_folder() == 1
    end = time.time() + 20
    while not ctx.db.query_one("SELECT 1 FROM source_asset WHERE path = ?", (str(v),)) and time.time() < end:
        time.sleep(0.05)
    assert ctx.db.query_one("SELECT provenance FROM source_asset WHERE path = ?", (str(v),)) is not None
    assert w.check_watch_folder() == 0


def test_segment_fades_in_panel_and_preview(window):
    w, ctx = window
    pid = ctx.projects.create(ctx.concepts.list_concepts()[0].id, "Fondus")
    con = ctx.db.connect()
    con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, audio_tracks, "
                "duration_s, width, height) VALUES (1,?,'video','x','x.mp4','ffade',1,10,320,180)", (pid,))
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    w.open_project(pid)
    st = w.studio
    st.tl.segments = []                                  # vidéo placée d'office : on repart à vide
    seg = st.tl.add_segment(aid, 0, 4)
    st._changed(panel=True)
    st.panel.sel_segment = seg.id
    st.panel._load_segment()
    st.panel.seg_fin.setValue(1.0)
    assert seg.fade_in == 1.0
    st.mplayer.seek(0.5)
    assert st.mplayer.view.fader.opacity() == pytest.approx(0.5, abs=0.05)
    st.mplayer.seek(2.0)
    assert st.mplayer.view.fader.opacity() == pytest.approx(0.0, abs=0.01)


def test_search_in_speech_and_open(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from studiovideo.transcribe.model import TSegment, Transcript
    w, ctx = window
    con = ctx.db.connect()
    con.execute("INSERT INTO source_asset(workspace_id, kind, path, file_name, fingerprint, audio_tracks, duration_s) "
                "VALUES (1,'video','x','interview.mp4','fspeech',1,60)")
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    ctx.transcripts.store_original(aid, Transcript([TSegment(0, 4, "Bonjour à tous."),
                                                    TSegment(30, 34, "Parlons de l'élection municipale.")], "fr"))
    lib = w.library
    lib.in_speech.setChecked(True)
    lib.search.setText("election")                               # sans accent : trouvé quand même
    assert lib.stack.currentIndex() == 2 and lib.speech.count() == 1
    assert "0:30" in lib.speech.item(0).text()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    seeks = []
    monkeypatch.setattr(w.studio, "_seek_source", lambda t: seeks.append(t))
    lib._open_hit(lib.speech.item(0))
    pid = ctx.library.get(aid)["project_id"]
    assert pid and w.studio.project_id == pid and seeks == [30.0]
    lib.in_speech.setChecked(False)
    assert lib.stack.currentIndex() == 1


def test_projects_dialog(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    from studiovideo.ui.projects_dialog import ProjectsDialog
    w, ctx = window
    cid = ctx.concepts.list_concepts()[0].id
    a = ctx.projects.create(cid, "Alpha")
    b = ctx.projects.create(cid, "Beta")
    w.open_project(a)
    d = ProjectsDialog(ctx, w, current_id=a)

    def select(pid):
        for i in range(d.table.rowCount()):
            if d.table.item(i, 0).data(0x0100) == pid:
                d.table.selectRow(i)
                return
        raise AssertionError(pid)
    select(b)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a_, **k: ("Gamma", True))
    d._rename()
    assert ctx.projects.get(b)["name"] == "Gamma"
    d._archive()
    assert ctx.projects.get(b)["status"] == "archived" and d.b_arch.text() == "Désarchiver"
    d._duplicate()
    dup = d.selected()
    assert dup not in (a, b) and ctx.projects.get(dup)["name"] == "Gamma (copie)"
    monkeypatch.setattr(QMessageBox, "question", lambda *a_, **k: QMessageBox.Yes)
    d._delete()
    assert ctx.projects.get(dup) is None
    select(a)
    monkeypatch.setattr(QMessageBox, "information", lambda *a_, **k: None)
    d._delete()                                                  # projet ouvert : refusé
    assert ctx.projects.get(a) is not None
    w.studio.refresh()


def test_dock_parcours_and_drafts(window):
    from studiovideo.ui import theme
    w, ctx = window
    assert set(w.dock.buttons) == set(theme.PARCOURS) | {"export"}
    w.go(0)
    w.start_parcours("translation")                             # aucun projet du parcours : projet vierge ouvert
    pid = w.studio.project_id
    p = ctx.projects.get(pid)
    assert p["mode"] == "translation" and p["concept_name"] == "Brouillons"
    assert w.pages.currentWidget() is w.studio
    assert w.studio.tl.format.aspect == theme.DEFAULT_ASPECT["translation"]
    assert w.dock.buttons["translation"].isChecked()
    assert theme.current["accent"].lower() == theme.PARCOURS["translation"][2].lower()
    w.start_parcours("translation")                             # même parcours : rien ne change
    assert w.studio.project_id == pid
    assert set(w.studio.format_pick.buttons) == {"16:9", "1:1", "9:16"}     # format en trois icônes
    w.studio.format_pick.buttons["1:1"].click()
    assert w.studio.tl.format.aspect == "1:1" and ctx.projects.is_untouched(pid)
    assert w.studio.format_pick.value() == "1:1"
    w.start_parcours("original")                                # un projet traverse tous les parcours
    assert w.studio.project_id == pid and ctx.projects.get(pid)["mode"] == "original"
    assert w.dock.buttons["original"].isChecked() and w.studio.tl.format.aspect == "1:1"
    bid = pid
    ctx.db.connect().execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, "
                             "audio_tracks, duration_s, width, height) VALUES (1,?,'video','d.mp4','d.mp4','fd',1,9,"
                             "640,360)", (bid,))
    w.start_parcours("smart_clipping")                          # même projet, même vidéo, autre interface
    assert w.studio.project_id == bid and w.studio.stack.currentWidget() is w.studio.clip_view
    assert "d.mp4" in w.studio.clip_view.meta.text()
    w.start_parcours("translation")
    assert w.studio.project_id == bid and w.studio.tr_view.left.currentIndex() == 1
    w.go(0)
    w.start_parcours("original")                                # hors Studio : le projet ouvert reprend
    assert w.studio.project_id == bid and ctx.projects.get(bid)["mode"] == "original"
    w.studio.new_blank.emit("original")                            # bouton « + » : projet vierge du même parcours
    nid = w.studio.project_id
    assert nid != bid and ctx.projects.get(nid)["mode"] == "original" and ctx.projects.is_untouched(nid)
    w.close()                                                   # fermeture : projet vierge intact non conservé
    assert ctx.projects.get(nid) is None and ctx.projects.get(bid) is not None
    w.show()
    w.go(0)                                                     # hors Studio : plus de parcours actif
    assert not w.dock.buttons["original"].isChecked()
    w.drawer.navigate.emit(6)
    assert w.pages.currentWidget() is w.concepts_page


def test_workspaces_show_only_their_tools(window):
    from studiovideo.ui.pages.studio import LAYOUTS
    w, ctx = window
    st = w.studio
    w.start_parcours("free_edit")                               # tous les outils
    assert st.timeline_box.isVisibleTo(st) and st.panel.isVisibleTo(st) and not st.export_card.isVisibleTo(st)
    assert all(st.panel.isTabVisible(i) for i in range(st.panel.count()))
    assert not st.full_btn.isVisibleTo(st) and st.guide.isHidden()
    assert list(w.dock.buttons) == ["free_edit", "smart_clipping", "translation", "original", "texts", "export"]
    assert w.dock.buttons["original"].dimmed and not w.dock.buttons["texts"].dimmed   # Création originale grisée
    w.start_parcours("smart_clipping")                          # Clipping : découpage seulement
    assert st.stack.currentWidget() is st.clip_view and not st.full_btn.isVisibleTo(st)
    w.start_parcours("translation")
    assert st.stack.currentWidget() is st.tr_view and st.guide.isHidden()
    assert st.full_btn.isVisibleTo(st) and "Tous les outils" in st.full_btn.toolTip()
    st.tr_view.open_editor.emit()                               # « Tous les outils » : montage complet
    assert st.stack.currentWidget() is st.editor and st.timeline_box.isVisibleTo(st)
    assert not st.export_card.isVisibleTo(st)                   # l'export a son propre onglet
    assert "Revenir" in st.full_btn.toolTip()
    st.full_btn.click()                                         # retour à l'écran simplifié
    assert st.stack.currentWidget() is st.tr_view
    w.start_parcours("texts")                                   # Textes : textes, sous-titres et export
    assert st.stack.currentWidget() is st.editor and st.guide.isHidden()
    assert [st.panel.tab_name(i) for i in range(st.panel.count()) if st.panel.isTabVisible(i)] == \
        ["Textes", "Sous-titres", "Export"]
    assert [i for i in range(st.left_tabs.count()) if st.left_tabs.isTabVisible(i)] == [0]     # import à gauche
    assert not st.timeline_box.isVisibleTo(st)
    assert not any(b.isVisibleTo(st) for b in (st.full_btn, st.undo_btn, st.redo_btn))
    assert st.mplayer.note.text() == ""                         # plus de phrase sous l'aperçu
    for mode in ("original",):
        w.start_parcours(mode)
        lay = LAYOUTS[mode]
        assert st.timeline_box.isVisibleTo(st) == lay["timeline"], mode
        assert st.export_card.isVisibleTo(st) == lay["export"], mode
        assert st.panel.isVisibleTo(st) == bool(lay["panel"]), mode
        assert [i for i in range(st.left_tabs.count()) if st.left_tabs.isTabVisible(i)] == sorted(lay["left"]), mode
        assert not st.guide.isHidden(), mode                    # chaque parcours spécialisé est guidé
        assert st.full_btn.isVisibleTo(st), mode
        st.full_btn.click()                                     # « Tous les outils » : montage complet
        assert st.timeline_box.isVisibleTo(st) and st.panel.isVisibleTo(st), mode
        assert all(st.panel.isTabVisible(i) for i in range(st.panel.count())), mode
        st.full_btn.click()
        assert st.timeline_box.isVisibleTo(st) == lay["timeline"], mode
    st.viewer_tabs.setCurrentIndex(1)                           # onglet masqué : le Montage reste affiché
    assert st.viewer_tabs.currentIndex() == 0
    assert st.guide.next_step().key == "script"


def test_import_bar_same_in_every_tab_and_attach_existing_video(window):
    w, ctx = window
    st = w.studio
    w.start_parcours("free_edit")
    bar = (st.import_btn, st.link_edit, st.media_btn)
    assert st.import_btn.text() == "" and st.export_quick.text() == ""         # icônes seules
    assert st.export_quick.property("kind") == "export"         # fond rouge de l'onglet Export
    assert st.link_edit.placeholderText() == "" and not st.link_icon.icon().isNull()   # icône de lien seule
    assert not hasattr(st, "doc_action") and not hasattr(st, "format_combo")    # plus de menu « … »
    assert st.jobs_btn.isVisibleTo(st)                           # tâches : en haut
    st._autosave()
    assert st.save_lbl.text() != "Enregistré"
    w.start_parcours("smart_clipping")
    assert st.clip_view.body.currentIndex() == 0                # pas encore de vidéo : zone d'import
    con = ctx.db.connect()
    con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, audio_tracks, "
                "duration_s, width, height) VALUES (1,NULL,'video','e.mp4','e.mp4','fe',1,90,1920,1080)")
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    st.attach_asset(aid)                                        # vidéo déjà importée (Médias)
    assert st.asset_id == aid and st.clip_view.body.currentIndex() == 1
    assert "e.mp4" in st.clip_view.meta.text()
    assert [(g.asset_id, g.src_out) for g in st.tl.segments] == [(aid, 90)]       # devenue la vidéo du projet
    assert not hasattr(st.tr_view, "link")                      # plus d'import propre à Traduction
    for mode in ("translation", "original", "texts", "free_edit", "export"):
        w.start_parcours(mode)
        assert all(x.isVisibleTo(st) for x in bar) == (mode != "export"), mode   # Export : pas d'import
        assert st.project_video()["id"] == aid, mode             # la même vidéo partout
        if mode == "original":
            assert st.media_list.isVisibleTo(st)                # médias importés visibles (images des scènes)
    assert st.stack.currentWidget() is st.export_view and w.dock.buttons["export"].isChecked()
    assert not st.export_quick.isVisibleTo(st)


def test_transcribe_button_sends_spoken_language(window, monkeypatch):
    from studiovideo.transcribe import engines
    w, ctx = window
    st = w.studio
    w.start_parcours("translation")
    con = ctx.db.connect()
    con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, audio_tracks, "
                "duration_s, width, height) VALUES (1,?,'video','t.mp4','t.mp4','ft',1,60,1920,1080)",
                (st.project_id,))
    st.refresh()
    tp = st.transcript
    assert tp.asset_id and tp.lang == ""                        # langue affichée : texte d'origine
    monkeypatch.setattr(engines, "available", lambda: True)
    tp.model.clear()
    tp.model.addItem("Small", "small")
    tp.src_lang.setCurrentIndex(tp.src_lang.findData("fr"))
    sent = {}
    monkeypatch.setattr(ctx.jobs, "submit", lambda kind, label_, params, **k: sent.update(kind=kind, **params) or 1)
    tp._transcribe()
    assert sent["kind"] == "transcribe" and sent["language"] == "fr" and sent["model"] == "small"


def test_color_picker_popover(window):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    from studiovideo.ui.color_picker import ColorButton
    w, _ = window
    b = ColorButton("#FF0000")
    got = []
    b.picked.connect(got.append)
    b.setParent(w)
    b.show()
    b.click()
    pop = b.pop
    assert pop.isVisible() and pop.parent() is w                # pas d'héritage du fond coloré de la pastille
    pop.hex.setText("#00ff00")
    pop._hex_typed("#00ff00")
    assert b.value == "#00FF00" and got[-1] == "#00FF00"        # changement en direct
    pop.sv._at(pop.sv.rect().topLeft())                         # coin haut gauche : blanc
    assert b.value == "#FFFFFF"
    QApplication.sendEvent(pop, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert not pop.isVisible() and b.value == "#FF0000"         # Échap : couleur de départ


def test_purge_untouched_blank_projects(tmp_path):
    ctx = build_context(tmp_path / "data")
    blank = ctx.projects.quick_create(ctx.concepts, "free_edit", blank=True)
    kept = ctx.projects.quick_create(ctx.concepts, "free_edit", blank=True)
    ctx.projects.rename(kept, "Mon film")                       # renommé : c'est une modification
    normal = ctx.projects.quick_create(ctx.concepts, "free_edit")
    assert ctx.projects.purge_untouched() == 1
    assert ctx.projects.get(blank) is None
    assert ctx.projects.get(kept) is not None and ctx.projects.get(normal) is not None
    ctx.close()


def test_drop_video_creates_draft_with_auto_clips(window, tmp_path):
    import subprocess
    import time

    from PySide6.QtWidgets import QApplication

    from studiovideo.media import ffmpeg
    if not ffmpeg.available():
        pytest.skip("FFmpeg absent")
    w, ctx = window
    v = tmp_path / "Mon interview.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x120:d=2:r=25",
                    "-f", "lavfi", "-i", "sine=f=300:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(v)], check=True)
    w.go(0)
    pid = w.drop_files([str(v)])
    p = ctx.projects.get(pid)
    assert p["name"] == "Mon interview" and p["mode"] == "smart_clipping"
    end = time.time() + 30
    job = None
    while time.time() < end:
        QApplication.processEvents()
        job = next((j for j in ctx.jobs.list(limit=20) if j.kind == "auto_clips"), None)
        if job and job.status in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    assert job is not None and job.status == "succeeded", job and job.error
    from studiovideo.transcribe import engines
    if not (engines.available() and engines.installed_models(ctx.paths.models)):
        assert "modèle de transcription" in job.result["skipped"]          # signalé, jamais simulé


def test_separated_music_goes_to_music_track(window, tmp_path):
    import shutil
    import subprocess

    from studiovideo.media import ffmpeg
    if not ffmpeg.available():
        pytest.skip("FFmpeg absent")
    w, ctx = window
    v = tmp_path / "clip.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x120:d=3:r=25",
                    "-f", "lavfi", "-i", "sine=f=300:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(v)], check=True)
    pid = ctx.projects.quick_create(ctx.concepts, "free_edit")
    aid = ctx.importer().import_file(v, project_id=pid).asset_id
    w.open_project(pid)
    st = w.studio
    st.tl.segments = []                                          # vidéo placée d'office : on repart à vide
    st.tl.add_segment(aid, 1.0, 3.0)
    st._changed(panel=True)
    d = ctx.stems.directory(aid)
    d.mkdir(parents=True)
    for name, f in (("voice.wav", 300), ("music.wav", 600)):
        subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f={f}:d=3",
                        str(d / name)], check=True)
    ctx.stems.mark_done(aid, "kim_vocal_2")
    assert st.apply_stems(aid)
    m = st.tl.music
    assert m.asset_id and m.src_offset == 1.0 and not m.duck and st.tl.voice.isolate
    assert ctx.library.get(m.asset_id)["file_name"] == "Musique · clip.wav"
    st.undo()
    assert not st.tl.music.asset_id


def test_font_picker_and_speakers_menu(window):
    from studiovideo.ui.montage_panel import FontPicker
    w, _ = window
    fp = FontPicker()
    got = []
    fp.fontChanged.connect(got.append)
    assert fp.count() > 0
    idx = max(i for i in range(fp.count()) if fp.itemData(i))   # dernière police (pas un séparateur)
    fp.setCurrentIndex(0 if idx == 0 else idx)
    if idx == 0:
        fp.fontChanged.emit(fp.currentData())
    assert got and got[-1] == fp.currentData()
    fp.set_family("Police Inventée")                        # police absente : ajoutée et choisie
    assert fp.currentData() == "Police Inventée"


def test_double_framing_panel_and_live_preview(window):
    """Double cadrage : réglages dans l'onglet Cadrage, deux vues recadrées dans l'aperçu, vue déplaçable."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtMultimedia import QVideoFrame

    from studiovideo.edit import compose
    w, ctx = window
    pid = ctx.projects.create(ctx.concepts.list_concepts()[0].id, "Débat")
    con = ctx.db.connect()
    for name, fp in (("debat.mp4", "d1"), ("jeu.mp4", "d2")):
        con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, "
                    "audio_tracks, duration_s, width, height) VALUES (1,?,'video',?,?,?,1,8,1920,1080)",
                    (pid, __file__, name, fp))
    a1, a2 = [r["id"] for r in ctx.db.query("SELECT id FROM source_asset WHERE project_id = ? ORDER BY id", (pid,))]
    w.open_project(pid)
    st = w.studio
    st.tl.segments = []                                  # vidéo placée d'office : on repart à vide
    st.tl.add_segment(a1, 0, 8)
    st._changed(panel=True)
    p = st.panel
    assert not (not p.lay_box.isHidden())
    p.lay_kind.setCurrentIndex(p.lay_kind.findData("split"))
    lay = compose.norm(st.tl.video.layout)
    assert lay["kind"] == "split" and (not p.lay_box.isHidden())
    assert not (not p.lay_second.isHidden()) and (not p.lay_swap.isHidden())      # même vidéo : pas de 2e source
    p.lay_ratio.setValue(60)
    p.lay_view.setCurrentIndex(1)
    p.lay_sl["zoom"].setValue(200)
    lay = compose.norm(st.tl.video.layout)
    assert lay["ratio"] == 0.6 and lay["views"][1]["zoom"] == 2.0 and lay["views"][0]["zoom"] == 1.3
    view = st.mplayer.view
    assert view.composite and all(v.outer.isVisible() for v in view.views)
    img = QImage(192, 108, QImage.Format_RGB32)
    img.fill(QColor("red"))
    view.set_frame(0, QVideoFrame(img))                                         # une image décodée, deux vues
    assert not view.views[0].pix.pixmap().isNull() and not view.views[1].pix.pixmap().isNull()
    W, H = st.tl.format.width, st.tl.format.height
    assert view.views[1].rect == (0, int(H * 0.6) // 2 * 2, W, H - int(H * 0.6) // 2 * 2)
    # fidélité : le point source au centre de la vue est celui que FFmpeg place au centre
    SW, SH, x, y = compose.view_crop(1920, 1080, W, view.views[1].rect[3], lay["views"][1])
    m = view.views[1].pix.transform()
    c = m.map(QPointF((x + W / 2) * 192 / SW, (y + view.views[1].rect[3] / 2) * 108 / SH))
    assert abs(c.x() - W / 2) < 1 and abs(c.y() - (view.views[1].rect[1] + view.views[1].rect[3] / 2)) < 1
    before = compose.norm(st.tl.video.layout)["views"][0]["cx"]
    view._drag = (0, QPointF(500, 300), *compose.view_crop(1920, 1080, W, view.views[0].rect[3],
                                                          lay["views"][0])[2:])

    class Ev:
        def __init__(self, x, y):
            self._p = view.mapFromScene(QPointF(x, y))

        def position(self):
            return QPointF(self._p)
    view.mouseMoveEvent(Ev(400, 300))                                           # glisser vers la gauche
    view.mouseReleaseEvent(None)
    after = compose.norm(st.tl.video.layout)["views"][0]["cx"]
    assert after > before                                                       # le cadre part vers la droite
    assert abs(p.lay_sl["cx"].value() / 100 - compose.norm(st.tl.video.layout)["views"][
        p.lay_view.currentIndex()]["cx"]) < 0.011                               # panneau à jour
    p.lay_kind.setCurrentIndex(p.lay_kind.findData("split2"))
    lay = compose.norm(st.tl.video.layout)
    assert lay["asset2"] == a2 and (not p.lay_second.isHidden()) and (not p.lay_audio.isHidden())
    assert view.second_size == (1920, 1080)
    p.lay_kind.setCurrentIndex(p.lay_kind.findData("single"))
    assert not view.composite and not (not p.lay_box.isHidden())
    st._autosave()
    assert ctx.projects.load_data(pid)["timeline"]["video"]["layout"]["kind"] == "single"


def test_clipping_screen_moments_and_generation(window, monkeypatch):
    """Écran Clipping : critères demandés pendant la transcription (explication au survol), extraits à cocher,
    début et fin à la souris, suivi de la tête sur demande, un projet par extrait coché, puis Export."""
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QContextMenuEvent, QMouseEvent
    from PySide6.QtWidgets import QApplication

    from studiovideo.ai.highlights import Candidate, Request
    from studiovideo.jobs.manager import JobInfo
    from studiovideo.transcribe.model import TSegment, Transcript
    w, ctx = window
    st = w.studio
    w.start_parcours("smart_clipping")
    cv, pid = st.clip_view, st.project_id
    assert "Importe" in cv.b_title.text() and cv.b_action.isVisibleTo(cv)
    assert cv.import_extra() == {"auto_clips": True}            # import : transcription puis extraits, d'office
    assert st._import_extra() == {"auto_clips": True}           # même réglage depuis l'en-tête commun
    assert [lb.text() for lb in cv.step_lbls] == ["Vidéo", "Transcription", "Sélection", "Vidéo"]
    con = ctx.db.connect()
    con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, audio_tracks, "
                "duration_s, width, height) VALUES (1,?,'video',?,'debat.mp4','fd',1,300,1920,1080)", (pid, __file__))
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    st.refresh()
    assert [(g.asset_id, g.src_out) for g in st.tl.segments] == [(aid, 300)]      # vidéo entière dans le montage
    assert cv.body.currentIndex() == 1 and cv.left_stack.currentIndex() == 1   # question des critères
    assert "Politique" in cv.ask_note.text() and not cv.ask_go.isEnabled()     # pas encore transcrite
    assert "rire" in cv.ask_crit.cats["humour"].toolTip() and "contradiction" in cv.ask_crit.cats["debat"].toolTip()
    cv.ask_crit.cats["humour"].setChecked(True)                 # choisi pendant la transcription
    assert ctx.projects.load_data(pid)["clip_criteria"]["categories"] == ["humour"]
    assert cv.crit.cats["humour"].isChecked()                   # même choix dans la colonne de droite
    ctx.transcripts.store_original(aid, Transcript([TSegment(0, 5, "Bonjour", [])], "fr", "t"))
    sent = []
    monkeypatch.setattr(ctx.jobs, "submit", lambda kind, label_, params=None, **k: sent.append((kind, params)) or
                        900 + len(sent))
    cv.refresh()
    assert "Cherche les extraits" in cv.b_title.text() and cv.ask_go.isEnabled()
    cv._find()
    assert sent[-1][0] == "find_highlights" and sent[-1][1]["request"]["categories"] == ["humour"]
    ids = ctx.candidates.replace_proposals(pid, aid, [
        Candidate(start=10, end=40, subject="la dette", category="debat", score=91, rationale="Désaccord net",
                  hook="Ils ne sont pas d'accord"),
        Candidate(start=60, end=95, subject="le chômage", category="politique", score=64, rationale="Chiffres",
                  hook=""),
        Candidate(start=120, end=150, subject="les retraites", category="politique", score=60, rationale="Débat",
                  hook="Et les retraites ?")], Request())
    sent.clear()
    cv.refresh()
    assert list(cv.cards) == ids and cv.left_stack.currentIndex() == 0 and "Coche" in cv.b_title.text()
    assert not cv.gen.isEnabled()
    texts = [lb.text() for lb in cv.cards[ids[0]].findChildren(type(cv.meta))]
    assert "la dette" in texts and not any("/10" in t for t in texts)          # pas de note sur 10
    assert sent == [] and not cv.follow.isChecked()             # suivi de la tête : désactivé par défaut
    assert cv.follow.text() == "" and "parle" in cv.follow.toolTip()         # icône seule, phrase au survol
    lay = cv.player.ctr                                         # à gauche des zones TikTok / Reels, même taille
    assert lay.indexOf(cv.follow) == lay.indexOf(cv.player.zone_btn) - 1
    assert cv.follow.size() == cv.player.zone_btn.size()
    assert not hasattr(cv, "title_edit") and not hasattr(cv, "caption") and not hasattr(cv, "hook")
    assert cv.meta.parent() is not cv.banner.parent()           # « Vidéo source » : en bas de la colonne d'outils
    assert "Vidéo source" in cv.meta.text()
    cv.follow.click()                                           # bouton : analyse des visages
    assert ctx.projects.load_data(pid)["clip_criteria"]["reframe"] is True
    assert [k for k, _ in sent] == ["reframe"] and sent[0][1]["candidate_id"] == ids[0]
    assert "visages" in cv.frame_note.text()
    cv.follow.click()
    cv.cards[ids[1]].box.setChecked(True)                       # cocher = garder
    cv.cards[ids[2]].box.setChecked(True)
    cv.refresh()
    assert cv.gen.isEnabled() and cv.gen.text() == "Créer 2 vidéo(s)" and "Crée 2" in cv.b_title.text()
    cv._select(ids[1])
    assert cv.in_s.value() == 60 and cv.out_s.value() == 95 and cv.fin_lbl.text() == "Fin · 35 s"
    assert cv.player.tl.format.aspect == "9:16" and cv.player.tl.duration == 35   # aperçu du clip seul

    def mouse(kind, x):
        ev = QMouseEvent(kind, QPointF(5, 5), QPointF(x, 5), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(cv.in_s.val, ev)
    mouse(QEvent.MouseButtonPress, 100)                         # glisser vers la droite : le début avance
    mouse(QEvent.MouseMove, 140)
    mouse(QEvent.MouseButtonRelease, 140)
    assert abs(ctx.candidates.get(ids[1])["start_s"] - 62.0) < 1e-6        # 40 px = 20 crans de 0,1 s
    cv.out_s.plus.click()                                       # + : 0,5 s de plus
    assert abs(ctx.candidates.get(ids[1])["end_s"] - 95.5) < 1e-6
    cv.player.t = 3.0                                           # « Début ici » selon la lecture
    cv.here_in.click()
    assert abs(ctx.candidates.get(ids[1])["start_s"] - 65.0) < 1e-6
    ctx.candidates.set_hook(ids[1], "Le chômage en chiffres")
    cv._create()                                                # un projet par extrait coché
    data = ctx.projects.load_data(pid)
    assert data["clip_of"] == ids[1] and data["clip_projects"][str(ids[1])] == pid
    seg = st.tl.segments[0]
    assert (seg.asset_id, seg.src_in, seg.src_out) == (aid, 65.0, 95.5)    # la vidéo du projet = l'extrait
    assert st.tl.texts[0].text == "Le chômage en chiffres" and st.tl.format.aspect == "9:16"
    other = data["clip_projects"][str(ids[2])]
    assert other != pid and ctx.projects.get(other)["name"] == "les retraites"
    ot = ctx.montage.load(other)
    assert (ot.segments[0].src_in, ot.segments[0].src_out) == (120, 150) and ot.texts[0].text == "Et les retraites ?"
    assert [r["state"] for r in ctx.candidates.list(other)] == ["accepted"]      # son extrait, déjà choisi
    cv.refresh()
    assert "prête" in cv.b_title.text() and not cv.gen.isVisibleTo(cv) and cv.next.isVisibleTo(cv)
    assert cv.next.go.text() == "Suivant : Traduction et adaptation"
    tags = {c: [lb.text() for lb in card.findChildren(type(cv.meta))] for c, card in cv.cards.items()}
    assert "Vidéo de ce projet" in tags[ids[1]] and "Vidéo créée" in tags[ids[2]]
    cv.out_s.minus.click()                                      # l'extrait réglé : la vidéo du projet suit
    assert st.tl.segments[0].src_out == 95.0
    cv.on_job(JobInfo(id=950, kind="reframe", label="", status="succeeded", progress=1, message="", error="",
                      critical=False, created_at="", result={"keys": [[65.0, 0.3], [95.0, 0.7]]},
                      params={"project_id": pid, "candidate_id": ids[1], "src_in": 65.0, "src_out": 95.0,
                              "asset_id": aid}))
    assert ctx.projects.load_data(pid)["clip_reframe"][str(ids[1])]["keys"] == [[65.0, 0.3], [95.0, 0.7]]
    calls = []                                                  # clic droit sur un projet : renommer, supprimer
    monkeypatch.setattr(st, "project_menu", lambda *a: calls.append(a))
    item = cv.projects.itemAt(0).widget()
    QApplication.sendEvent(item, QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(3, 3), QPoint(30, 30)))
    assert calls and calls[0][0] == item.pid and calls[0][1] == "smart_clipping"
    acts = cv.next.more.menu().actions()                        # la flèche : les autres onglets
    assert [a.text() for a in acts] == ["Traduction et adaptation", "Textes", "Montage libre", "Export"]
    acts[3].trigger()                                           # vers Export
    assert st.stack.currentWidget() is st.export_view and w.dock.buttons["export"].isChecked()
    w.start_parcours("smart_clipping")
    cv.next.go.click()                                          # « Suivant » : la même vidéo, dans Traduction
    assert w.dock.buttons["translation"].isChecked() and st.stack.currentWidget() is st.tr_view
    assert st.tr_view.player.tl.duration == 30.0 and st.tr_view.player.tl.format.aspect == "9:16"


def test_delete_and_rename_project_from_list(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    w, ctx = window
    st = w.studio
    w.start_parcours("smart_clipping")
    a = st.project_id
    ctx.projects.rename(a, "Premier")
    st.new_blank.emit("smart_clipping")
    b = st.project_id
    ctx.projects.rename(b, "Second")
    ctx.db.connect().execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, "
                             "audio_tracks, duration_s, width, height) VALUES (1,?,'video','x.mp4','x.mp4','fx',1,9,"
                             "640,360)", (b,))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    assert st.delete_project(b, "smart_clipping")               # projet ouvert supprimé : le suivant s'ouvre
    assert ctx.projects.get(b) is None and st.project_id == a
    assert st.stack.currentWidget() is st.clip_view
    names = [st.clip_view.projects.itemAt(i).widget() for i in range(st.clip_view.projects.count())]
    assert [it.pid for it in names] == [a]


def test_translation_screen_generate(window, monkeypatch):
    """Écran Traduction : langues, style des sous-titres, aperçu sur la vidéo du projet, « Préparer · N
    langues » (sans export : l'onglet Export fait la vidéo de la langue choisie)."""
    from studiovideo.jobs.manager import JobInfo
    from studiovideo.transcribe.model import TSegment, Transcript
    w, ctx = window
    st = w.studio
    w.start_parcours("translation")
    tv, pid = st.tr_view, st.project_id
    assert tv.left.currentIndex() == 0 and not tv.go.isEnabled()          # pas de vidéo
    assert not hasattr(tv, "caption")                                     # légende : onglet Export
    con = ctx.db.connect()
    con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, audio_tracks, "
                "duration_s, width, height) VALUES (1,?,'video',?,'court.mp4','fc',1,30,1080,1920)", (pid, __file__))
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    ctx.transcripts.store_original(aid, Transcript([TSegment(0, 4, "Bonjour à tous", [])], "fr", "t"))
    st.refresh()
    assert st.tl.format.aspect == "9:16"                                  # format de la vidéo d'origine
    assert tv.left.currentIndex() == 1 and not tv.chips["fr"].isEnabled()      # langue d'origine
    assert "Français détecté" in tv.video_lbl.text()
    assert tv.engine.currentData() == "auto" and "Automatique" in tv.engine.currentText()   # gratuit par défaut
    ctx.settings.set("llm", {"kind": "ollama", "model": "m"})
    tv.refresh()
    assert [tv.engine.itemData(i) for i in range(tv.engine.count())][:2] == ["auto", "llm"]
    tv.chips["de"].setChecked(True)
    assert tv.go.isEnabled() and tv.go.text() == "Préparer  ·  2 langues"      # anglais coché par défaut
    next(b for b in tv.presets.buttons() if b.property("preset") == "yellow").setChecked(True)
    tv.top_text.setText("Recette facile")
    tv._preview()
    assert tv.player.tl.subtitles.style.color == "#FFD54A" and tv.player.tl.format.aspect == "9:16"
    assert tv.player.tl.texts[0].text == "Recette facile"
    assert ctx.projects.load_data(pid)["translate_ui"]["preset"] == "yellow"   # choix gardés dans le projet
    sent = []
    monkeypatch.setattr(ctx.jobs, "submit", lambda kind, label_, params=None, **k: sent.append((kind, params, k))
                        or 77)
    tv._go()
    kind, params, kw = sent[0]
    assert kind == "translate_video" and params["languages"] == ["en", "de"] and params["export"] is False
    assert params["preset"] == "yellow" and params["top_text"] == "Recette facile" and params["voice"] is None
    assert params["timeline"]["segments"][0]["asset_id"] == aid                # le montage du projet
    assert tv.go.text() == "Annuler"
    tv.on_job(JobInfo(id=77, kind="translate_video", label="", status="succeeded", progress=1, message="", error="",
                      critical=False, created_at="", params=params,
                      result={"ready": ["en", "de"], "top_texts": {"en": "Easy recipe", "de": "Einfaches Rezept"},
                              "masks": [], "dubbed": False, "keep_music": True}))
    tr = ctx.projects.load_data(pid)["translations"]
    assert tr["ready"] == ["de", "en"] and tr["top_texts"]["en"] == "Easy recipe" and tr["preset"] == "yellow"
    tv.refresh()
    assert tv.next.isVisibleTo(tv) and tv.next.go.text() == "Suivant : Textes"   # vers l'onglet suivant


def test_export_tab_texts_networks_and_publication(window, monkeypatch, tmp_path):
    """Onglet Export : titre et description communs, un texte par réseau (TikTok : titre en tête de la
    description), aperçu téléphone, puis export et brouillon / programmation / publication."""
    from PySide6.QtCore import QDateTime

    from studiovideo.domain.models import Platform
    from studiovideo.jobs.manager import JobInfo
    from studiovideo.transcribe.model import TSegment, Transcript
    w, ctx = window
    st = w.studio
    w.start_parcours("free_edit")
    pid = st.project_id
    p = ctx.projects.get(pid)
    yt = ctx.concepts.create_account(Platform.YOUTUBE, "machaine")
    tk = ctx.concepts.create_account(Platform.TIKTOK, "moncompte")
    con = ctx.db.connect()
    con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, audio_tracks, "
                "duration_s, width, height) VALUES (1,?,'video',?,'x.mp4','fx',1,20,1080,1920)", (pid, __file__))
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    st.refresh()
    st.export_quick.click()                                     # « Exporter » mène à l'onglet Export
    ev = st.export_view
    assert st.stack.currentWidget() is ev and w.dock.buttons["export"].isChecked()
    assert list(ev.nets) == ["youtube", "tiktok", "facebook", "instagram"]    # une colonne par réseau
    assert ev.player.tl.segments[0].asset_id == aid             # la vidéo du projet
    assert not ev.player.play_btn.isVisibleTo(ev) and ev.player.view.click_toggles   # clic : lecture / pause
    assert not ev.version_row.isVisibleTo(ev)                   # pas de langue préparée : pas de choix
    assert ev.file_name.text() == p["name"] and ev.act_local.isChecked()      # par défaut : sur le PC
    assert not hasattr(ev, "act_draft") and not hasattr(ev, "tabs")
    assert ev.go.text() == "Exporter sur mon PC" and ev.go.isEnabled()
    acc = ev.nets["youtube"].account
    assert acc.count() == 1 and not acc.itemIcon(0).isNull()    # pastille du compte
    ev.title.setText("Le vrai chiffre")
    ev.desc.setPlainText("Ce qu'on ne vous dit pas #politique")
    yt_tab, tk_tab = ev.nets["youtube"], ev.nets["tiktok"]
    assert yt_tab.title.text() == "Le vrai chiffre" and yt_tab.text.toPlainText() == "Ce qu'on ne vous dit pas #politique"
    assert tk_tab.title_row.isHidden() and tk_tab.text.toPlainText() == \
        "Le vrai chiffre\n\nCe qu'on ne vous dit pas #politique"               # TikTok : titre puis description
    assert not ev.nets["facebook"].on.isEnabled()               # pas de compte Facebook : signalé
    assert ev.nets["facebook"].no_account.isVisibleTo(ev.nets["facebook"])
    tk_tab.text.setPlainText("Texte rien que pour TikTok 🔥")      # texte propre au réseau
    ev.desc.setPlainText("Nouvelle description")
    assert tk_tab.text.toPlainText() == "Texte rien que pour TikTok 🔥" and tk_tab.reset.isVisibleTo(tk_tab)
    assert yt_tab.text.toPlainText() == "Nouvelle description"
    ev.nets["youtube"].text.setFocus()
    from studiovideo.ui.export_view import EmojiButton
    eb = next(b for b in ev.findChildren(EmojiButton) if b.target is ev.title)
    eb.insert("🔥")                                              # menu d'emojis
    assert "🔥" in ev.title.text()
    ui = ctx.projects.load_data(pid)["export_ui"]
    assert ui["nets"]["tiktok"]["custom"] and ui["nets"]["tiktok"]["account"] == tk.id
    sent = []
    real_submit = ctx.jobs.submit
    monkeypatch.setattr(ctx.jobs, "submit", lambda kind, label_, params=None, **k: sent.append((kind, params, k))
                        or 500 + len(sent))
    ev.file_name.setText("Mon export : final")                  # nom et dossier choisis
    ev.folder = str(tmp_path)
    ev._changed()
    ev._go()                                                    # sur le PC seulement : aucune publication
    kind, params, kw = sent[-1]
    assert kind == "export_timeline" and params["dest"] == str(tmp_path / "Mon export   final.mp4")
    pend = ctx.projects.load_data(pid)["export_pending"]
    assert pend["action"] == "local" and pend["nets"] == {}
    (tmp_path / "Mon export   final.mp4").write_bytes(b"x")
    ev.on_job(JobInfo(id=pend["job"], kind="export_timeline", label="", status="succeeded", progress=1, message="",
                      error="", critical=True, created_at="", params=params,
                      result={"path": str(tmp_path / "Mon export   final.mp4")}))
    data = ctx.projects.load_data(pid)
    assert "enregistrée" in data["export_last"]["summary"] and "master_id" not in data["export_last"]
    assert ev.open_dir.isVisibleTo(ev)
    ev.file_name.setText("")
    ev.folder = str(tmp_path)
    ev.act_schedule.setChecked(True)
    ev.when.setDateTime(QDateTime.currentDateTime().addDays(2))
    assert ev.go.text().startswith("Exporter et programmer") and ev.go.isEnabled()
    ev._go()
    kind, params, kw = sent[-1]
    assert kind == "export_timeline" and kw.get("critical") and params["project_id"] == pid
    pend = ctx.projects.load_data(pid)["export_pending"]
    assert pend["action"] == "schedule" and set(pend["nets"]) == {"youtube", "tiktok"}
    out = tmp_path / "v.mp4"
    out.write_bytes(b"x")
    vid = ctx.montage.record_export(pid, st.tl, {"path": str(out)}, None)
    ev.on_job(JobInfo(id=pend["job"], kind="export_timeline", label="", status="succeeded", progress=1, message="",
                      error="", critical=True, created_at="", params=params,
                      result={"path": str(out), "variant_id": vid}))
    data = ctx.projects.load_data(pid)
    assert "export_pending" not in data and "programmée" in data["export_last"]["summary"]
    pubs = {pb.account.platform.value: pb for pb in ctx.publications.list_for_master(data["export_last"]["master_id"])}
    assert set(pubs) == {"youtube", "tiktok"}
    assert pubs["youtube"].title.startswith("Le vrai chiffre") and pubs["youtube"].description == "Nouvelle description"
    assert pubs["tiktok"].description == "Texte rien que pour TikTok 🔥" and pubs["tiktok"].title == ""
    assert all(pb.status.value == "scheduled_local" for pb in pubs.values())     # programmées (Calendrier)
    assert p["concept_id"] and yt.id
    monkeypatch.setattr(ctx.jobs, "submit", real_submit)


def test_llm_model_is_a_dropdown(window, monkeypatch):
    from studiovideo.ai import highlights as hl
    w, ctx = window
    sp = w.settings_page
    monkeypatch.setattr(hl, "check_llm", lambda kind, url="", key="": ["gpt-4o-mini", "tts-1", "gpt-4o"])
    assert not sp.llm_model.isEditable()                        # rien à taper
    sp.llm_kind.setCurrentIndex(sp.llm_kind.findData("openai"))   # la liste se remplit d'elle-même
    assert [sp.llm_model.itemText(i) for i in range(sp.llm_model.count())] == ["gpt-4o", "gpt-4o-mini"]
    sp.llm_model.setCurrentText("gpt-4o-mini")
    sp._llm_save()
    assert ctx.settings.get("llm", None)["model"] == "gpt-4o-mini"
