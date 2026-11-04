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
    old = compose.norm({"kind": "split", "swap": True, "views": [{"cx": 0.1}, {"cx": 0.9}]})   # ancienne forme
    assert old["kind"] == "stack" and old["count"] == 2 and [v["cx"] for v in old["views"][:2]] == [0.9, 0.1]
    assert compose.norm({"kind": "split2", "asset2": 7})["views"][1]["asset"] == 7
    assert compose.zones({"kind": "stack", "count": 4}, 1080, 1920) == [
        (0, 0, 1080, 480), (0, 480, 1080, 480), (0, 960, 1080, 480), (0, 1440, 1080, 480)]
    assert compose.zones({"kind": "stack", "count": 3}, 1080, 1920)[2] == (0, 1280, 1080, 640)
    assert compose.source_crop(1920, 1080, {"crop_l": 0.25, "crop_r": 0.25}) == (960, 1080, 480, 0)
    lay4 = {"kind": "stack", "count": 3, "views": [{}, {"asset": 5}, {"asset": 5}, {"asset": 9}]}
    assert compose.others(lay4) == [5] and [compose.view_source(lay4, i) for i in range(3)] == [0, 1, 1]
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


@needs_ffmpeg
def test_four_views_each_cropped(media, tmp_path):
    """Jusqu'à 4 vues empilées, chacune de sa vidéo et rognée à gauche / à droite."""
    lay = {"kind": "stack", "count": 4, "audio": "both", "views": [
        {"crop_r": 0.45, "zoom": 1.0},                      # la vidéo du montage, rognée à droite : A (rouge)
        {"crop_l": 0.45, "zoom": 1.0},                      # rognée à gauche : B (bleu)
        {"asset": 2, "zoom": 1.0},                          # une autre vidéo (verte)
        {"crop_l": 0.45, "cx": 1.0}]}
    img = rgb(preview_frame(vertical(lay), media, 1.0, tmp_path / "q.png"), 360, 640)
    assert [dominant(img[y0 + 20:y0 + 140]) for y0 in (0, 160, 320, 480)] == ["rouge", "bleu", "vert", "bleu"]
    out = tmp_path / "four.mp4"
    rep = export(vertical(lay), media, None, out, run_cmd)
    assert (rep["width"], rep["height"]) == (360, 640) and abs(rep["duration"] - 2.0) < 0.15
    assert ffmpeg.probe(out).audio_tracks == 1
    only = {"kind": "stack", "count": 2, "views": [{"asset": 2}, {"asset": 2, "crop_t": 0.3}]}
    img = rgb(preview_frame(vertical(only), media, 1.0, tmp_path / "o.png"), 360, 640)   # sans la vidéo du montage
    assert dominant(img[20:300]) == "vert" and dominant(img[340:620]) == "vert"


