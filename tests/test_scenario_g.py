"""Scénario G du cahier des charges (§26) : interlocuteur faible, musique forte, bruit de fond.

Attendu : l'analyse signale les problèmes ; le traitement améliore l'intelligibilité ; la musique est
atténuée pendant les paroles ; les niveaux des interlocuteurs sont équilibrés ; aucune saturation
introduite ; les réglages restent modifiables.
"""
import re
import subprocess

import pytest

from studiovideo.edit.audiofix import apply_plan, optimize
from studiovideo.edit.model import Timeline
from studiovideo.edit.render import Source, analyze_audio, export
from studiovideo.media import ffmpeg

pytestmark = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")
FF = None


def ff():
    return ffmpeg.ffmpeg_bin()


def run(cmd, on_line):
    p = subprocess.run(cmd, capture_output=True, text=True)
    for line in p.stderr.splitlines(keepends=True):
        on_line(line)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-1200:])


def band_level(path, a, d, freq):
    """Niveau RMS (dB) autour d'une fréquence, sur [a, a+d]."""
    p = subprocess.run([ff(), "-hide_banner", "-nostdin", "-ss", str(a), "-t", str(d), "-i", str(path), "-map", "0:a",
                        "-af", f"bandpass=f={freq}:width_type=q:w=8,astats=measure_perchannel=none", "-f", "null", "-"],
                       capture_output=True, text=True)
    return float(re.findall(r"RMS level dB:\s*(-?[\d.]+)", p.stderr)[-1])


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("g")
    voice = ("sine=f={f}:d=6,volume='if(lt(mod(t,1),0.6),1,0)':eval=frame,volume={v}dB")
    src = d / "interview.mp4"
    # interlocuteur A fort (300 Hz) puis B faible (500 Hz, 22 dB plus bas), bruit rose permanent
    subprocess.run([ff(), "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=12",
                    "-f", "lavfi", "-i", voice.format(f=300, v=-10),
                    "-f", "lavfi", "-i", voice.format(f=500, v=-32),
                    "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.006:d=12",
                    "-filter_complex", "[1][2]concat=n=2:v=0:a=1[v];[v][3]amix=inputs=2:normalize=0[a]",
                    "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", str(src)], check=True)
    music = d / "music.m4a"
    subprocess.run([ff(), "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=1000:d=20,volume=-4dB",
                    "-c:a", "aac", str(music)], check=True)
    return Source(str(src), 320, 180, 12.0, True), Source(str(music), None, None, 20.0, True)


def timeline():
    tl = Timeline()
    tl.set_aspect("9:16", 360)
    tl.format.fps = 25
    tl.export.preset = "ultrafast"
    tl.add_segment(1, 0.0, 6.0)      # interlocuteur A
    tl.add_segment(1, 6.0, 12.0)     # interlocuteur B (faible)
    tl.music.asset_id = 2
    tl.music.volume_db = 0.0         # musique forte
    tl.music.duck = False
    tl.music.fade_in = tl.music.fade_out = 0.0
    tl.audio.highpass = False
    return tl


def test_scenario_g(media, tmp_path):
    video, music = media
    sources = {1: video}

    # 1. l'analyse signale les problèmes
    tl = timeline()
    before = analyze_audio(tl, sources, music)
    p = optimize(tl, sources, music)
    joined = " ".join(p.report)
    assert "Extrait 2" in joined and "faible" in joined           # interlocuteur faible détecté
    assert "bruit" in joined.lower()                              # bruit de fond détecté
    assert "Musique" in joined                                    # musique trop forte prise en charge

    # export « avant » (réglages bruts) et « après » optimisation
    raw = export(timeline(), sources, music, tmp_path / "avant.mp4", run)
    tl2 = tl                         # même timeline : le plan vise ses extraits
    apply_plan(tl2, p)
    after = export(tl2, sources, music, tmp_path / "apres.mp4", run)

    # 2. niveaux des interlocuteurs équilibrés
    gap_before = band_level(raw["path"], 0.5, 5, 300) - band_level(raw["path"], 6.5, 5, 500)
    gap_after = band_level(after["path"], 0.5, 5, 300) - band_level(after["path"], 6.5, 5, 500)
    assert gap_before > 10 and abs(gap_after) < 4, (gap_before, gap_after)

    # 3. musique atténuée pendant les paroles : rapport voix / musique amélioré
    vm_before = band_level(raw["path"], 6.5, 5, 500) - band_level(raw["path"], 6.5, 5, 1000)
    vm_after = band_level(after["path"], 6.5, 5, 500) - band_level(after["path"], 6.5, 5, 1000)
    assert vm_after - vm_before > 10, (vm_before, vm_after)

    # 4. aucune saturation introduite par le logiciel
    assert after["true_peak"] <= -0.5 and abs(after["lufs"] - tl2.audio.target_lufs) < 1.5

    # 5. les réglages restent visibles et modifiables
    assert tl2.music.duck and tl2.music.volume_db <= -16 and tl2.audio.denoise
    assert tl2.segments[1].volume_db > 10
    tl2.audio.denoise = False                    # l'utilisateur peut revenir sur un traitement
    assert before["lufs"] is not None


def test_continuous_background_is_not_denoised(tmp_path):
    from studiovideo.edit.audiofix import SegmentMeasure, plan
    tl = Timeline()
    tl.add_segment(1, 0, 5)
    p = plan(tl, [SegmentMeasure(tl.segments[0].id, 0, -16.0, 3.0, -2.0, -20.0, True)], False)
    assert not p.audio.get("denoise") and any("Fond sonore continu" in w for w in p.warnings)
    p2 = plan(tl, [SegmentMeasure(tl.segments[0].id, 0, -20.0, 3.0, -2.0, -48.0, True)], False)
    assert p2.audio["denoise"]
