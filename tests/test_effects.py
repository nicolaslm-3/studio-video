"""Effets de l'image (bouton robot) : miroir, légère rotation sans coins vides, mouvement subtil, filtre et son
intensité, vitesse ; ils touchent la vidéo, jamais les textes."""
import subprocess

import numpy as np
import pytest

from studiovideo.edit import render
from studiovideo.edit.model import Timeline
from studiovideo.media import ffmpeg

needs_ffmpeg = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")


def _source(tmp_path, color, name="s.mp4", d=2, post=""):
    src = tmp_path / name
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color={color}:s=160x160:d={d}:r=25" + (f",{post}" if post else ""),
                    "-f", "lavfi", "-i", f"sine=f=440:d={d}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(src)], check=True)
    return render.Source(path=str(src), width=160, height=160, duration=float(d), has_audio=True)


def _frames(tl, sources, tmp_path, rgb=False):
    gb = render.GraphBuilder(tl, sources, None, [])
    inputs, vout, aout = gb.build(normalize=False)
    out = tmp_path / "o.mkv"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", *inputs, "-filter_complex", ";".join(gb.graph),
                    "-map", vout, "-map", aout, "-c:v", "ffv1", "-c:a", "pcm_s16le", str(out)], check=True)
    fmt, k = ("rgb24", 3) if rgb else ("gray", 1)
    raw = subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-i", str(out), "-f", "rawvideo", "-pix_fmt", fmt, "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, 160, 160, k)


def _tl():
    tl = Timeline()
    tl.set_aspect("1:1", 160)
    tl.format.fps = 25
    tl.add_segment(1, 0, 2)
    return tl


def test_old_projects_load_without_effects():
    tl = Timeline.from_dict({"video": {"mirror": True}})
    assert tl.video.tilt == 0 and tl.video.pan == 0 and tl.video.look == "" and tl.video.speed == 1.0
    tl.video.speed = 2.0
    tl.add_segment(1, 0, 10)
    assert render.output_duration(tl) == 5.0 and tl.duration == 10.0    # le montage reste en temps d'origine


@needs_ffmpeg
def test_mirror_tilt_pan_and_look(tmp_path):
    src = {1: _source(tmp_path, "black", post="drawbox=x=80:y=0:w=80:h=160:color=white:t=fill")}
    tl = _tl()
    tl.video.mirror = True
    fr = _frames(tl, src, tmp_path)
    assert fr[10, 80, 20, 0] > 200 and fr[10, 80, 140, 0] < 50              # miroir : le blanc passe à gauche
    white = {1: _source(tmp_path, "white", "w.mp4")}
    tl = _tl()
    tl.video.tilt = 6.0
    fr = _frames(tl, white, tmp_path)
    assert min(fr[10, 1, 1, 0], fr[10, 158, 158, 0]) > 200                  # pas de coins noirs
    tl = _tl()
    tl.video.pan = 1.0
    fr = _frames(tl, src, tmp_path)
    assert np.abs(fr[0].astype(int) - fr[-1].astype(int)).mean() > 1         # le cadre a glissé
    red = {1: _source(tmp_path, "red", "r.mp4")}
    tl = _tl()
    tl.video.look, tl.video.look_amount = "nb", 1.0
    px = _frames(tl, red, tmp_path, rgb=True)[10, 80, 80].astype(int)
    assert abs(px[0] - px[1]) < 25                                         # noir et blanc
    tl.video.look_amount = 0.5
    half_px = _frames(tl, red, tmp_path, rgb=True)[10, 80, 80].astype(int)
    assert half_px[0] - half_px[1] > 40 and half_px[0] - half_px[1] < 200   # à moitié


@needs_ffmpeg
def test_speed_changes_exported_duration_and_texts_are_not_mirrored(tmp_path):
    src = {1: _source(tmp_path, "gray", d=4)}
    tl = Timeline()
    tl.set_aspect("1:1", 160)
    tl.format.fps = 25
    tl.add_segment(1, 0, 4)
    tl.video.speed = 2.0
    tl.video.mirror = True
    tl.add_text("Bonjour", 0, 4)
    tl.audio.normalize = False

    def run(cmd, on_line):
        p = subprocess.run(cmd, capture_output=True, text=True)
        assert p.returncode == 0, p.stderr[-500:]
        return 0
    rep = render.export(tl, src, None, tmp_path / "out.mp4", run)
    assert abs(rep["duration"] - 2.0) < 0.2                                 # deux fois plus vite
    gb = render.GraphBuilder(tl, src, None, [(tl.texts[0], tmp_path / "t.png")])
    gb.build(normalize=False)
    seg_line = next(g for g in gb.graph if g.startswith("[0:v:0]"))
    text_lines = [g for g in gb.graph if "overlay" in g]
    assert "hflip" in seg_line and text_lines and not any("hflip" in g for g in text_lines)


def test_emojis_and_flags_in_texts():
    """Emojis en couleur et drapeaux en image (Windows écrirait « FR ») ; le texte autour reste un tracé."""
    from studiovideo.edit import emoji
    from studiovideo.edit.model import TextItem
    from studiovideo.edit.textrender import render_text_image
    assert emoji.runs("Go \U0001F1EB\U0001F1F7!") == [("Go ", False), ("\U0001F1EB\U0001F1F7", True), ("!", False)]
    assert emoji.runs("\U0001F44D\U0001F3FD ok") == [("\U0001F44D\U0001F3FD", True), (" ok", False)]
    assert emoji.flag_code("\U0001F1EB\U0001F1F7") == "fr" and (emoji.FLAG_DIR / "fr.png").exists()
    img = render_text_image(TextItem(text="\U0001F1EB\U0001F1F7", position="center", size=200, outline=0,
                                     color="#FFFFFF"), 400, 400)
    cols = {img.pixelColor(x, 200).name() for x in range(100, 300, 4) if img.pixelColor(x, 200).alpha() > 200}
    blue = any(int(c[5:7], 16) > 120 and int(c[1:3], 16) < 90 for c in cols)
    red = any(int(c[1:3], 16) > 180 and int(c[3:5], 16) < 110 for c in cols)
    assert blue and red                                          # bleu et rouge : le drapeau, pas « FR » blanc
