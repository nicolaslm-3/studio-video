"""Production en série : planification et exports réels."""
import subprocess

import pytest

from studiovideo.edit.batch import BatchOptions, plan_batch
from studiovideo.edit.model import Timeline
from studiovideo.edit.render import Source, export
from studiovideo.media import ffmpeg
from studiovideo.transcribe.model import TSegment, Transcript, Word


def tr():
    ws = [Word(1 + i * 0.4, 1.35 + i * 0.4, " " + w) for i, w in enumerate("voici un extrait très intéressant".split())]
    return Transcript([TSegment(1.0, ws[-1].end, "voici un extrait très intéressant", ws)])


def template():
    t = Timeline()
    t.video.mirror = True
    t.audio.target_lufs = -16
    t.add_text("texte propre au montage", 0, 2)
    t.subtitles.enabled = True
    t.subtitles.animation = "pop"
    t.add_segment(9, 0, 1)
    return t


def test_plan_batch_clips_by_formats():
    clips = [{"asset_id": 1, "start": 0.5, "end": 4.0, "name": "A", "hook": "Le meilleur moment"},
             {"asset_id": 2, "start": 2.0, "end": 6.0, "name": "B", "hook": ""}]
    out = plan_batch(template(), clips, BatchOptions(aspects=["9:16", "1:1"]), {1: tr()}, {"color": "#FFD54A"})
    assert len(out) == 4
    by = {(c["name"], a): t for c, a, t in out}
    a916 = by[("A", "9:16")]
    assert (a916.format.width, a916.format.height) == (1080, 1920) and by[("A", "1:1")].format.height == 1080
    assert a916.video.mirror and a916.audio.target_lufs == -16          # réglages du modèle repris
    assert [x.text for x in a916.texts] == ["Le meilleur moment"] and a916.texts[0].color == "#FFD54A"
    assert a916.subtitles.enabled and a916.subtitles.cues and a916.subtitles.animation == "pop"
    b = by[("B", "9:16")]
    assert b.texts == [] and not b.subtitles.enabled                    # pas d'accroche, pas de transcription
    assert len(a916.segments) == 1 and a916.duration == pytest.approx(3.5)


@pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")
def test_batch_real_exports(tmp_path):
    v = tmp_path / "v.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=25:duration=6", "-f", "lavfi", "-i", "sine=f=300:d=6",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(v)],
                   check=True)
    t = template()
    t.export.preset = "ultrafast"
    clips = [{"asset_id": 1, "start": 0.5, "end": 4.0, "name": "A", "hook": "Accroche"}]
    out = plan_batch(t, clips, BatchOptions(aspects=["9:16", "16:9"], short_side=240), {1: tr()})

    def run(cmd, on_line):
        p = subprocess.run(cmd, capture_output=True, text=True)
        for line in p.stderr.splitlines(keepends=True):
            on_line(line)
        if p.returncode != 0:
            raise RuntimeError(p.stderr[-800:])
    sizes = set()
    for c, asp, tl in out:
        rep = export(tl, {1: Source(str(v), 320, 180, 6.0, True)}, None, tmp_path / f"{asp.replace(':', 'x')}.mp4", run)
        sizes.add((rep["width"], rep["height"]))
    assert sizes == {(240, 426), (426, 240)}


def test_batch_language_variant_uses_dub_when_available():
    from studiovideo.edit.batch import BatchOptions, plan_batch
    from studiovideo.edit.model import Timeline
    from studiovideo.transcribe.model import TSegment, Transcript
    tr = Transcript([TSegment(0, 2, "Hi", [], "")], "en", "t")
    clips = [{"asset_id": 1, "start": 0, "end": 3, "name": "a"}, {"asset_id": 2, "start": 0, "end": 3, "name": "b"}]
    opts = BatchOptions(aspects=["9:16"], languages=["", "en"], dubbed={(1, "en")})
    plan = plan_batch(Timeline(), clips, opts, {(1, "en"): tr, (2, "en"): tr})
    voices = {(c["name"], f): (tl.voice.mode, tl.voice.language) for c, f, tl in plan}
    assert voices[("a", "9:16 · en")] == ("dub", "en")
    assert voices[("b", "9:16 · en")] == ("original", "")
    assert voices[("a", "9:16")] == ("original", "")
