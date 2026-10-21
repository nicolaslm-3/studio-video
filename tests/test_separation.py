"""Séparation voix / musique : STFT, rendu avec pistes séparées, et séparation réelle (modèle MDX-Net)."""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from studiovideo.edit import separate
from studiovideo.edit.model import Timeline
from studiovideo.edit.render import export
from studiovideo.media import ffmpeg

pytestmark = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")
SEP_MODEL = os.environ.get("STUDIOVIDEO_SEP_MODEL", "")


def _make(path, args):
    subprocess.run([ffmpeg.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *args, str(path)], check=True)
    return path


def _dominant(path, start, end):
    p = subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-ss", str(start), "-t", str(end - start), "-i", str(path),
                        "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True, check=True)
    x = np.frombuffer(p.stdout, "<i2").astype(float)
    return float(np.fft.rfftfreq(x.size, 1 / 16000)[int(np.argmax(np.abs(np.fft.rfft(x * np.hanning(x.size)))))])


def test_stft_roundtrip():
    x = np.random.default_rng(0).standard_normal((2, 40000)).astype(np.float32)
    w = separate._window(7680)
    y = separate.istft(separate.stft(x, 7680, 1024, w), 7680, 1024, w, x.shape[1])
    assert np.abs(x - y).max() < 1e-4


def test_missing_model_is_reported(tmp_path):
    with pytest.raises(separate.SeparationError, match="non installé"):
        separate.MDXSeparator(tmp_path)


def test_render_with_stems_and_ambience_under_dub(tmp_path):
    """Pistes séparées simulées : 300 Hz = mélange d'origine, 500 Hz = voix seule, 700 Hz = ambiance."""
    from studiovideo.core import build_context
    video = _make(tmp_path / "v.mp4", ["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=4",
                                       "-f", "lavfi", "-i", "sine=frequency=300:duration=4",
                                       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"])
    app = build_context(tmp_path / "d")
    try:
        aid = app.importer().import_file(video).asset_id
        tl = Timeline()
        tl.add_segment(aid, 0, 4)
        tl.audio.normalize = tl.audio.highpass = False
        tl.voice.isolate = True
        with pytest.raises(ValueError, match="Séparez d'abord"):
            app.montage.sources_for(tl)
        d = app.stems.directory(aid)
        d.mkdir(parents=True)
        _make(d / "voice.wav", ["-f", "lavfi", "-i", "sine=frequency=500:duration=4", "-ar", "44100", "-ac", "2"])
        _make(d / "music.wav", ["-f", "lavfi", "-i", "sine=frequency=700:duration=4", "-ar", "44100", "-ac", "2"])
        app.stems.mark_done(aid, "test")
        assert app.stems.has(aid)

        def run(cmd, on_line):
            p = subprocess.run(cmd, capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-1500:]
            return 0
        src, mus = app.montage.sources_for(tl)
        export(tl, src, mus, tmp_path / "iso.mp4", run)
        assert abs(_dominant(tmp_path / "iso.mp4", 1, 3) - 500) < 15        # voix isolée
        # doublage par-dessus l'ambiance séparée (pas de voix d'origine)
        dub_dir = tmp_path / "dub"
        dub_dir.mkdir()
        _make(dub_dir / "dub.wav", ["-f", "lavfi", "-i", "sine=frequency=1000:duration=4", "-ar", "48000", "-ac", "1"])
        from studiovideo.edit.dubbing import DubManifest
        app.dubs.store(DubManifest(aid, "en", "test", {}, 4.0, [], "dub.wav"), dub_dir)
        tl.voice.isolate = False
        tl.voice.mode, tl.voice.language, tl.voice.background = "dub", "en", "ambience"
        tl.voice.ambience_db = -12
        src, mus = app.montage.sources_for(tl)
        export(tl, src, mus, tmp_path / "dub_amb.mp4", run)
        p = subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-ss", "1", "-t", "2", "-i", str(tmp_path / "dub_amb.mp4"),
                            "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True, check=True)
        x = np.frombuffer(p.stdout, "<i2").astype(float)
        spec = np.abs(np.fft.rfft(x * np.hanning(x.size)))
        f = np.fft.rfftfreq(x.size, 1 / 16000)
        band = lambda hz: spec[(f > hz - 10) & (f < hz + 10)].max()   # noqa: E731
        assert band(1000) > band(700) > 5 * band(300)                       # doublage > ambiance, pas d'original
    finally:
        app.close()


@pytest.mark.skipif(not (SEP_MODEL and Path(SEP_MODEL).exists()), reason="modèle de séparation absent "
                    "(STUDIOVIDEO_SEP_MODEL)")
def test_real_separation_improves_voice(tmp_path):
    """Voix synthétique + accord et bruit rose : la voix extraite doit être bien plus proche de la voix seule."""
    models = tmp_path / "models"
    (models / "separation").mkdir(parents=True)
    shutil.copy(SEP_MODEL, models / "separation" / separate.MODELS[0].file)
    speech = os.environ.get("STUDIOVIDEO_SEP_SPEECH", "")
    if not speech:
        pytest.skip("voix de test absente (STUDIOVIDEO_SEP_SPEECH)")
    clean = _make(tmp_path / "clean.wav", ["-i", speech, "-af", "aresample=44100,pan=stereo|c0=c0|c1=c0,"
                                           "apad=whole_dur=10", "-t", "10"])
    music = _make(tmp_path / "music.wav", ["-f", "lavfi", "-i", "sine=frequency=220:duration=10",
                                           "-f", "lavfi", "-i", "sine=frequency=330:duration=10",
                                           "-f", "lavfi", "-i", "anoisesrc=d=10:c=pink:a=0.03",
                                           "-filter_complex", "[0][1][2]amix=inputs=3,volume=1.5,apulsator=hz=2",
                                           "-ar", "44100", "-ac", "2"])
    mix = _make(tmp_path / "mix.wav", ["-i", str(clean), "-i", str(music), "-filter_complex",
                                       "amix=inputs=2:normalize=0", "-t", "10"])
    t = time.time()
    res = separate.separate_file(mix, tmp_path / "out", models)
    rd = lambda p: separate._read_mix(Path(p))   # noqa: E731
    c, m, v, r = rd(clean), rd(mix), rd(res["voice"]), rd(res["music"])
    n = min(c.shape[1], m.shape[1], v.shape[1])

    def sdr(ref, est):
        return float(10 * np.log10((ref ** 2).sum() / (((ref - est) ** 2).sum() + 1e-9)))
    before, after = sdr(c[:, :n], m[:, :n]), sdr(c[:, :n], v[:, :n])
    print(json.dumps({"sdr_before": before, "sdr_after": after, "seconds": time.time() - t}))
    assert after > before + 8
    mu = rd(music)
    assert sdr(mu[:, :n], r[:, :n]) > sdr(mu[:, :n], m[:, :n]) + 15
