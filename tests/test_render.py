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
