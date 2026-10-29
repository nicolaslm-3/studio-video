"""Moteur de rendu : exports réels avec FFmpeg (pas de simulation)."""
import subprocess

import pytest

from studiovideo.edit.model import Timeline, apply_concept_defaults, concept_defaults_from
from studiovideo.edit.render import RenderError, Source, analyze_audio, export, preview_frame, verify
from studiovideo.media import ffmpeg

pytestmark = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")


def _make(path, args):
    subprocess.run([ffmpeg.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *args, str(path)], check=True)
    return path


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("m")
    wide = _make(d / "wide.mp4", ["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=4",
                                  "-f", "lavfi", "-i", "sine=frequency=300:duration=4:sample_rate=44100",
                                  "-af", "volume=-26dB", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                                  "-c:a", "aac", "-shortest"])
    mute = _make(d / "mute.mp4", ["-f", "lavfi", "-i", "smptebars=size=480x640:rate=30:duration=2",
                                  "-c:v", "libx264", "-pix_fmt", "yuv420p"])
    music = _make(d / "music.m4a", ["-f", "lavfi", "-i", "sine=frequency=880:duration=1.5", "-c:a", "aac"])
    src = {1: Source(str(wide), 640, 360, 4.0, True), 2: Source(str(mute), 480, 640, 2.0, False)}
    return src, Source(str(music), None, None, 1.5, True)


def run_cmd(cmd, on_line):
    p = subprocess.run(cmd, capture_output=True, text=True)
    for line in p.stderr.splitlines(keepends=True):
        on_line(line)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-1500:])
    return 0


def small(tl, aspect="9:16"):
    tl.set_aspect(aspect, short_side=360)
    tl.format.fps = 25
    tl.export.preset = "ultrafast"
    return tl


def test_full_export_vertical(media, tmp_path):
    sources, music = media
    tl = small(Timeline())
    tl.add_segment(1, 0.5, 2.5)
    tl.add_segment(2, 0.0, 1.5)          # sans piste audio
    tl.add_segment(1, 3.0, 4.0)
    tl.video.mirror = True
    tl.video.saturation = 1.2
    tl.add_text("Titre du sketch", 0, 2, position="top", box=True, fade=0.3)
    tl.add_text("Abonnez-vous", 3.5, 4.5, position="bottom")
    tl.music.asset_id = 99
    tl.music.duck = True
    steps = []
    rep = export(tl, sources, music, tmp_path / "out.mp4", run_cmd, lambda f, m: steps.append(f))
    assert (rep["width"], rep["height"]) == (360, 640)
    assert abs(rep["duration"] - 4.5) < 0.2
    assert rep["video_codec"] == "h264" and rep["audio_codec"] == "aac"
    assert abs(rep["lufs"] - tl.audio.target_lufs) < 1.5, rep
    assert rep["true_peak"] <= -0.4, rep          # pas de saturation introduite
    assert steps[-1] == 1.0 and steps == sorted(steps)
    assert not (tmp_path / "out.partial.mp4").exists()


@pytest.mark.parametrize("fit", ["fill", "fit", "blur"])
@pytest.mark.parametrize("aspect", ["9:16", "1:1", "16:9"])
def test_fit_modes_and_aspects(media, tmp_path, fit, aspect):
    sources, _ = media
    tl = small(Timeline(), aspect)
    tl.format.fit = fit
    tl.video.zoom = 1.2
    tl.video.offset_x = 0.5
    tl.video.rotation = 90 if aspect == "16:9" else 0
    tl.add_segment(1, 0, 1)
    tl.add_segment(2, 0, 1)
    tl.audio.normalize = False
    rep = export(tl, sources, None, tmp_path / f"{fit}.mp4", run_cmd)
    assert (rep["width"], rep["height"]) == (tl.format.width, tl.format.height)


def test_preview_frame_matches_format(media, tmp_path):
    sources, _ = media
    tl = small(Timeline())
    tl.add_segment(1, 0, 2)
    tl.add_text("Bonjour", 0, 2)
    p = preview_frame(tl, sources, 1.0, tmp_path / "p.png", max_height=320)
    info = ffmpeg.probe(p)
    assert info.height == 320 and info.width == 180


def test_audio_analysis_flags_quiet_and_silence(media):
    sources, _ = media
    tl = Timeline()
    tl.add_segment(1, 0, 4)
    tl.add_segment(2, 0, 2)  # 2 s sans son -> pas assez long
    tl.add_segment(2, 0, 2)  # 4 s de silence d'affilée
    rep = analyze_audio(tl, sources, None)
    assert rep["lufs"] < -20
    assert any("faible" in i for i in rep["issues"])
    assert any("silence" in i for i in rep["issues"])


def test_silent_timeline_exports_without_normalization(media, tmp_path):
    sources, _ = media
    tl = small(Timeline())
    tl.add_segment(2, 0, 1)
    rep = export(tl, sources, None, tmp_path / "silent.mp4", run_cmd)
    assert rep["duration"] > 0.9


def test_failed_render_leaves_no_file(media, tmp_path):
    sources, _ = media
    tl = small(Timeline())
    tl.add_segment(1, 0, 1)

    def failing(cmd, on_line):
        raise RuntimeError("ffmpeg a échoué")
    with pytest.raises(RuntimeError):
        export(tl, sources, None, tmp_path / "ko.mp4", failing)
    assert list(tmp_path.glob("ko*")) == []


