"""Ordre des projets stable (seule une vraie modification fait remonter un projet) et nom des vidéos importées
par lien (le titre de la vidéo, pas l'adresse)."""
import shutil
import subprocess
import time

import pytest

from studiovideo.core import build_context
from studiovideo.media import downloader, ffmpeg


def wait(app, jid, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        j = app.jobs.get(jid)
        if j.status in ("succeeded", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise TimeoutError(app.jobs.get(jid))


def test_order_changes_only_after_a_real_change(tmp_path):
    app = build_context(tmp_path / "d")
    try:
        a = app.projects.quick_create(app.concepts, "smart_clipping", "A")
        b = app.projects.quick_create(app.concepts, "smart_clipping", "B")
        con = app.db.connect()
        con.execute("UPDATE project SET updated_at = '2020-01-02T00:00:00Z' WHERE id = ?", (a,))
        con.execute("UPDATE project SET updated_at = '2020-01-01T00:00:00Z' WHERE id = ?", (b,))
        order = lambda: [r["id"] for r in app.projects.recent(10) if r["id"] in (a, b)]   # noqa: E731
        assert order() == [a, b]
        data = app.projects.load_data(b)
        assert app.projects.save_data(b, data) is False            # rien de changé : pas de remontée
        data["last_asset_id"] = 7                                  # naviguer (média affiché) n'est pas modifier
        assert app.projects.save_data(b, data) is False
        app.projects.set_mode(b, "translation")                    # l'ouvrir dans un autre onglet non plus
        app.montage.save(b, app.montage.load(b))                   # montage identique, enregistré
        assert order() == [a, b]
        tl = app.montage.load(b)
        tl.set_aspect("16:9")                                      # une vraie modification
        app.montage.save(b, tl)
        assert order() == [b, a]
        con.execute("UPDATE project SET updated_at = '2020-01-03T00:00:00Z' WHERE id = ?", (b,))
        data = app.projects.load_data(a)
        data["clip_criteria"] = {"categories": ["rire"]}
        assert app.projects.save_data(a, data) is True
        assert order()[0] == a
    finally:
        app.close()


@pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")
def test_link_import_is_named_after_the_video_title(tmp_path, monkeypatch):
    src = tmp_path / "src.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x120:d=1:r=25",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)

    def fake_download(url, dest, progress=None, is_cancelled=None):
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / "youtube-abc123.mp4"                          # nom technique du fichier téléchargé
        shutil.copy(src, out)
        return out, {"id": "abc123", "title": "Le  grand débat : la suite", "uploader": "chaine",
                     "webpage_url": url, "extractor": "youtube", "upload_date": "20260101"}
    monkeypatch.setattr(downloader, "download", fake_download)
    app = build_context(tmp_path / "d")
    try:
        pid = app.projects.quick_create(app.concepts, "smart_clipping", "Lien")
        j = wait(app, app.jobs.submit("import_url", "Lien", {"url": "https://youtu.be/abc123", "project_id": pid}))
        assert j.status == "succeeded", j.error
        a = app.library.get(j.result["asset_id"])
        assert a["file_name"] == "Le grand débat : la suite.mp4"   # le titre de la vidéo, pas le lien
        assert a["path"].endswith("youtube-abc123.mp4") and a["origin_url"] == "https://youtu.be/abc123"
    finally:
        app.close()


def test_scrub_number_replaces_qt_number_fields():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from studiovideo.ui.scrub import ScrubNumber, ScrubTime, double_spin, int_spin
    seen = []
    n = int_spin()
    n.setRange(0, 30)
    n.setSuffix(" px")
    n.valueChanged.connect(seen.append)
    n.setValue(12.4)
    assert n.value() == 12 and isinstance(n.value(), int) and seen == [12] and n.val.text() == "12 px"
    n.plus.click()
    assert n.value() == 13
    n.setValue(99)
    assert n.value() == 30                                         # bornes respectées
    d = double_spin()
    d.setRange(0, 5)
    d.setDecimals(1)
    d.setSingleStep(0.1)
    d.setSuffix(" s")
    d.minus.click()
    assert d.value() == 0.0
    d.setValue(1.26)
    assert d.value() == 1.3 and d.val.text() == "1,3 s"           # virgule à la française
    d.setSpecialValueText("Auto")
    d.setValue(0)
    assert d.val.text() == "Auto"
    t = ScrubTime(step=1.0)
    t.setRange(0, 100)
    t.setValue(10)
    t.plus.click()
    assert t.value() == 11.0                                       # + : une seconde
    assert isinstance(ScrubNumber(), ScrubTime)

def test_project_takes_the_media_name_unless_renamed(tmp_path):
    app = build_context(tmp_path / "d")
    try:
        pid = app.projects.quick_create(app.concepts, "smart_clipping")
        assert app.projects.is_auto_name(app.projects.get(pid)["name"])
        assert app.projects.name_after_media(pid, "Le débat de la semaine.mp4") == "Le débat de la semaine"
        app.projects.rename(pid, "Mon titre")                      # nom choisi : il reste
        assert app.projects.name_after_media(pid, "autre.mp4") is None
        assert app.projects.get(pid)["name"] == "Mon titre"
    finally:
        app.close()
