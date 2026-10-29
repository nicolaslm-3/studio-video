"""Test de fumée de l'interface (mode sans affichage)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

pytest.importorskip("PySide6")

from studiovideo.core import build_context  # noqa: E402
from studiovideo.domain.models import Platform  # noqa: E402
from PySide6.QtWidgets import QLabel  # noqa: E402
from studiovideo.ui.widgets import ToggleSwitch  # noqa: E402


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
    st.panel._add_text("hook")                                  # accroche : toute la vidéo
    assert st.tl.texts and (st.tl.texts[0].start, st.tl.texts[0].end) == (0.0, st.tl.duration)
    st.panel._add_text("cta")                                   # les autres : à la tête de lecture
    assert st.tl.texts[1].start == 2.0
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


def test_calendar_publication_flow(window, monkeypatch, tmp_path):
    """Calendrier : menu des concepts (ou tous), « Nouvelle publication » en rectangle volant (concept, vidéo, un
    seul texte, date et heure, logos), vidéos en petits rectangles à leur date avec trois logos à cocher."""
    from PySide6.QtCore import QDate, QDateTime, QTime
    from PySide6.QtWidgets import QApplication, QLabel
    from studiovideo.domain.models import Platform
    from studiovideo.ui.pages import calendar as calmod
    from studiovideo.ui.widgets import Popover
    w, ctx = window
    c = ctx.concepts.create_concept("Tous y passent")
    c2 = ctx.concepts.create_concept("Autre concept")
    pid = ctx.projects.create(c.id, "Mon sketch")
    w.go(3)
    cal = w.calendar
    assert not hasattr(cal, "todo") and not hasattr(cal, "undated")        # plus de « À faire » ni « Sans date »
    assert cal.concept.itemText(0) == "Tous les concepts" and cal.concept.findData(c.id) > 0
    pop = cal.new_publication(day="2026-11-05")
    assert isinstance(pop, Popover)                                        # rectangle volant, pas une fenêtre
    assert not hasattr(pop, "thumb") and not hasattr(pop, "rows")          # ni miniature ni « Destinations »
    pop.concept.setCurrentIndex(pop.concept.findData(c.id))
    pop.video.setCurrentIndex(pop.video.findData(pid))
    pop.text.setPlainText("Mon sketch\nLa suite\n#humour")
    assert pop.when.dateTime().toString("yyyy-MM-ddTHH:mm") == "2026-11-05T18:00"
    pop.when.setDateTime(QDateTime(QDate(2026, 11, 5), QTime(20, 30)))
    pop.nets["tiktok"].setChecked(False)
    assert pop.nets["tiktok"].text() == "" and pop.nets["tiktok"].toolTip() == "TikTok"   # logo seul
    pop._save()                                                           # « Enregistrer » : visible au calendrier
    (row,) = ctx.publications.planned("2026-11-01T00:00", "2026-12-01T00:00")
    assert row["planned_at"] == "2026-11-05T20:30" and row["video"] == "Mon sketch" and row["title"] == "Mon sketch"
    assert row["networks"] == {"youtube": True, "tiktok": False, "meta": True}
    cal.year, cal.month = 2026, 11
    cal.refresh()
    chips = [ch for cell in cal.cells for ch in cell.findChildren(calmod.PlanChip)]
    assert len(chips) == 1
    chip = chips[0]
    assert not chip.toggles["tiktok"].isChecked() and chip.toggles["youtube"].isChecked()
    chip.toggles["tiktok"].click()                                        # en couleur : juste pour s'organiser
    assert ctx.publications.plan_of(row["id"])["networks"]["tiktok"] is True
    assert ctx.publications.list_for_master(row["id"]) == []              # rien n'est publié
    cal.concept.setCurrentIndex(cal.concept.findData(c2.id))              # autre concept : ses vidéos seulement
    assert not [ch for cell in cal.cells for ch in cell.findChildren(calmod.PlanChip) if ch.isVisibleTo(cell)]
    cal.concept.setCurrentIndex(0)
    edit = cal.open_master(row["id"])                                      # clic : modifier
    assert edit.text.toPlainText().startswith("Mon sketch") and edit.video.currentData() == pid
    assert not [lb for lb in edit.findChildren(QLabel) if lb.text() in ("Concept", "Vidéo", "Texte")]
    assert edit.video.placeholderText() == "Vidéo" and edit.text.placeholderText().startswith("Texte")
    assert not edit.tile.isEnabled() and not edit.reveal_btn.isEnabled()   # pas encore exportée : non disponible
    assert "Pas encore exportée" in edit.tile.hint.text()
    edit.nets["meta"].click()                                              # logo du rectangle = logo du calendrier
    assert ctx.publications.plan_of(row["id"])["networks"]["meta"] is False
    chip = next(ch for cell in cal.cells for ch in cell.findChildren(calmod.PlanChip))
    assert not chip.toggles["meta"].isChecked()
    out = tmp_path / "Mon sketch.mp4"
    out.write_bytes(b"x")
    data = ctx.projects.load_data(pid)
    data["export_last"] = {"path": str(out)}
    ctx.projects.save_data(pid, data)
    edit._show_video()
    assert edit.tile.isEnabled() and edit.tile.path == str(out) and edit.reveal_btn.isEnabled()   # à glisser
    out.unlink()
    edit._show_video()
    assert not edit.tile.isEnabled() and "Non disponible" in edit.tile.name.text()     # déplacé : signalé
    assert not cal.cells[0].graphicsEffect() is None or cal.cells[0].day[5:7] == "11"  # hors du mois : pâle
    assert cal.upcoming.count() >= 1                                       # liste « À venir »
    # Atelier de publication (Calendrier) : page d'envoi dans un nouvel onglet, lien confirmé
    yt = ctx.concepts.create_account(Platform.YOUTUBE, "Chaîne")
    ctx.concepts.link(c.id, yt.id)
    m = ctx.publications.create_master(c.id, "Mon sketch")
    (pub,) = ctx.publications.set_destinations(m, [yt.id])
    from studiovideo.ui.publish_workshop import PublishWorkshop
    opened = []
    a = PublishWorkshop(ctx, pub, open_url=opened.append)
    a._open()
    assert QApplication.clipboard().text() == "Mon sketch"
    assert opened == ["https://www.youtube.com/upload"] and "nouvel onglet" in a.msg.text()
    a.link.url.setText("https://youtu.be/abc")
    a.link._confirm()
    assert ctx.publications.get(pub)["status"] == "published"
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
    # « Par vidéo » (premier onglet) : la même vidéo sur les quatre réseaux, dans un seul tableau
    assert pg.tabs.tabText(0) == "Par vidéo" and pg.videos.rowCount() == 1
    v = pg.videos
    assert [v.horizontalHeaderItem(c).text() for c in range(2, 6)] == ["YouTube", "TikTok", "Facebook", "Instagram"]
    assert v.item(0, 0).text() == "Vidéo 1" and v.item(0, 1).text() == "12 k"          # 500 + 12 000
    assert (v.item(0, 2).text(), v.item(0, 3).text(), v.item(0, 4).text()) == ("500", "12 k", "")   # pas sur Facebook
    d = pg.detail
    assert d.rowCount() == 4 and d.item(0, 0).text() == "YouTube · A" and d.item(0, 2).text() == "20"
    assert d.item(1, 4).text() == "40" and d.item(1, 6).text() == "saisi"               # partages TikTok, source
    assert d.item(0, 6).text() == "API" and d.item(2, 1).text() == ""                  # Facebook : pas publiée
    assert d.item(1, 8).text() == "Ouvrir"                                             # lien de la vidéo TikTok


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


def test_opens_in_free_edit_without_home(window):
    w, ctx = window
    assert w.pages.currentWidget() is w.studio and w.studio._mode() == "free_edit"    # pas de page d'accueil
    assert "Accueil" not in [b.text() for b in w.nav_group.buttons()]
    assert "Publication" not in [w.settings_page.tabs.tabText(i) for i in range(w.settings_page.tabs.count())]


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
    w.go(2)
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
    fp = w.studio.format_pick                                   # format : un bouton et son menu (trois icônes)
    assert set(fp.actions_) == {"16:9", "1:1", "9:16"} and fp.menu() is not None
    fp.actions_["1:1"].trigger()
    assert w.studio.tl.format.aspect == "1:1" and ctx.projects.is_untouched(pid)
    assert fp.value() == "1:1" and "Carré" in fp.toolTip()
    w.start_parcours("original")                                # un projet traverse tous les parcours
    assert w.studio.project_id == pid and ctx.projects.get(pid)["mode"] == "original"
    assert w.dock.buttons["original"].isChecked() and w.studio.tl.format.aspect == "1:1"
    bid = pid
    ctx.db.connect().execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, "
                             "audio_tracks, duration_s, width, height) VALUES (1,?,'video','d.mp4','d.mp4','fd',1,9,"
                             "640,360)", (bid,))
    w.start_parcours("smart_clipping")                          # même projet, même vidéo, autre interface
    assert w.studio.project_id == bid and w.studio.stack.currentWidget() is w.studio.clip_view
    assert "d.mp4" in w.studio.clip_view.full_card.sub.text()
    w.start_parcours("translation")
    assert w.studio.project_id == bid and w.studio.tr_view.left.currentIndex() == 1
    w.go(2)
    w.start_parcours("original")                                # hors Studio : le projet ouvert reprend
    assert w.studio.project_id == bid and ctx.projects.get(bid)["mode"] == "original"
    w.studio.new_blank.emit("original")                            # bouton « + » : projet vierge du même parcours
    nid = w.studio.project_id
    assert nid != bid and ctx.projects.get(nid)["mode"] == "original" and ctx.projects.is_untouched(nid)
    w.close()                                                   # fermeture : projet vierge intact non conservé
    assert ctx.projects.get(nid) is None and ctx.projects.get(bid) is not None
    w.show()
    w.go(2)                                                     # hors Studio : plus de parcours actif
    assert not w.dock.buttons["original"].isChecked()
    w.drawer.navigate.emit(6)
    assert w.pages.currentWidget() is w.archives                # menu ☰ : Archives (plus de Concepts et projets)


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
    assert not st.full_btn.isVisibleTo(st)                      # Traduction : plus de « Tous les outils »
    w.start_parcours("texts")                                   # Textes : textes et sous-titres seulement
    assert st.stack.currentWidget() is st.editor and st.guide.isHidden()
    assert [st.panel.tab_name(i) for i in range(st.panel.count()) if st.panel.isTabVisible(i)] == \
        ["Textes", "Sous-titres"]
    assert not st.left_tabs.isVisibleTo(st) and st.sidebar.isVisibleTo(st)   # à gauche : la barre des projets
    assert st.sidebar.title.text() == "Textes"
    assert not st.timeline_box.isVisibleTo(st)
    assert not any(b.isVisibleTo(st) for b in (st.full_btn, st.undo_btn, st.redo_btn))
    assert not st.mplayer.note.isVisibleTo(st)                  # plus de phrase sous l'aperçu
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
    assert st.import_btn.text() == "" and st.media_btn.text() == ""   # icônes
    assert not hasattr(st, "export_quick")                      # Exporter : l'onglet Export du dock, en bas
    assert st.link_edit.placeholderText() == "" and not st.link_icon.icon().isNull()   # icône de lien seule
    lay = st.topbar.layout()                                    # à gauche : Médias, Importer, lien
    assert [lay.itemAt(i).widget() for i in range(3)] == [st.media_btn, st.import_btn, st.link_edit]
    assert not hasattr(st, "doc_action") and not hasattr(st, "format_combo")    # plus de menu « … »
    assert not hasattr(st, "concept_lbl")                       # plus de rectangle « Brouillons »
    assert st.project_combo.toolTip() == "Toutes les vidéos"
    assert st.jobs_btn.isVisibleTo(st)                           # tâches : en haut
    st.tl.set_aspect("1:1")                                     # une modification…
    st._autosave()                                              # « Enregistré » : en haut de la liste des tâches
    assert w.jobs_panel.note.text().startswith("✓ Enregistré à")
    st.save_lbl.setText("Import en cours…")
    assert "Import en cours…" in w.jobs_panel.note.text()
    w.start_parcours("smart_clipping")
    assert st.clip_view.body.currentIndex() == 0                # pas encore de vidéo : zone d'import
    con = ctx.db.connect()
    con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, audio_tracks, "
                "duration_s, width, height) VALUES (1,NULL,'video','e.mp4','e.mp4','fe',1,90,1920,1080)")
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    st.attach_asset(aid)                                        # vidéo déjà importée (Médias)
    assert st.asset_id == aid and st.clip_view.body.currentIndex() == 1
    assert "e.mp4" in st.clip_view.full_card.sub.text()
    assert [(g.asset_id, g.src_out) for g in st.tl.segments] == [(aid, 90)]       # devenue la vidéo du projet
    assert not hasattr(st.tr_view, "link")                      # plus d'import propre à Traduction
    for mode in ("translation", "original", "texts", "free_edit", "export"):
        w.start_parcours(mode)
        assert all(x.isVisibleTo(st) for x in bar) == (mode != "export"), mode   # Export : pas d'import
        assert st.project_video()["id"] == aid, mode             # la même vidéo partout
        if mode == "original":
            assert st.media_list.isVisibleTo(st)                # médias importés visibles (images des scènes)
    assert st.stack.currentWidget() is st.export_view and w.dock.buttons["export"].isChecked()


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
    w.go(2)
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
    assert not cv.banner.isVisibleTo(cv) and cv.body.currentIndex() == 0    # seulement la zone de dépôt
    assert cv.drop_zone.width() == 420 and cv.player.fit_top           # petite, au centre ; aperçu en haut
    assert cv.import_extra() == {"auto_clips": True}            # import : transcription puis extraits, d'office
    assert st._import_extra() == {"auto_clips": True}           # même réglage depuis l'en-tête commun
    assert not hasattr(cv, "step_lbls")                         # plus d'étapes en haut
    con = ctx.db.connect()
    con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, audio_tracks, "
                "duration_s, width, height) VALUES (1,?,'video',?,'debat.mp4','fd',1,300,1920,1080)", (pid, __file__))
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    st.refresh()
    assert [(g.asset_id, g.src_out) for g in st.tl.segments] == [(aid, 300)]      # vidéo entière dans le montage
    assert cv.body.currentIndex() == 1 and cv.left_stack.currentIndex() == 1   # question des critères
    assert "Politique" in cv.ask_note.text() and not cv.ask_go.isEnabled()     # pas encore transcrite
    assert list(cv.ask_crit.cats) == ["rire", "polarisant", "emotion", "politique"]   # les quatre critères
    assert "mort de rire" in cv.ask_crit.cats["rire"].toolTip() and "sature" in cv.ask_crit.cats["rire"].toolTip()
    assert "je pense que" in cv.ask_crit.cats["polarisant"].toolTip()
    assert "voix qui monte" in cv.ask_crit.cats["emotion"].toolTip()
    cv.ask_crit.cats["rire"].setChecked(True)                   # choisi pendant la transcription
    assert ctx.projects.load_data(pid)["clip_criteria"]["categories"] == ["rire"]
    assert cv.crit.cats["rire"].isChecked()                     # même choix dans la bulle des critères
    assert cv.crit.window() is cv.popup and not cv.popup.isVisible()   # pas dans la colonne de droite
    ctx.transcripts.store_original(aid, Transcript([TSegment(0, 5, "Bonjour", [])], "fr", "t"))
    sent = []
    monkeypatch.setattr(ctx.jobs, "submit", lambda kind, label_, params=None, **k: sent.append((kind, params)) or
                        900 + len(sent))
    cv.refresh()
    assert not cv.banner.isVisibleTo(cv) and cv.ask_go.isEnabled()   # rien au-dessus : la carte des critères
    cv._find()
    assert sent[-1][0] == "find_highlights" and sent[-1][1]["request"]["categories"] == ["rire"]
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
    texts = [lb.text() for lb in cv.cards[ids[0]].findChildren(QLabel)]
    assert "la dette" in texts and not any("/10" in t for t in texts)          # pas de note sur 10
    assert cv.cards[ids[0]].sub.text() == "Ils ne sont pas d'accord"   # sans parole transcrite : l'accroche
    ctx.transcripts.store_original(aid, Transcript([TSegment(0, 5, "Bonjour", []),
                                                    TSegment(9, 14, "La dette publique explose", [])], "fr", "t"))
    cv.refresh()
    assert cv.cards[ids[0]].sub.text() == "La dette publique explose"   # en gris : le début de la transcription
    assert len({c.height() for c in cv.cards.values()}) == 1    # hauteur fixe
    assert cv.full_card.title.text() == "Vidéo complète" and cv.full_card.sub.text() == "debat.mp4"
    cv._more()                                                  # « Proposer d'autres extraits » : la bulle
    assert cv.popup.isVisible() and cv.popup.crit.cats["rire"].isChecked()
    cv.popup.hide()
    assert sent == [] and not cv.follow.isChecked()             # suivi de la tête : désactivé par défaut
    assert cv.follow.text() == "" and "parle" in cv.follow.toolTip()         # icône seule, phrase au survol
    lay = cv.player.ctr                                         # à gauche des zones TikTok / Reels, même taille
    assert lay.indexOf(cv.follow) == lay.indexOf(cv.player.fx_btn) - 1
    assert cv.follow.size() == cv.player.fx_btn.size()
    assert not hasattr(cv, "title_edit") and not hasattr(cv, "caption") and not hasattr(cv, "hook")
    assert not hasattr(cv, "meta")                              # « Vidéo source » : la carte « Vidéo complète »
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
    cv.out_s.plus.click()                                       # + : une seconde de plus
    assert abs(ctx.candidates.get(ids[1])["end_s"] - 96.0) < 1e-6
    assert cv.out_s.plus.geometry().x() == cv.out_s.minus.geometry().x()    # + au-dessus de −, à droite
    assert not hasattr(cv, "here_in") and not hasattr(cv, "here_out")   # plus de « Début ici » / « Fin ici »
    cv.in_s.edit.setText("1:05")                                # double-clic : saisie au clavier
    cv.in_s._typed()
    assert abs(ctx.candidates.get(ids[1])["start_s"] - 65.0) < 1e-6
    ctx.candidates.set_hook(ids[1], "Le chômage en chiffres")
    cv._create()                                                # un projet par extrait coché
    data = ctx.projects.load_data(pid)
    assert data["clip_of"] == ids[1] and data["clip_projects"][str(ids[1])] == pid
    seg = st.tl.segments[0]
    assert (seg.asset_id, seg.src_in, seg.src_out) == (aid, 65.0, 96.0)    # la vidéo du projet = l'extrait
    assert st.tl.texts[0].text == "Le chômage en chiffres" and st.tl.format.aspect == "9:16"
    other = data["clip_projects"][str(ids[2])]
    assert other != pid and ctx.projects.get(other)["name"] == "les retraites"
    ot = ctx.montage.load(other)
    assert (ot.segments[0].src_in, ot.segments[0].src_out) == (120, 150) and ot.texts[0].text == "Et les retraites ?"
    assert [r["state"] for r in ctx.candidates.list(other)] == ["accepted"]      # son extrait, déjà choisi
    cv.refresh()
    assert "prête" in cv.b_title.text() and not cv.gen.isVisibleTo(cv) and not hasattr(cv, "next")
    tags = {c: [lb.text() for lb in card.findChildren(QLabel)] for c, card in cv.cards.items()}
    assert "Cette vidéo" in tags[ids[1]] and "Vidéo créée" in tags[ids[2]]
    cv.out_s.minus.click()                                      # l'extrait réglé : la vidéo du projet suit
    assert st.tl.segments[0].src_out == 95.0                    # − : une seconde de moins
    cv.on_job(JobInfo(id=950, kind="reframe", label="", status="succeeded", progress=1, message="", error="",
                      critical=False, created_at="", result={"keys": [[65.0, 0.3], [95.0, 0.7]]},
                      params={"project_id": pid, "candidate_id": ids[1], "src_in": 65.0, "src_out": 95.0,
                              "asset_id": aid}))
    assert ctx.projects.load_data(pid)["clip_reframe"][str(ids[1])]["keys"] == [[65.0, 0.3], [95.0, 0.7]]
    calls = []                                                  # clic droit sur un projet : renommer, supprimer
    monkeypatch.setattr(st, "project_menu", lambda *a: calls.append(a))
    items = [st.sidebar.items.itemAt(i).widget() for i in range(st.sidebar.items.count())]
    assert {it.pid for it in items} >= {pid, other}             # barre de gauche : les nouveaux projets aussi
    item = next(it for it in items if it.pid == pid)
    assert item.name.text() == "debat"                          # le nom du média source
    assert item.sub.text() == "publique · explose · dette"       # en gris : les mots-clés de la transcription
    QApplication.sendEvent(item, QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(3, 3), QPoint(30, 30)))
    assert calls and calls[0][0] == item.pid and calls[0][1] == "smart_clipping"
    w.start_parcours("translation")                             # le dock en bas : la même vidéo, dans Traduction
    assert w.dock.buttons["translation"].isChecked() and st.stack.currentWidget() is st.tr_view
    assert st.tr_view.player.tl.duration == 30.0 and st.tr_view.player.tl.format.aspect == "9:16"
    w.start_parcours("smart_clipping")
    cv._select(-1)                                              # « Vidéo complète » : toute la vidéo
    assert cv.player.tl.duration == 300 and not cv.edit_box.isVisibleTo(cv)
    n = len(ctx.candidates.list(pid))
    cv._add_clip()                                              # « + » : un petit rectangle, pas une fenêtre
    assert cv.add_popup.isVisible() and cv.add_popup.windowFlags() & Qt.Popup
    cv.add_popup.start.setValue(200)
    cv.add_popup.length.setValue(20)
    cv.add_popup.go.click()                                     # un extrait choisi à la main
    assert not cv.add_popup.isVisible()
    new = [r for r in ctx.candidates.list(pid) if r["engine"] == "manual"]
    assert len(ctx.candidates.list(pid)) == n + 1 and (new[0]["start_s"], new[0]["end_s"]) == (200, 220)
    assert new[0]["state"] == "accepted" and cv.sel == new[0]["id"]


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
    items = [st.sidebar.items.itemAt(i).widget() for i in range(st.sidebar.items.count())]
    assert [it.pid for it in items] == [a]                     # barre des projets à jour


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
    assert "Français détecté" in tv.video_lbl.text() and "court.mp4" in tv.video_lbl.text()   # sous l'aperçu
    assert tv.video_lbl.parent() is not tv.go.parent()
    steps = [lb.text() for lb in tv.findChildren(type(tv.video_lbl)) if lb.property("role") == "step"]
    assert "VIDÉO" not in " ".join(steps) and not any("·" in s for s in steps)   # ni étape vidéo ni numéros
    assert tv.do_voice.toolTip().startswith("Voulez-vous traduire la voix") and not tv.do_voice.isChecked()
    assert isinstance(tv.do_voice, ToggleSwitch) and isinstance(tv.do_subs, ToggleSwitch)   # interrupteurs
    assert tv.subs_row.engine.text().startswith("Automatique") and tv.subs_row.ok      # gratuit, prêt (vert)
    assert not tv.voice_row.ok and "Aucune voix" in tv.voice_row.state.toolTip()       # pas de voix : rouge
    assert tv.voice_row.gear is not None and tv.subs_row.gear is not None             # roues crantées
    assert not hasattr(tv, "do_top") and not hasattr(tv, "engine")                    # texte du haut : Textes
    assert tv.chips["de"].text() == "" and tv.chips["de"].toolTip().startswith("Allemagne")   # drapeau
    tv.chips["de"].setChecked(True)
    assert tv.go.isEnabled() and tv.go.text() == "Préparer  ·  2 langues"      # anglais coché par défaut
    next(b for b in tv.presets.buttons() if b.property("preset") == "yellow").setChecked(True)
    tv._preview()                                               # projet d'origine : la vidéo originale
    assert tv.player.tl is st.tl and tv.player.tl.format.aspect == "9:16"
    assert ctx.projects.load_data(pid)["translate_ui"]["preset"] == "yellow"   # choix gardés dans le projet
    sent = []
    monkeypatch.setattr(ctx.jobs, "submit", lambda kind, label_, params=None, **k: sent.append((kind, params, k))
                        or 77)
    tv._go()
    kind, params, kw = sent[0]
    assert kind == "translate_video" and params["languages"] == ["en", "de"] and params["export"] is False
    assert params["preset"] == "yellow" and params["top_text"] == "" and params["voice"] is None
    assert params["subtitles"] is True and params["root_id"] == pid
    assert params["timeline"]["segments"][0]["asset_id"] == aid                # le montage du projet
    assert tv.go.text() == "Annuler"
    tv._show_task("EN · voix · Synthèse 3/40", 0.5)             # ce que fait le logiciel, en clair
    assert tv.task.text() == "Anglais — doublage de la voix · 50 %" and tv.task_detail.text() == "Synthèse 3/40"
    tv.on_job(JobInfo(id=77, kind="translate_video", label="", status="succeeded", progress=1, message="", error="",
                      critical=False, created_at="", params=params,
                      result={"ready": ["en", "de"], "top_texts": {"en": "Easy recipe", "de": "Einfaches Rezept"},
                              "masks": [], "dubbed": False, "keep_music": True}))
    tr = ctx.projects.load_data(pid)["translations"]
    assert tr["ready"] == ["de", "en"] and tr["top_texts"]["en"] == "Easy recipe" and tr["preset"] == "yellow"
    ctx.transcripts.store_translation(aid, Transcript([TSegment(0, 4, "Hello everyone", [])], "en", "t"))
    tv.refresh()
    assert not hasattr(tv, "next")                              # plus de « Suivant » : le dock en bas
    rep = tv.status.text()                                      # résultat outil par outil
    assert "Sous-titres traduits" in rep and "Réussi" in rep and "Voix traduite" in rep and "non demandé" in rep
    report = tv._report({"status": "succeeded", "result": {"status": {
        "en": {"subtitles": "ok", "voice": "ok"}, "tr": {"subtitles": "ok", "voice": "pas de voix en turc"}}}},
        ["en", "tr"])
    assert "✓ Réussi :</span> Anglais, Turc" in report and "✗ Échec · Turc :</span> pas de voix en turc" in report
    vers = ctx.projects.versions(pid)                           # langue prête : une version sous le projet
    assert [r["lang"] for r in vers] == ["en"] and vers[0]["name"].endswith("V-EN")
    vtl = ctx.montage.load(vers[0]["id"])
    assert vtl.subtitles.language == "en" and vtl.subtitles.cues[0].text == "Hello everyone"
    entries = st.sidebar.entries()
    assert [(e[0], e[3]) for e in entries if e[0] in (pid, vers[0]["id"])] == [(pid, False), (vers[0]["id"], True)]
    assert entries[[e[0] for e in entries].index(vers[0]["id"])][1] == "V-EN"
    tv.refresh()
    assert len(ctx.projects.versions(pid)) == 1                 # pas de doublon


def test_export_tab_texts_networks_and_publication(window, monkeypatch, tmp_path):
    """Onglet Export en deux colonnes : à gauche nom du fichier, titre, description, petit aperçu et « Brouillon sur
    les réseaux » / « Sur mon PC seulement » ; à droite l'Atelier de publication (vidéo à glisser, une carte par
    réseau : YouTube, TikTok, Meta)."""
    from PySide6.QtWidgets import QApplication

    from studiovideo.domain.models import Platform
    from studiovideo.jobs.manager import JobInfo
    w, ctx = window
    st = w.studio
    w.start_parcours("free_edit")
    pid = st.project_id
    p = ctx.projects.get(pid)
    concept = ctx.concepts.create_concept("Tous y passent")
    yt = ctx.concepts.create_account(Platform.YOUTUBE, "machaine", "https://www.youtube.com/@machaine")
    tk = ctx.concepts.create_account(Platform.TIKTOK, "moncompte")
    for a in (yt, tk):
        ctx.concepts.link(concept.id, a.id)
    con = ctx.db.connect()
    con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, audio_tracks, "
                "duration_s, width, height) VALUES (1,?,'video',?,'x.mp4','fx',1,20,1080,1920)", (pid, __file__))
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    st.refresh()
    st.show_export()                                            # l'onglet Export du dock
    ev = st.export_view
    assert st.stack.currentWidget() is ev and w.dock.buttons["export"].isChecked()
    assert list(ev.nets) == ["youtube", "tiktok", "meta"]       # une carte par réseau ; Meta = Facebook + Instagram
    assert not hasattr(ev, "act_publish") and not hasattr(ev, "act_schedule") and not hasattr(ev, "when")
    from studiovideo.ui import export_view as evmod
    assert not hasattr(evmod, "PhonePreview")                   # plus d'aperçus par réseau
    assert not hasattr(ev.nets["youtube"], "on") and not hasattr(ev, "title") and not hasattr(ev, "desc")
    assert ev.text.placeholderText() == "Texte" and ev.file_name.placeholderText() == "Nom du fichier exporté"
    assert ev.count.parent() is ev.text                         # compteur dans le rectangle de texte
    assert not [lb for lb in ev.findChildren(QLabel) if lb.text() in ("Nom du fichier", "Titre sur les réseaux",
                                                                       "Description", "Atelier de publication",
                                                                       "Concept")]
    assert ev.reveal_btn.text() == "" and ev.reveal_btn.toolTip() == "Afficher dans l'Explorateur"   # icône seule
    assert ev.player.tl.segments[0].asset_id == aid             # la vidéo du projet, petit aperçu
    assert not ev.player.play_btn.isVisibleTo(ev) and ev.player.view.click_toggles   # clic : lecture / pause
    assert ctx.projects.get(pid)["name"] == "x"                 # le projet a pris le nom du média
    assert ev.file_name.text() == "x" and ev.act_local.isChecked()      # par défaut : sur le PC
    from studiovideo.ui.export_view import date_prefix, file_name
    assert ev.date_lbl.text() == date_prefix() and len(date_prefix()) == 10   # le fichier commence par la date
    assert file_name("2020-01-01 Clip") == f"{date_prefix()} Clip.mp4"
    assert "Instagram" in ev.act_draft.toolTip()
    assert ev.go.text() == "Exporter sur mon PC" and ev.go.isEnabled()
    assert not ev.tile.isEnabled() and not ev.reveal_btn.isEnabled()      # rien d'exporté encore
    assert ev.tile.maximumWidth() <= 300                        # vidéo à glisser : rectangle étroit
    # un rectangle : menu des concepts, logos (en couleur si une chaîne est liée), « Ouvrir les pages d'envoi »
    assert ev.concept.currentData() == p["concept_id"] and ev.nets["youtube"].account_id() is None
    ev.concept.setCurrentIndex(ev.concept.findData(concept.id))
    ytc, meta = ev.nets["youtube"], ev.nets["meta"]
    assert ytc.account_id() == yt.id and ytc.channel.text() == "machaine" and ev.nets["tiktok"].account_id() == tk.id
    assert meta.account_id() is None and not meta.isVisibleTo(ev)          # pas de chaîne Meta : ligne masquée
    assert "pas de chaîne" in ev.net_icons["meta"].toolTip() and "machaine" in ev.net_icons["youtube"].toolTip()
    assert ev.open_all.text() == "Ouvrir les pages d'envoi" and not ev.open_all.isEnabled()   # rien d'exporté
    assert ctx.projects.load_data(pid)["export_ui"]["concept"] == concept.id
    # en dessous : les concepts et leurs chaînes (YouTube, TikTok, Meta)
    box = ev.concepts
    row = next(r for r in box.items() if r.concept and r.concept.id == concept.id)
    assert row.nets["youtube"].linked and row.nets["tiktok"].linked and not row.nets["meta"].linked
    assert row.nets["meta"].text() == ""                        # logo seul, nom au survol
    cp = row.open_channel("meta")                               # réseau grisé : rectangle volant pour l'ajouter
    from studiovideo.ui.widgets import Popover
    assert isinstance(cp, Popover) and not hasattr(cp, "name")   # le nom de la chaîne : celui du concept
    assert not cp.open_btn.isEnabled()
    cp.url.setText("https://www.instagram.com/moninsta")         # Meta : Instagram d'après l'adresse
    assert cp.platform() == "instagram" and cp.open_btn.isEnabled()   # lien à ouvrir dans le navigateur
    cp._save()
    assert row.nets["meta"].linked and meta.account_id() is not None and "Tous y passent" in meta.channel.text()
    assert ctx.concepts.get_account(meta.account_id()).platform.value == "instagram"
    ig_id = meta.account_id()
    new = box.new_concept()                                     # « Nouveau concept » : une ligne, sans fenêtre
    assert new.concept is None and new.name.text() == ""
    new.name.setText("Deuxième concept")
    new._name_done()
    assert new.concept is not None and ev.concept.findData(new.concept.id) >= 0
    assert ctx.concepts.get_concept(new.concept.id).name == "Deuxième concept"
    ev.text.setPlainText("Le vrai chiffre\nCe qu'on ne vous dit pas #politique")   # 1re ligne : le titre
    from studiovideo.ui.export_view import EmojiButton
    eb = next(b for b in ev.findChildren(EmojiButton) if b.target is ev.text)
    assert eb.parent() is ev.text and eb.text() == ""           # icône grise, dans le champ, en haut à droite
    assert ev.count.text().startswith("51 caract")
    ui = ctx.projects.load_data(pid)["export_ui"]
    assert ui["concept"] == concept.id and ui["title"] == "Le vrai chiffre"
    assert ui["description"] == "Ce qu'on ne vous dit pas #politique"
    sent = []
    real_submit = ctx.jobs.submit
    monkeypatch.setattr(ctx.jobs, "submit", lambda kind, label_, params=None, **k: sent.append((kind, params, k))
                        or 500 + len(sent))
    ev.file_name.setText("Mon export : final")                  # nom et dossier choisis
    ev.folder = str(tmp_path)
    ev._changed()
    ev._go()                                                    # sur le PC seulement : aucune publication
    kind, params, kw = sent[-1]
    dest = tmp_path / f"{date_prefix()} Mon export   final.mp4"   # date du jour, puis le nom choisi
    assert kind == "export_timeline" and params["dest"] == str(dest) and kw.get("critical")
    pend = ctx.projects.load_data(pid)["export_pending"]
    assert pend["action"] == "local" and pend["nets"] == {}
    dest.write_bytes(b"x")
    vid = ctx.montage.record_export(pid, st.tl, {"path": str(dest)}, None)
    ev.on_job(JobInfo(id=pend["job"], kind="export_timeline", label="", status="succeeded", progress=1, message="",
                      error="", critical=True, created_at="", params=params,
                      result={"path": str(dest), "variant_id": vid}))
    data = ctx.projects.load_data(pid)
    assert "enregistrée" in data["export_last"]["summary"] and "master_id" not in data["export_last"]
    assert ev.open_dir.isVisibleTo(ev) and ev.tile.isEnabled() and ev.reveal_btn.isEnabled()
    assert ev.tile.path == str(dest)                            # la vidéo exportée, à glisser
    # « Ouvrir les pages d'envoi » : un onglet par chaîne du concept, en un clic ; texte copié
    from studiovideo.publish import reconcile
    monkeypatch.setattr(reconcile, "youtube_channel_id", lambda url: "UC" + "x" * 22)
    opened = []
    ev.open_url = opened.append
    tkc = ev.nets["tiktok"]
    assert ev.open_all.isEnabled()
    ev.open_all.click()
    assert opened == ["https://studio.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx/videos/upload?d=ud",
                      "https://www.tiktok.com/upload", "https://business.facebook.com/latest/reels_composer"]
    assert QApplication.clipboard().text() == "Le vrai chiffre\nCe qu'on ne vous dit pas #politique"
    pubs = ctx.projects.load_data(pid)["export_last"]["pubs"]
    r = ctx.publications.get(pubs["tiktok"])
    assert r["status"] == "manual_action" and r["account_id"] == tk.id and r["output_path"] == str(dest)
    assert tkc.state.text() == "À publier" and tkc.link.isVisibleTo(tkc)
    tkc.link.url.setText("https://www.tiktok.com/@moncompte/video/7")
    tkc.link._confirm()
    assert ctx.publications.get(pubs["tiktok"])["status"] == "published" and "Publiée" in tkc.state.text()
    opened.clear()
    ev.open_pages()                                             # TikTok déjà publiée : plus rouverte
    assert len(opened) == 2 and "https://www.tiktok.com/upload" not in opened
    # brouillon sur les réseaux : YouTube connecté envoyé en brouillon ; Instagram (Meta) n'en propose pas
    con.execute("UPDATE social_account SET connection_status = 'connected' WHERE id = ?", (yt.id,))
    ev.refresh()
    assert ev.nets["meta"].account_id() == ig_id
    ev.act_draft.setChecked(True)
    assert ev.go.text().startswith("Exporter en brouillon") and "Meta" in ev.go.text()
    ev.file_name.setText("brouillon")
    ev._go()
    kind, params, kw = sent[-1]
    pend = ctx.projects.load_data(pid)["export_pending"]
    assert pend["action"] == "draft" and set(pend["nets"]) == {"youtube", "tiktok", "meta"}
    assert pend["nets"]["tiktok"]["text"].startswith("Le vrai chiffre\n\n")    # TikTok : titre puis description
    out2 = tmp_path / "b.mp4"
    out2.write_bytes(b"x")
    vid2 = ctx.montage.record_export(pid, st.tl, {"path": str(out2)}, None)
    n = len(sent)
    ev.on_job(JobInfo(id=pend["job"], kind="export_timeline", label="", status="succeeded", progress=1, message="",
                      error="", critical=True, created_at="", params=params,
                      result={"path": str(out2), "variant_id": vid2}))
    ups = [pr for k, pr, _ in sent[n:] if k == "publish_upload"]
    assert len(ups) == 1 and ups[0]["draft"] is True and ups[0]["now"] is True
    data = ctx.projects.load_data(pid)
    assert "brouillon" in data["export_last"]["summary"] and "Instagram" in data["export_last"]["summary"]
    pubs = {pb.account.platform.value: pb for pb in ctx.publications.list_for_master(data["export_last"]["master_id"])}
    assert pubs["instagram"].status.value == "manual_action" and pubs["tiktok"].status.value == "manual_action"
    assert pubs["youtube"].status.value == "uploading"
    assert set(data["export_last"]["pubs"]) == {"youtube", "tiktok", "meta"}
    assert p["concept_id"]
    monkeypatch.setattr(ctx.jobs, "submit", real_submit)


def test_llm_model_is_a_dropdown(window, monkeypatch):
    from studiovideo.ai import highlights as hl
    w, ctx = window
    sp = w.settings_page
    monkeypatch.setattr(hl, "check_llm", lambda kind, url="", key="": ["gpt-4o-mini", "tts-1", "gpt-4o"])
    lp = sp.llm_panel
    assert not lp.model.isEditable()                            # rien à taper
    lp.kind.setCurrentIndex(lp.kind.findData("openai"))         # la liste se remplit d'elle-même
    assert [lp.model.itemText(i) for i in range(lp.model.count())] == ["gpt-4o", "gpt-4o-mini"]
    lp.model.setCurrentText("gpt-4o-mini")
    lp.save()
    assert ctx.settings.get("llm", None)["model"] == "gpt-4o-mini"


def test_texts_tools_voice_track_and_preview(window, tmp_path):
    """Onglet Textes : boutons d'ajout dans leur style, début / fin à la souris, position (haut, centre, bas puis
    X / Y libres), police courte, taille à la souris, B / I, alignement ; sous-titres réglables à la souris ;
    piste Voix de la timeline ; aperçu sans bandes noires, barre de lecture sous l'image."""
    from studiovideo.edit.dubbing import DubLine, DubManifest
    from studiovideo.edit.textrender import anchor
    w, ctx = window
    st = w.studio
    w.start_parcours("texts")
    pid = st.project_id
    con = ctx.db.connect()
    con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, audio_tracks, "
                "duration_s, width, height) VALUES (1,?,'video',?,'v.mp4','fv',1,10,1080,1920)", (pid, __file__))
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    st.refresh()
    pn = st.panel
    assert st.tl.duration == 10.0 and pn.isTabVisible(2) and not pn.isTabVisible(5)     # pas d'onglet Export
    assert all(not b.icon().isNull() and b.text() == "" for b in pn.add_btns.values())   # le texte, dans son style
    assert not [b for b in pn.findChildren(type(pn.t_all)) if b.text() in ("Début ici", "Fin ici")]
    pn._add_text("title")
    t = pn.current_text()
    assert (t.start, t.end, t.position) == (0.0, 10.0, "top")            # titre : toute la vidéo
    pn.t_start._commit(1.5)                                              # début réglé à la souris
    assert t.start == 1.5 and pn.t_start.value() == 1.5
    pn.t_all.click()                                                     # « Tout » : à droite des réglages
    assert (t.start, t.end) == (0.0, 10.0)
    assert pn.pos_btns["top"].isChecked() and abs(pn.text_y.value() / 1000 - anchor(t, 1080, 1920)[1]) < 0.002
    pn.pos_btns["bottom"].click()                                        # Bas : le texte y va, X / Y suivent
    assert t.position == "bottom" and pn.text_y.value() > 700 and (t.x, t.y) == anchor(t, 1080, 1920)
    pn.text_y.setValue(400)                                              # Y bougé : placement libre
    assert t.position == "custom" and t.y == 0.4 and not any(b.isChecked() for b in pn.pos_btns.values())
    pn.pos_btns["top"].click()                                           # Haut de nouveau : retour en haut
    assert t.position == "top" and pn.pos_btns["top"].isChecked()
    font = next(w_ for k, p_, w_ in pn.bt.items if p_ == "font")
    assert font.maximumWidth() <= 170 and font.toolTip() == t.font                 # nom entier au survol
    assert font.itemData(0, 3) == font.itemText(0)                                  # 3 = Qt.ToolTipRole
    size = next(w_ for k, p_, w_ in pn.bt.items if p_ == "size")
    size._commit(100)                                                    # taille réglée à la souris
    assert t.size == 100 and size.val.text() == "100 px"
    bold = next(w_ for k, p_, w_ in pn.bt.items if p_ == "bold")
    assert bold.text() == "B" and bold.isCheckable() and bold.isChecked()
    bold.click()
    assert t.bold is False
    align = next(w_ for k, p_, w_ in pn.bt.items if p_ == "align")
    align.buttons["left"].click()
    assert t.align == "left" and align.buttons["left"].isChecked()
    labels = {lb.text() for lb in pn.widget(2).findChildren(type(st.mode_icon))}      # onglet Textes
    assert not {"Police", "Taille", "Style"} & labels                    # ça se comprend sans libellés
    pn.playhead = 2.0                                                    # sous-titres : début / fin à la souris
    pn._cue_add()
    c = pn._cue()
    pn.cue_end._commit(3.4)
    assert abs(c.end - 3.4) < 1e-6 and not hasattr(pn, "_cue_here")
    # piste Voix : les phrases de la voix doublée (version traduite)
    d = tmp_path / "dub"
    d.mkdir()
    ctx.dubs.store(DubManifest(aid, "en", "piper", {}, 10.0, [DubLine(0, 1.0, 3.0, "Hello everyone", "", "v",
                                                                       placed_start=1.0, placed_end=2.8)]), d)
    st.tl.voice.mode, st.tl.voice.language = "dub", "en"
    st._push_views()
    cv = st.timeline.canvas
    assert cv.voice_name == "Voix doublée · anglais" and cv.voice_lines == [(1.0, 2.8, "Hello everyone")]
    # aperçu : pas de bandes noires (fond de la page), barre de lecture sous l'image, à sa largeur
    view = st.mplayer.view
    from PySide6.QtCore import Qt
    assert view.backgroundBrush().style() == Qt.NoBrush and view.alignment() & Qt.AlignBottom   # image en bas
    view.frame_moved.emit(40.0, 25.0)
    m = st.mplayer.slider_row.contentsMargins()
    assert (m.left(), m.right()) == (40, 25)
    w.start_parcours("export")
    ep = st.export_view.player                                           # clic : icône lecture / pause 1,5 s
    ep._compact_click()
    assert not ep._flash.isHidden() and ep._flash_timer.interval() == 1500


