"""Mise en page : double cadrage d'une même source, deux vidéos empilées, image dans l'image (rendus réels)."""
import subprocess

import numpy as np
import pytest

from studiovideo.edit import compose
from studiovideo.edit.model import Timeline
from studiovideo.edit.render import Source, export, preview_frame, verify
from studiovideo.media import ffmpeg

needs_ffmpeg = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")


def _make(path, args):
    subprocess.run([ffmpeg.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *args, str(path)], check=True)
    return path


def run_cmd(cmd, on_line):
    p = subprocess.run(cmd, capture_output=True, text=True)
    for line in p.stderr.splitlines(keepends=True):
        on_line(line)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-1500:])
    return 0


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    if not ffmpeg.available():
        pytest.skip("FFmpeg absent")
    d = tmp_path_factory.mktemp("c")
    # débat filmé en 16:9 : intervenant A (rouge) à gauche, intervenant B (bleu) à droite
    debate = _make(d / "debate.mp4", [
        "-f", "lavfi", "-i", "color=c=red:s=320x360:r=25:d=3", "-f", "lavfi", "-i", "color=c=blue:s=320x360:r=25:d=3",
        "-f", "lavfi", "-i", "sine=frequency=300:duration=3",
        "-filter_complex", "[0:v][1:v]hstack=inputs=2[v]", "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"])
    game = _make(d / "game.mp4", ["-f", "lavfi", "-i", "color=c=lime:s=640x360:r=30:d=2",
                                  "-f", "lavfi", "-i", "sine=frequency=900:duration=2",
                                  "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"])
    return {1: Source(str(debate), 640, 360, 3.0, True), 2: Source(str(game), 640, 360, 2.0, True)}


def rgb(png, w, h):
    p = subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-i", str(png), "-f", "rawvideo", "-pix_fmt", "rgb24",
                        "-"], capture_output=True, check=True)
    return np.frombuffer(p.stdout, np.uint8).reshape(h, w, 3).astype(int)


def dominant(px) -> str:
    r, g, b = px.reshape(-1, 3).mean(axis=0)
    return max((("rouge", r), ("vert", g), ("bleu", b)), key=lambda x: x[1])[0]


def vertical(layout):
    tl = Timeline()
    tl.set_aspect("9:16", short_side=360)          # 360 × 640
    tl.format.fps = 25
    tl.export.preset = "ultrafast"
    tl.add_segment(1, 0.0, 2.0)
    tl.video.layout = layout
    return tl


def test_geometry_shared_by_render_and_preview():
    lay = compose.norm({"kind": "split", "ratio": 0.6})
    assert compose.zones(lay, 1080, 1920) == [(0, 0, 1080, 1152), (0, 1152, 1080, 768)]
    assert compose.zones({**lay, "swap": True}, 1080, 1920)[0] == (0, 1152, 1080, 768)
    assert compose.zones({"kind": "split"}, 1080, 1920)[1] == (0, 960, 1080, 960)      # 50/50 par défaut
    SW, SH, x, y = compose.view_crop(1920, 1080, 1080, 960, {"cx": 0.0, "cy": 0.5, "zoom": 1.0})
    assert (SH, x) == (960, 0) and SW >= 1080 and SW % 2 == 0                       # bord gauche, jamais de noir
    SW, SH, x, y = compose.view_crop(1920, 1080, 1080, 960, {"cx": 1.0, "cy": 0.5, "zoom": 2.0})
    assert x == SW - 1080
    cx, cy = compose.center_for(1920, 1080, 1080, 960, {"zoom": 1.0}, 0, 0)
    assert compose.view_crop(1920, 1080, 1080, 960, {"cx": cx, "cy": cy, "zoom": 1.0})[2:] == (0, 0)
    assert compose.norm({"kind": "nope", "ratio": 5})["kind"] == "single" and compose.norm({"ratio": 5})["ratio"] == 0.8
    tl = Timeline.from_dict(vertical({"kind": "split"}).to_dict())                 # sauvegardé avec le projet
    assert tl.video.layout == {"kind": "split"}


@needs_ffmpeg
def test_double_framing_same_source(media, tmp_path):
    """Intervenant A en haut, B en bas, depuis une seule vidéo ; un seul flux audio ; 9:16."""
    lay = {"kind": "split", "views": [{"cx": 0.2, "zoom": 1.0}, {"cx": 0.8, "zoom": 1.0}]}
    img = rgb(preview_frame(vertical(lay), media, 1.0, tmp_path / "p.png"), 360, 640)
    assert dominant(img[20:300]) == "rouge" and dominant(img[340:620]) == "bleu"
    img = rgb(preview_frame(vertical({**lay, "swap": True}), media, 1.0, tmp_path / "s.png"), 360, 640)
    assert dominant(img[20:300]) == "bleu" and dominant(img[340:620]) == "rouge"   # vues inversées
    img = rgb(preview_frame(vertical({**lay, "ratio": 0.7}), media, 1.0, tmp_path / "r.png"), 360, 640)
    assert dominant(img[400:440]) == "rouge" and dominant(img[470:630]) == "bleu"  # 70 % / 30 %
    tl = vertical(lay)
    out = tmp_path / "split.mp4"
    rep = export(tl, media, None, out, run_cmd)
    assert (rep["width"], rep["height"]) == (360, 640) and abs(rep["duration"] - 2.0) < 0.15
    assert ffmpeg.probe(out).audio_tracks == 1                                     # le son n'est pas doublé
    verify(out, tl)


@needs_ffmpeg
def test_two_sources_and_picture_in_picture(media, tmp_path):
    lay = {"kind": "split2", "asset2": 2, "views": [{"cx": 0.2}, {}], "audio": "both"}
    img = rgb(preview_frame(vertical(lay), media, 1.0, tmp_path / "two.png"), 360, 640)
    assert dominant(img[20:300]) == "rouge" and dominant(img[340:620]) == "vert"
    out = tmp_path / "two.mp4"
    rep = export(vertical(lay), media, None, out, run_cmd)
    assert abs(rep["duration"] - 2.0) < 0.15          # seconde vidéo plus courte (2 s) : dernière image tenue
    lay = {"kind": "pip", "asset2": 2, "views": [{"cx": 0.8}, {}], "pip": {"x": 0.5, "y": 0.05, "w": 0.45,
                                                                            "h": 0.15}, "border": 6}
    img = rgb(preview_frame(vertical(lay), media, 1.0, tmp_path / "pip.png"), 360, 640)
    assert dominant(img[300:600]) == "bleu"                                        # vidéo principale
    assert dominant(img[45:115, 200:330]) == "vert"                               # incrustation en haut à droite
    assert img[32, 260].min() > 200                                                # contour blanc
    export(vertical(lay), media, None, tmp_path / "pip.mp4", run_cmd)
