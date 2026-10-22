"""Look LUT (.cube) : appliqué à l'export, chemin avec deux-points et apostrophe, fichier absent ignoré."""
import subprocess

import numpy as np
import pytest

from studiovideo.edit import render
from studiovideo.edit.model import Timeline
from studiovideo.media import ffmpeg

pytestmark = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")

INVERT = "LUT_3D_SIZE 2\n" + "".join(f"{1 - r} {1 - g} {1 - b}\n" for b in (0, 1) for g in (0, 1) for r in (0, 1))


def _mean(tl, src):
    chain = render.video_chain(tl, render.Source(path=str(src), width=64, height=64, duration=1, has_audio=False), "0",
                               tl.segments[0])
    raw = subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-i", str(src), "-vf", chain, "-frames:v", "1", "-f",
                          "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).astype(float).mean()


def test_lut_applied(tmp_path):
    src = tmp_path / "w.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=white:s=64x64:d=1:r=10",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    d = tmp_path / "a:b c'd"
    d.mkdir()
    lut = d / "inverse.cube"
    lut.write_text(INVERT)
    tl = Timeline()
    tl.set_aspect("1:1", 64)
    tl.add_segment(1, 0, 1)
    assert _mean(tl, src) > 230
    tl.video.lut = str(lut)
    assert _mean(tl, src) < 25                                   # blanc → noir
    tl.video.lut = str(d / "absent.cube")
    assert _mean(tl, src) > 230                                  # fichier disparu : export quand même