def test_engine_dialogs_have_one_choice_pastille(window):
    """Réglages des moteurs : une pastille par alternative, une seule cochée ; un moteur pas prêt ne se choisit pas."""
    from studiovideo.ui.engines import TranslationEnginesDialog, VoiceEnginesDialog
    w, ctx = window
    d = TranslationEnginesDialog(ctx, w)
    assert d.picks["auto"].isChecked() and d.choice.exclusive()
    assert d.picks["google_free"].isEnabled() and not d.picks["deepl"].isEnabled()   # sans clé : pas choisissable
    d.picks["google_free"].setChecked(True)
    assert not d.picks["auto"].isChecked() and ctx.settings.get("translate_engine") == "google_free"
    d.close()
    w.start_parcours("translation")
    tv = w.studio.tr_view
    tv.refresh()
    assert tv.subs_row.engine.text().startswith("Google Traduction")   # le moteur affiché suit
    v = VoiceEnginesDialog(ctx, w)
    assert set(v.picks) == {"elevenlabs", "eleven_dub", "openai", "piper"} and v.chosen is None
    v.close()

def test_refresh_keeps_playing_and_sidebar_stable(window, monkeypatch):
    """Une tâche qui finit (rafraîchissement) ne met plus la vidéo en pause ; la barre des projets n'est pas
    reconstruite si rien n'a changé ; un nom trop long finit par « … » sans élargir la ligne."""
    from PySide6.QtWidgets import QApplication
    w, ctx = window
    st = w.studio
    w.start_parcours("smart_clipping")
    ctx.db.connect().execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, "
                             "audio_tracks, duration_s, width, height) VALUES (1,?,'video','l.mp4',?,'fl',1,9,640,"
                             "360)", (st.project_id, "Un nom de fichier vraiment beaucoup trop long pour la barre.mp4"))
    st.refresh()
    paused = []
    monkeypatch.setattr(st.mplayer, "pause", lambda: paused.append("montage"))
    monkeypatch.setattr(st.clip_view.player, "pause", lambda: paused.append("clipping"))
    monkeypatch.setattr(st.tr_view.player, "pause", lambda: paused.append("traduction"))
    st.refresh()
    st.refresh()                                                # tâche finie, import… : la lecture continue
    assert paused == []
    items = [st.sidebar.items.itemAt(i).widget() for i in range(st.sidebar.items.count())]
    st.sidebar.refresh()
    assert items == [st.sidebar.items.itemAt(i).widget() for i in range(st.sidebar.items.count())]
    for _ in range(5):
        QApplication.processEvents()
    item = next(it for it in items if it.pid == st.project_id)
    assert item.sub.text() == "Transcription à venir"           # puis les mots-clés de la transcription
    assert item.name.text().endswith("pour la barre")                     # nom du média
    if item.name.fontMetrics().horizontalAdvance(item.name.text()) > item.name.width():
        assert QLabel.text(item.name).endswith("…")                     # trop long : « … » (selon la police)
    assert item.minimumSizeHint().width() < st.sidebar.width() - 20   # ne déborde pas : arrondie des deux côtés
    w.start_parcours("translation")                             # autre écran : là, la lecture s'arrête
    assert "clipping" in paused