def test_verify_rejects_wrong_dimensions(media, tmp_path):
    sources, _ = media
    tl = small(Timeline())
    tl.add_segment(1, 0, 1)
    tl.audio.normalize = False
    rep = export(tl, sources, None, tmp_path / "a.mp4", run_cmd)
    tl.set_aspect("1:1", 360)
    with pytest.raises(RenderError, match="dimensions"):
        verify(tmp_path / "a.mp4", tl)
    assert rep


def test_concept_defaults_roundtrip():
    a = Timeline()
    a.set_aspect("1:1")
    a.video.mirror = True
    a.audio.target_lufs = -16
    a.add_text("x", font="Arial", color="#FFCC00")
    a.add_segment(1, 0, 2)
    d = concept_defaults_from(a)
    b = apply_concept_defaults(Timeline(), d)
    assert b.format.aspect == "1:1" and b.video.mirror and b.audio.target_lufs == -16
    assert b.segments == [] and b.texts == []
    assert d["text_style"]["color"] == "#FFCC00" and "text" not in d["text_style"]


def test_masks_hide_burned_text(media, tmp_path):
    """Des « traits » de texte incrustés (rayures fines, très contrastées) deviennent illisibles."""
    import numpy as np
    src_path = _make(tmp_path / "burn.mp4", ["-f", "lavfi", "-i", "color=c=0x336699:s=640x360:r=25:d=1",
                                             "-vf", ",".join(f"drawbox=x={100 + 12 * k}:y=285:w=3:h=40:color=white@1:"
                                                             "t=fill" for k in range(36)),
                                             "-c:v", "libx264", "-pix_fmt", "yuv420p"])
    srcs = {1: Source(str(src_path), 640, 360, 1.0, False)}

    def band_mean(png):
        p = subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-i", str(png), "-f", "rawvideo", "-pix_fmt", "gray",
                            "-"], capture_output=True, check=True)
        img = np.frombuffer(p.stdout, np.uint8).reshape(360, 640)
        return float(img[288:322, 110:520].std()), float(img[50:150, 50:600].mean())
    tl = Timeline()
    tl.set_aspect("16:9", 360)
    tl.add_segment(1, 0, 1)
    plain = band_mean(preview_frame(tl, srcs, 0.5, tmp_path / "p0.png"))
    assert plain[0] > 50                                      # traits nets et contrastés
    for mode in ("blur", "delogo", "box"):
        tl.video.masks = [{"x": 0.14, "y": 0.75, "w": 0.72, "h": 0.17, "mode": mode}]
        band, rest = band_mean(preview_frame(tl, srcs, 0.5, tmp_path / f"p_{mode}.png"))
        assert band < plain[0] / 4, (mode, band, plain[0])    # traits effacés ou noyés dans le flou
        assert abs(rest - plain[1]) < 3                       # le reste de l'image est intact
    out = tmp_path / "masked.mp4"
    export(tl, srcs, None, out, run_cmd)
    assert verify(out, tl)["width"] == 640


def test_detect_burned_text_and_logo(tmp_path):
    """Logo fixe en haut à droite + sous-titres intermittents en bas, sur un fond qui bouge."""
    from studiovideo.edit import textdetect
    # logo à traits de 3 px (1 px après réduction à 320 px serait au seuil de détection) ; encodage sur un seul fil et
    # de bonne qualité : la même vidéo quel que soit le nombre de cœurs de la machine (CI)
    logo = ",".join(f"drawbox=x={540 + 6 * k}:y=20:w=3:h=24:color=white@1:t=fill" for k in range(12))
    s1 = ",".join(f"drawbox=x={120 + 14 * k}:y=300:w=4:h=26:color=white@1:t=fill:enable='between(t,1,3)'"
                  for k in range(28))
    s2 = ",".join(f"drawbox=x={150 + 11 * k}:y=302:w=3:h=24:color=yellow@1:t=fill:enable='between(t,5,7)'"
                  for k in range(30))
    src = "gradients=s=640x360:r=25:d=8:speed=0.25:n=4:seed=3"
    enc = ["-c:v", "libx264", "-crf", "16", "-threads", "1", "-pix_fmt", "yuv420p"]
    burned = _make(tmp_path / "burn.mp4", ["-f", "lavfi", "-i", src, "-vf", ",".join([logo, s1, s2]), *enc])
    clean = _make(tmp_path / "clean.mp4", ["-f", "lavfi", "-i", src, *enc])
    zones = {z["kind"]: z for z in textdetect.detect_file(burned, 0, 8, 640, 360)}
    lg, st = zones["logo"], zones["subtitles"]

    def covers(z, x0, y0, x1, y1):
        return z["x"] <= x0 and z["y"] <= y0 and z["x"] + z["w"] >= x1 and z["y"] + z["h"] >= y1
    assert covers(lg, 545 / 640, 22 / 360, 605 / 640, 42 / 360) and lg["w"] < 0.25
    assert covers(st, 125 / 640, 302 / 360, 495 / 640, 326 / 360) and st["y"] > 0.7
    assert textdetect.detect_file(clean, 0, 8, 640, 360) == []
