"""Fondus d'extrait : niveau dans le temps, image et son réellement atténués à l'export, coupe et silences."""
import subprocess

import numpy as np
import pytest

from studiovideo.edit.model import Segment, Timeline
from studiovideo.media import ffmpeg


def test_fade_level_and_split():
    s = Segment(asset_id=1, src_in=10, src_out=14, fade_in=1.0, fade_out=3.0)
    assert s.fades() == (1.0, 2.0)                                   # borné à la moitié
    assert s.fade_level(0) == 0 and s.fade_level(0.5) == 0.5 and s.fade_level(1.5) == pytest.approx(1.0)
    assert s.fade_level(3.0) == pytest.approx(0.5)
    tl = Timeline()
    tl.segments.append(s)
    left, right = tl.split_at(2.0)
    assert (left.fade_in, left.fade_out, right.fade_in, right.fade_out) == (1.0, 0.0, 0.0, 3.0)


@pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")
def test_fade_rendered(tmp_path):
    from studiovideo.edit import render
    src = tmp_path / "s.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=white:s=160x160:d=2:r=25",
                    "-f", "lavfi", "-i", "sine=f=440:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(src)], check=True)
    tl = Timeline()
    tl.set_aspect("1:1", 160)
    tl.format.fps = 25
    seg = tl.add_segment(1, 0, 2)
    seg.fade_in, seg.fade_out = 0.5, 0.5
    sources = {1: render.Source(path=str(src), width=160, height=160, duration=2.0, has_audio=True)}
    gb = render.GraphBuilder(tl, sources, None, [])
    inputs, vout, aout = gb.build(normalize=False)
    out = tmp_path / "o.mkv"
    graph = ";".join(gb.graph)
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", vout,
                    "-map", aout, "-c:v", "ffv1", "-c:a", "pcm_s16le", str(out)], check=True)
    raw = subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-i", str(out), "-f", "rawvideo", "-pix_fmt", "gray",
                          "-"], capture_output=True, check=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, 160 * 160).mean(axis=1)
    assert fr[0] < 30 and fr[25] > 220 and fr[-1] < 60 and 90 < fr[-7] < 170   # noir → blanc → noir
    pcm = subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-i", str(out), "-ac", "1", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    a = np.abs(np.frombuffer(pcm, np.int16).astype(float))
    n = len(a)
    assert a[: n // 40].mean() < a[n // 2 - n // 20: n // 2 + n // 20].mean() * 0.3


@pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")
def test_preview_frame_shows_fade_and_zoom(tmp_path):
    from PIL import Image

    from studiovideo.edit import render
    src = tmp_path / "s.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=white:s=160x160:d=4:r=25",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)
    tl = Timeline()
    tl.set_aspect("1:1", 160)
    seg = tl.add_segment(1, 0, 4)
    seg.fade_in = 1.0
    sources = {1: render.Source(path=str(src), width=160, height=160, duration=4.0, has_audio=False)}
    levels = []
    for t in (0.5, 2.0):
        out = render.preview_frame(tl, sources, t, tmp_path / f"p{t}.png")
        levels.append(np.asarray(Image.open(out).convert("L"), float).mean())
    assert 100 < levels[0] < 160 and levels[1] > 230


@pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")
def test_export_still_job_full_resolution(tmp_path):
    import time

    from PIL import Image

    from studiovideo.core import build_context
    app = build_context(tmp_path / "d")
    try:
        src = tmp_path / "s.mp4"
        subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:d=2:r=25",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)
        aid = app.importer().import_file(src).asset_id
        tl = Timeline()
        tl.set_aspect("9:16", 1080)
        tl.add_segment(aid, 0, 2)
        dest = tmp_path / "out" / "image.png"
        jid = app.jobs.submit("export_still", "Image", {"timeline": tl.to_dict(), "t": 1.0, "dest": str(dest)})
        end = time.time() + 30
        while app.jobs.get(jid).status in ("pending", "running") and time.time() < end:
            time.sleep(0.05)
        assert app.jobs.get(jid).status == "succeeded", app.jobs.get(jid).error
        assert Image.open(dest).size == (1080, 1920)
    finally:
        app.close()