def test_grid_tracks_and_zoom_out():
    """Côte à côte (2 en haut, 2 en bas…), pistes vidéo 2 à 4 de la timeline, zoom sous 100 %."""
    assert compose.zones({"kind": "grid", "count": 2}, 1920, 1080) == [(0, 0, 960, 1080), (960, 0, 960, 1080)]
    assert compose.zones({"kind": "grid", "count": 3}, 1920, 1080)[2] == (0, 540, 1920, 540)
    assert compose.zones({"kind": "grid", "count": 4}, 1920, 1080)[3] == (960, 540, 960, 540)
    # vidéos importées : la première libre des pistes 2 à 4 ; format large → côte à côte
    lay, j = compose.place_track(None, 7, 1920, 1080)
    assert j == 1 and lay["kind"] == "grid" and lay["count"] == 2 and lay["views"][1]["asset"] == 7
    lay, j = compose.place_track(lay, 8, 1920, 1080, start=4.0)
    assert j == 2 and lay["count"] == 3 and compose.others(lay) == [7, 8] and compose.track_start(lay, 8) == 4.0
    assert compose.place_track(lay, 8, 1920, 1080)[1] is None               # déjà posée
    lay, _ = compose.place_track(lay, 9, 1920, 1080)
    assert compose.place_track(lay, 10, 1920, 1080)[1] is None              # 4 pistes occupées
    assert compose.place_track(None, 7, 1080, 1920)[0]["kind"] == "stack"   # format vertical → empilées
    # instant montré : la piste commence à 4 s du montage ; sans piste, calée sur la source (+ décalage)
    assert compose.other_time(lay, 8, src_t=30.0, t_line=6.0) == 2.0
    assert compose.other_time(lay, 8, src_t=30.0, t_line=1.0) == -3.0          # pas encore commencée
    assert compose.other_time({"kind": "stack", "offset": 1.5, "views": [{}, {"asset": 3}]}, 3, 30.0, 6.0) == 31.5
    lay = compose.remove_track(lay, 2)
    assert compose.others(lay) == [7, 9] and lay["count"] == 3
    lay = compose.remove_track(compose.remove_track(lay, 1), 3)
    assert lay["kind"] == "single" and not lay["tracks"]
    # zoom sous 100 % : l'image plus petite que sa zone, centrée (marge noire)
    SW, SH, x, y = compose.view_crop(640, 360, 400, 400, {"zoom": 0.5, "cx": 0.5, "cy": 0.5})
    assert (SW, SH) == (356, 200) and x == -22 and y == -100
    assert compose.norm({"views": [{"zoom": 0.05}]})["views"][0]["zoom"] == compose.ZOOM_MIN


@needs_ffmpeg
def test_grid_export_track_start_and_zoom_out(media, tmp_path):
    """Côte à côte en 16:9 ; la vidéo de la piste 2 commence à 1 s (avant : sa première image) ; une vue réduite."""
    tl = Timeline()
    tl.set_aspect("16:9", short_side=360)          # 640 × 360
    tl.format.fps = 25
    tl.export.preset = "ultrafast"
    tl.add_segment(1, 0.0, 2.0)
    lay, _ = compose.place_track(None, 2, 640, 360, start=1.0)
    lay["views"][0].update({"crop_r": 0.45})                         # A (rouge)
    tl.video.layout = lay
    img = rgb(preview_frame(tl, media, 1.5, tmp_path / "g.png"), 640, 360)
    assert dominant(img[40:320, 20:300]) == "rouge" and dominant(img[40:320, 340:620]) == "vert"
    out = tmp_path / "grid.mp4"
    rep = export(tl, media, None, out, run_cmd)
    assert (rep["width"], rep["height"]) == (640, 360) and abs(rep["duration"] - 2.0) < 0.15
    lay["views"][1]["zoom"] = 0.5                                    # la vidéo verte réduite dans sa vue
    tl.video.layout = lay
    img = rgb(preview_frame(tl, media, 1.5, tmp_path / "z.png"), 640, 360)
    assert img[20, 480].max() < 40 and dominant(img[170:190, 460:500]) == "vert"   # du noir autour, vert au centre


@needs_ffmpeg
def test_zoom_out_blur_and_vivid(media, tmp_path):
    """Bouton robot : zoom sous 100 % (la vidéo réduite, du noir autour), flou, filtre « Vif »."""
    tl = vertical({})
    tl.video.zoom = 0.5
    img = rgb(preview_frame(tl, media, 1.0, tmp_path / "z.png"), 360, 640)
    assert img[20, 180].max() < 40 and img[320, 180].max() > 100        # bord noir, image au centre
    tl.video.zoom, tl.video.blur = 1.0, 1.0
    img = rgb(preview_frame(tl, media, 1.0, tmp_path / "b.png"), 360, 640)
    mid = img[320, 150:210]                                               # frontière rouge / bleu adoucie
    assert ((mid[:, 0] > 60) & (mid[:, 2] > 60)).any()
    from studiovideo.edit.render import _look_chain
    assert "gblur=sigma=10" in _look_chain(tl)            # 30 pour 1080 de large, ici 360
    tl.video.blur, tl.video.look, tl.video.look_amount = 0.0, "vif", 1.0
    assert "saturation=1.8" in _look_chain(tl) and "gblur" not in _look_chain(tl)
    export(tl, media, None, tmp_path / "v.mp4", run_cmd)
