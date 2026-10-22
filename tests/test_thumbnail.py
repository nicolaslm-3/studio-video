"""Miniature : image réelle avec titre, < 2 Mo ; dialogue ; envoi YouTube et couverture TikTok."""
import subprocess

import pytest

from studiovideo.media import ffmpeg
from studiovideo.publish import thumbnail

pytestmark = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")


def _video(path, size="1280x720"):
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:d=3:r=25",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_make_thumbnail(tmp_path):
    from PySide6.QtGui import QImage
    v = _video(tmp_path / "v.mp4")
    plain = thumbnail.make(v, 1.0, "", tmp_path / "a.jpg")
    titled = thumbnail.make(v, 1.0, "Le sketch du dentiste", tmp_path / "b.jpg")
    a, b = QImage(str(plain)), QImage(str(titled))
    assert (a.width(), a.height()) == (1280, 720) and b.size() == a.size()
    assert titled.stat().st_size <= thumbnail.MAX_BYTES
    diff = sum(a.pixel(x, 530) != b.pixel(x, 530) for x in range(100, 1180, 10))     # titre en bas
    assert diff > 20
    vert = thumbnail.make(_video(tmp_path / "w.mp4", "720x1280"), 0.5, "", tmp_path / "c.jpg")
    assert QImage(str(vert)).height() == 1280


def test_thumbnail_dialog_saves(tmp_path, qtbot):
    from studiovideo.core import build_context
    from studiovideo.ui.thumbnail_dialog import ThumbnailDialog
    app = build_context(tmp_path / "d")
    try:
        c = app.concepts.create_concept("C")
        m = app.publications.create_master(c.id, "Titre")
        v = _video(tmp_path / "v.mp4")
        d = ThumbnailDialog(app, m, str(v), "Titre")
        qtbot.addWidget(d)
        d.slider.setValue(15)
        d._save()
        row = app.publications.master(m)
        assert row["cover_s"] == 1.5 and row["thumbnail"].endswith(f"publication_{m}.jpg")
    finally:
        app.close()


def test_youtube_thumbnail_and_tiktok_cover(tmp_path, monkeypatch):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from studiovideo.publish import tiktok, youtube
    got = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n)
            if self.path.startswith("/upload/youtube/v3/thumbnails/set"):
                got["thumb"] = (self.path, self.headers["Content-Type"], len(raw))
                code = 403 if "videoId=NOPE" in self.path else 200
                self.send_response(code)
                self.end_headers()
                self.wfile.write(b"{}")
                return
            if self.path.startswith("/v2/post/publish/creator_info"):
                body = {"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE"]}, "error": {"code": "ok"}}
            elif self.path.startswith("/v2/post/publish/video/init"):
                got["init"] = json.loads(raw)
                body = {"error": {"code": "invalid_params", "message": "stop"}}
            else:
                body = {}
            b = json.dumps(body).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    monkeypatch.setenv("STUDIOVIDEO_YOUTUBE_TEST_URL", url)
    monkeypatch.setenv("STUDIOVIDEO_TIKTOK_TEST_URL", url)
    try:
        img = thumbnail.make(_video(tmp_path / "v.mp4"), 1, "T", tmp_path / "t.jpg")
        youtube.set_thumbnail("AT", "VID1", img)
        assert "videoId=VID1" in got["thumb"][0] and got["thumb"][1] == "image/jpeg" and got["thumb"][2] > 1000
        with pytest.raises(youtube.YouTubeError, match="validée"):
            youtube.set_thumbnail("AT", "NOPE", img)
        with pytest.raises(tiktok.TikTokError):
            tiktok.upload(tmp_path / "v.mp4", "x", "PUBLIC_TO_EVERYONE", "A", cover_ms=1500)
        assert got["init"]["post_info"]["video_cover_timestamp_ms"] == 1500
    finally:
        srv.shutdown()