def test_robot_effects_export_sidebar_and_nested_projects(window, monkeypatch):
    """Bouton robot sous chaque aperçu (effets de la vidéo du projet), barre des projets dans Export (on y reste),
    extraits rangés sous leur projet, version supprimée pour de bon, heure en heures + minutes."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox
    w, ctx = window
    st = w.studio
    w.start_parcours("free_edit")
    pid = st.project_id
    ctx.db.connect().execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, "
                             "audio_tracks, duration_s, width, height) VALUES (1,?,'video','r.mp4','r.mp4','fr',1,20,"
                             "1920,1080)", (pid,))
    st.refresh()
    for pl in (st.mplayer, st.clip_view.player, st.tr_view.player, st.export_view.player):
        lay = pl.ctr
        assert lay.indexOf(pl.fx_btn) == lay.indexOf(pl.format_pick) - 1   # robot puis format
        assert not hasattr(pl, "step_prev") and pl.play_btn.width() == 26   # sans image avant / arrière
    fp = st.clip_view.player.format_pick                         # zones TikTok / Reels : dans le menu du format
    fp.zone_act.trigger()
    assert st.clip_view.player.zone.isChecked() and st.mplayer.format_pick is st.format_pick
    fp.actions_["1:1"].trigger()                                 # format choisi sous un aperçu : partout
    assert st.tl.format.aspect == "1:1" and st.mplayer.format_pick.value() == "1:1"
    w.start_parcours("smart_clipping")
    st.clip_view.player.fx_btn.click()                           # le rectangle des effets
    pop = st.clip_view.player.fx_pop
    assert pop.isVisible() and pop.windowFlags() & Qt.Popup
    pop.mirror.setChecked(True)
    pop.tilt.setValue(3.5)
    pop.speed.setValue(1.5)
    chips = pop.looks.buttons()                                  # filtres : un emoji, le nom au survol, même taille
    assert {b.toolTip() for b in chips} >= {"Chaud", "Froid", "Noir et blanc"}
    assert next(b for b in chips if b.toolTip() == "Chaud").text() == "🔥"
    assert len({b.size().toTuple() for b in chips}) == 1
    next(b for b in chips if b.property("look") == "nb").setChecked(True)
    pop.amount.setValue(40)
    v = st.tl.video                                              # la vidéo du projet, dans tous les onglets
    assert v.mirror and v.tilt == 3.5 and v.speed == 1.5 and v.look == "nb" and v.look_amount == 0.4
    st.flush()
    saved = ctx.montage.load(pid).video
    assert saved.mirror and saved.speed == 1.5
    pop.hide()
    st.tr_view.refresh()
    assert st.tr_view.player.tl.video.mirror                     # Traduction : même image
    assert not st.tr_view.video_lbl.isVisibleTo(st.tr_view)      # plus de nom de fichier sous l'aperçu
    w.start_parcours("texts")
    assert st.viewer_card.property("role") == "dim"              # pas de grand rectangle sous l'aperçu
    # export : la barre des projets, et on y reste en changeant de projet
    other = ctx.projects.quick_create(ctx.concepts, "translation", "Autre")
    w.start_parcours("export")
    assert st.sidebar.isVisibleTo(st) and st.sidebar.mode == "export" and not st.project_combo.isVisibleTo(st)
    st.sidebar.refresh()
    st.open_from_list(other, "export")
    assert st.project_id == other and st.stack.currentWidget() is st.export_view
    # un extrait devenu un projet, et une version traduite : sous leur projet
    clip = ctx.projects.quick_create(ctx.concepts, "smart_clipping", "Mon extrait")
    data = ctx.projects.load_data(pid)
    data["clip_projects"] = {"1": pid, "2": clip}
    data["translations"] = {"ready": ["en"]}
    ctx.projects.save_data(pid, data)
    ver = ctx.projects.save_version(pid, "en", st.tl.to_dict())
    ents = st.sidebar.entries()
    ids = [e[0] for e in ents]
    assert ids.index(pid) < ids.index(ver) < ids.index(clip) and ents[ids.index(clip)][3] == 1
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    assert st.delete_project(ver, "export")
    assert "en" not in ctx.projects.load_data(pid)["translations"]["ready"]       # pas recréée ensuite


def test_transcript_button_translate_and_voices(window, monkeypatch):
    """Bouton « Transcription » (corriger : les sous-titres suivent), roue crantée de la transcription ;
    Traduction : sous-titres puis voix, « Traduire » et « Corriger », doublage ElevenLabs, écoute des voix."""
    from studiovideo.edit.model import Timeline
    from studiovideo.services import secrets
    from studiovideo.transcribe.model import TSegment, Transcript
    from studiovideo.ui.engines import TranscriptionEnginesDialog
    from studiovideo.ui.simple_views import VoiceDialog
    from studiovideo.ui.transcript_dialog import TranscriptDialog
    w, ctx = window
    st = w.studio
    w.start_parcours("translation")
    pid = st.project_id
    con = ctx.db.connect()
    con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, audio_tracks, "
                "duration_s, width, height) VALUES (1,?,'video',?,'t.mp4','ft',1,10,1080,1920)", (pid, __file__))
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    ctx.transcripts.store_original(aid, Transcript([TSegment(0, 2, "Bonjour à tous", []),
                                                    TSegment(2, 4, "On commence", [])], "fr", "faster-whisper:small"))
    st.refresh()
    assert st.transcript_btn.isVisibleTo(st) and not hasattr(st, "asr_btn")     # la roue : dans la fenêtre
    assert st.media_btn.toolTip() == "Médias"
    from studiovideo.edit.subtitles import generate
    st.tl.subtitles.cues = generate(st.tl, {aid: ctx.transcripts.get(aid)})       # sous-titres déjà faits
    dlg = TranscriptDialog(ctx, st.project_video(), "", st)
    assert dlg.table.rowCount() == 2 and dlg.info.text() == "t.mp4 · Whisper small · sur ce PC"
    assert not hasattr(dlg, "save_btn") and not hasattr(dlg, "revert_btn")
    assert dlg.history_btn.toolTip() == "Historique des transcriptions"
    dlg.saved.connect(st.transcript_saved)
    dlg.table.item(0, 1).setText("Bonsoir à tous")              # enregistré de lui-même
    dlg._save_timer.timeout.emit()
    assert ctx.transcripts.get(aid).segments[0].text == "Bonsoir à tous"           # la version corrigée
    assert ctx.transcripts.original(aid).segments[0].text == "Bonjour à tous"
    assert "Bonsoir" in " ".join(c.text for c in st.tl.subtitles.cues)             # les sous-titres suivent
    eng = TranscriptionEnginesDialog(ctx, st)                    # roue crantée : sur ce PC ou Scribe v2
    assert set(eng.picks) == {"local", "elevenlabs"}
    # une nouvelle transcription s'applique ; l'ancienne (corrigée) reste dans l'historique
    ctx.transcripts.store_original(aid, Transcript([TSegment(0, 2, "Salut tout le monde", [])], "fr",
                                                   "elevenlabs:scribe_v2"))
    dlg.load()
    assert dlg.info.text() == "t.mp4 · ElevenLabs Scribe v2"
    dlg._history()
    titles = [(e.title.text(), e.sub.text()) for e in dlg.pop.entries]
    assert titles[0] == ("✓ ElevenLabs Scribe v2", "Salut tout le monde")
    assert titles[1] == ("Whisper small · sur ce PC", "Bonsoir à tous")
    dlg.pop.entries[1].clicked.emit()                            # revenir à l'ancienne
    assert ctx.transcripts.get(aid).segments[0].text == "Bonsoir à tous" and dlg.table.rowCount() == 2
    assert "Bonsoir" in " ".join(c.text for c in st.tl.subtitles.cues)
    tv = st.tr_view
    lay = tv.subs_row.parentWidget().layout()
    order = [lay.indexOf(r) for r in (tv.subs_row, tv.voice_row, tv.music_row, tv.erase_row)]
    assert order == sorted(order)                                # sous-titres, voix, musique, effacer
    assert tv.music_row.engine.isHidden() and tv.erase_row.engine.isHidden()
    assert tv.erase_row.name.text() == "Effacer les textes d'origine"
    assert all(b.toolTip().startswith("Sous-titres :") and not b.text() for b in tv.presets.buttons())   # « A »
    assert tv.chips["de"].iconSize().width() == 20 and tv.subs_hist.toolTip() == "Historique des traductions"
    assert tv.voice_hist.toolTip() == "Historique des voix générées"
    sent = []
    monkeypatch.setattr(ctx.jobs, "submit", lambda kind, label_, params=None, **k: sent.append((kind, params))
                        or 90 + len(sent))
    for c, b in tv.chips.items():
        b.setChecked(c in ("en", "de"))
    tv._translate_now()                                          # « Traduire » : le texte seul, par langue
    assert [(k, p["language"]) for k, p in sent] == [("translate", "en"), ("translate", "de")]
    ctx.transcripts.store_translation(aid, Transcript([TSegment(0, 2, "Hello everyone", [])], "en", "t"))
    tv.refresh()
    fix = [tv.tr_edit.itemAt(i).widget() for i in range(tv.tr_edit.count())]
    flags = [b for b in fix if b is not None and b.property("lang")]
    assert [b.property("lang") for b in flags] == ["en"] and flags[0].text() == "" and flags[0].toolTip() == "Anglais"
    tdlg = TranscriptDialog(ctx, st.project_video(), "en", st)   # « Corriger » : la traduction
    tdlg.table.item(0, 1).setText("Hi everyone")
    tdlg.accept()                                                # fermer enregistre
    assert ctx.transcripts.translation(aid, "en").segments[0].text == "Hi everyone"
    ctx.transcripts.store_translation(aid, Transcript([TSegment(0, 2, "Hey all", [])], "en", "traduction:deepl"),
                                      label="DeepL")              # nouvelle traduction : appliquée
    tv._history("translation")
    ent = tv.hist_pop.entries
    assert len(ent) == 2 and ent[0].title.text() == "✓ DeepL" and ent[1].sub.text() == "Hi everyone"
    ent[1].clicked.emit()                                        # retour à la traduction corrigée
    assert ctx.transcripts.translation(aid, "en").segments[0].text == "Hi everyone"
    monkeypatch.setattr(secrets, "get_secret", lambda n: "sk_x" if n == "elevenlabs_api_key" else None)
    vd = VoiceDialog(ctx, {"engine": "eleven_dub"}, st)          # doublage ElevenLabs : à partir du son
    vd._load()
    it = vd.list.currentItem()
    assert it.data(0x0100)["engine"] == "eleven_dub" and "son de la vidéo" in vd.status.text()
    tv.voice = it.data(0x0100)
    assert tv._voice_ready()[0]
    root_tl = st.tl                                              # projet d'origine : l'original à l'aperçu
    tv._preview()
    assert tv.player.tl is root_tl and isinstance(root_tl, Timeline)


def test_sidebar_hover_actions_and_archives(window, monkeypatch):
    """Barre des vidéos : au survol, renommer / archiver / supprimer ; une vidéo archivée quitte la barre et se
    retrouve dans Archives (menu des trois barres), d'où on la restaure."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QEnterEvent
    from PySide6.QtWidgets import QApplication, QInputDialog

    from studiovideo.ui.project_sidebar import ProjectItem
    w, ctx = window
    st = w.studio
    w.start_parcours("smart_clipping")
    keep = st.project_id
    ctx.projects.rename(keep, "Garder")
    st.new_blank.emit("smart_clipping")
    gone = st.project_id
    ctx.projects.rename(gone, "À archiver")
    st.sidebar.refresh()
    items = {it.pid: it for it in st.sidebar.findChildren(ProjectItem)}
    it = items[gone]
    assert not it.actions.isVisibleTo(it)
    QApplication.sendEvent(it, QEnterEvent(it.rect().center(), it.rect().center(), it.mapToGlobal(it.rect().center())))
    assert it.actions.isVisibleTo(it)                                 # au survol : les trois boutons
    tips = [b.toolTip() for b in it.actions.findChildren(type(st.sidebar.new_btn))]
    assert tips[0] == "Renommer" and tips[1].startswith("Archiver") and tips[2] == "Supprimer"
    QApplication.sendEvent(it, QEvent(QEvent.Leave))
    assert not it.actions.isVisibleTo(it)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Nouveau nom", True))
    st.project_action(keep, "rename", "smart_clipping")
    assert ctx.projects.get(keep)["name"] == "Nouveau nom"
    st.project_action(gone, "archive", "smart_clipping")              # vidéo ouverte archivée : une autre s'ouvre
    assert ctx.projects.get(gone)["status"] == "archived" and st.project_id != gone
    assert gone not in [e[0] for e in st.sidebar.entries()]
    w.drawer.navigate.emit(6)
    ar = w.archives
    assert w.pages.currentWidget() is ar and ar.list.count() == 1
    ar.list.setCurrentRow(0)
    ar.restore()
    assert ctx.projects.get(gone)["status"] == "active" and ar.list.count() == 0
    assert gone in [e[0] for e in st.sidebar.entries()]
