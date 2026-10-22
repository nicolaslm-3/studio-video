"""Aperçus légers : critères, création automatique à l'import, lecture du proxy, export sur l'original."""
import subprocess
import time

import pytest

from studiovideo.media import ffmpeg, proxy

pytestmark = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")


def test_needed():
    row = {"kind": "video", "width": 1920, "height": 1080, "video_codec": "h264"}
    assert not proxy.needed(row)
    assert proxy.needed(dict(row, width=3840, height=2160))
    assert proxy.needed(dict(row, video_codec="hevc"))
    assert not proxy.needed(dict(row, kind="audio", width=None, height=None, video_codec=None))


def test_import_creates_proxy_and_player_uses_it(tmp_path):
    from studiovideo.core import build_context
    src = tmp_path / "big.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=2560x1440:d=2:r=25",
                    "-f", "lavfi", "-i", "sine=d=2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    app = build_context(tmp_path / "d")
    try:
        jid = app.jobs.submit("import_file", "Import", {"path": str(src)})
        end = time.time() + 60
        while time.time() < end:
            jobs = app.db.query("SELECT id, kind, status FROM job")
            if any(j["kind"] == "make_proxy" and j["status"] in ("succeeded", "failed") for j in jobs):
                break
            time.sleep(0.1)
        aid = app.jobs.get(jid).result["asset_id"]
        p = proxy.path_for(app.paths.proxies, aid)
        assert p.exists(), app.db.query("SELECT kind, status, error FROM job")
        info = ffmpeg.probe(p)
        assert min(info.width, info.height) == 540 and info.audio_tracks == 1
        row = app.library.get(aid)
        assert proxy.preview_path(app.paths.proxies, row) == str(p)
        from studiovideo.edit.model import Timeline
        tl = Timeline()
        tl.add_segment(aid, 0, 2)
        sources, _ = app.montage.sources_for(tl)
        assert sources[aid].path == str(src)                     # l'export utilise l'original
    finally:
        app.close()
