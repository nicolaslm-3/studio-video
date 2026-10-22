"""Archive de projet : export puis import sur un « autre ordinateur » (base vierge), identifiants renumérotés."""
import subprocess
import zipfile

import pytest

from studiovideo.media import ffmpeg
from studiovideo.services import archive

pytestmark = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")


def _setup(app, tmp_path):
    from studiovideo.edit.model import Timeline
    from studiovideo.transcribe.model import TSegment, Transcript
    vid = tmp_path / "source.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:d=2:r=25",
                    "-f", "lavfi", "-i", "sine=d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(vid)], check=True)
    c = app.concepts.create_concept("Poussey Pass", color="#E8833A")
    pid = app.projects.create(c.id, "Sketch", "translation")
    aid = app.importer().import_file(vid, project_id=pid).asset_id
    tr = Transcript([TSegment(0, 1.5, "Bonjour tout le monde")], "fr")
    app.transcripts.store_original(aid, tr)
    tr.edit_segment(0, "Bonjour à tous")
    app.transcripts.save_edit(aid, tr)
    app.transcripts.store_translation(aid, Transcript([TSegment(0, 1.5, "Hello everyone")], "en"))
    tl = Timeline()
    tl.add_segment(aid, 0.2, 1.8)
    app.montage.save(pid, tl)
    with app.db.transaction() as con:
        con.execute("INSERT INTO clip(project_id, source_asset_id, name, start_s, end_s) VALUES (?,?,'Clip 1',0,1)",
                    (pid, aid))
    return pid, aid, vid


def test_roundtrip_with_media(tmp_path):
    from studiovideo.core import build_context
    a = build_context(tmp_path / "pc1")
    try:
        pid, aid, vid = _setup(a, tmp_path)
        res = archive.export_project(a, pid, tmp_path / "Sketch.studioproj")
        assert res["archived"] == 1
        names = zipfile.ZipFile(tmp_path / "Sketch.studioproj").namelist()
        assert "project.json" in names and any(n.startswith("media/") for n in names)
    finally:
        a.close()
    vid.unlink()                                             # le fichier d'origine n'existe pas sur l'autre PC
    b = build_context(tmp_path / "pc2")
    try:
        b.concepts.create_concept("Autre")
        b.importer()                                         # base vierge, identifiants différents
        out = archive.import_project(b, tmp_path / "Sketch.studioproj", tmp_path / "Videos")
        assert out["extracted"] == 1 and not out["missing"]
        np = out["project_id"]
        p = b.projects.get(np)
        assert p["name"] == "Sketch" and p["mode"] == "translation" and p["concept_name"] == "Poussey Pass"
        tl = b.montage.load(np)
        new_aid = tl.segments[0].asset_id
        assert b.library.get(new_aid)["path"].endswith("source.mp4")
        assert (tmp_path / "Videos" / "Sketch" / "source.mp4").exists()
        assert b.transcripts.get(new_aid).segments[0].text == "Bonjour à tous"           # correction conservée
        assert b.transcripts.original(new_aid).segments[0].text == "Bonjour tout le monde"
        assert b.transcripts.translation(new_aid, "en").segments[0].text == "Hello everyone"
        clip = b.db.query_one("SELECT * FROM clip WHERE project_id = ?", (np,))
        assert clip["source_asset_id"] == new_aid
        again = archive.import_project(b, tmp_path / "Sketch.studioproj", tmp_path / "Videos")
        assert again["reused"] == 1 and again["name"] == "Sketch (importé)"           # média reconnu, pas dupliqué
    finally:
        b.close()


def test_without_media_and_missing(tmp_path):
    from studiovideo.core import build_context
    a = build_context(tmp_path / "pc1")
    try:
        pid, aid, vid = _setup(a, tmp_path)
        res = archive.export_project(a, pid, tmp_path / "light.studioproj", include_media=False)
        assert res["archived"] == 0
    finally:
        a.close()
    vid.unlink()
    b = build_context(tmp_path / "pc2")
    try:
        out = archive.import_project(b, tmp_path / "light.studioproj", tmp_path / "Videos")
        assert out["missing"] == ["source.mp4"]
        tl = b.montage.load(out["project_id"])
        assert b.library.get(tl.segments[0].asset_id)["missing"] == 1
        out2 = archive.import_project(b, tmp_path / "light.studioproj", tmp_path / "Videos")
        assert out2["missing"] == ["source.mp4"]                                        # pas de conflit
        bad = tmp_path / "bad.studioproj"
        bad.write_bytes(b"pas un zip")
        with pytest.raises(archive.ArchiveError):
            archive.import_project(b, bad, tmp_path / "Videos")
    finally:
        b.close()
