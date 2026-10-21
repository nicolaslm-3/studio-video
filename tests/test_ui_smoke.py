"""Test de fumée de l'interface (mode sans affichage)."""
import pytest

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
