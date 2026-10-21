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
