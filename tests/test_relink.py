"""Médias déplacés : retrouver un fichier (même empreinte), refus d'un fichier différent, recherche par dossier."""
import shutil
import subprocess

import pytest

from studiovideo.media import ffmpeg

pytestmark = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")


def _vid(path, freq):
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x120:d=1:r=25",
                    "-f", "lavfi", "-i", f"sine=f={freq}:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(path)], check=True)
    return path


def test_relink(tmp_path):
    from studiovideo.core import build_context
    app = build_context(tmp_path / "d")
    try:
        a1 = app.importer().import_file(_vid(tmp_path / "a.mp4", 300)).asset_id
        a2 = app.importer().import_file(_vid(tmp_path / "b.mp4", 500)).asset_id
        moved = tmp_path / "ailleurs"
        moved.mkdir()
        shutil.move(tmp_path / "a.mp4", moved / "a.mp4")
        (moved / "sous").mkdir()
        shutil.move(tmp_path / "b.mp4", moved / "sous" / "b.mp4")
        app.importer().refresh_missing()
        assert app.library.get(a1)["missing"] == 1 and app.library.get(a2)["missing"] == 1
        other = _vid(tmp_path / "autre.mp4", 900)
        with pytest.raises(ValueError, match="pas identique"):
            app.library.relink(a1, other)
        assert app.library.relink(a1, moved / "a.mp4") == {"same": True}
        r = app.library.get(a1)
        assert r["missing"] == 0 and r["path"] == str(moved / "a.mp4")
        assert app.library.relink_folder(moved) == [a2]
        assert app.library.get(a2)["path"] == str(moved / "sous" / "b.mp4")
        assert app.library.relink(a2, other, force=True) == {"same": False}       # remplacement voulu
    finally:
        app.close()
