"""Dossier surveillé : fichiers stables seulement, temporaires et doublons ignorés, import par la fenêtre."""
import os
import shutil
import subprocess
import time

import pytest

from studiovideo.media import ffmpeg
from studiovideo.media.watch import FolderWatcher

pytestmark = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")


def _vid(path, f=300):
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x120:d=1:r=25",
                    "-f", "lavfi", "-i", f"sine=f={f}:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(path)], check=True)
    return path


def _age(p, s=30):
    t = time.time() - s
    os.utime(p, (t, t))


def test_scan_rules(tmp_path):
    from studiovideo.core import build_context
    app = build_context(tmp_path / "d")
    try:
        w = tmp_path / "depot"
        (w / "telephone").mkdir(parents=True)
        a = _vid(w / "a.mp4")
        b = _vid(w / "telephone" / "b.mp4", 500)
        (w / "c.mp4.part").write_bytes(b"x" * 100)
        (w / "notes.docx").write_bytes(b"x" * 100)
        known = _vid(tmp_path / "deja.mp4", 700)
        app.importer().import_file(known)
        shutil.copy(known, w / "copie.mp4")
        for p in w.rglob("*"):
            if p.is_file():
                _age(p)
        watcher = FolderWatcher(app.db, app.workspace_id)
        assert watcher.scan(w) == []                              # 1er passage : on attend la stabilité
        assert sorted(x.name for x in watcher.scan(w)) == ["a.mp4", "b.mp4"]
        assert watcher.scan(w) == []                              # pas deux fois
        fresh = _vid(w / "nouveau.mp4", 900)                      # copie toute récente
        watcher.scan(w)
        assert watcher.scan(w) == []
        _age(fresh)
        assert watcher.scan(w) == []                              # date changée : encore un passage
        assert [x.name for x in watcher.scan(w)] == ["nouveau.mp4"]
        assert a.exists() and b.exists()                          # rien n'est déplacé
    finally:
        app.close()
