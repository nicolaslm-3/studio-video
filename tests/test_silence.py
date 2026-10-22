"""Suppression des silences : détection sur le son, sur la transcription, découpage de la timeline."""
import subprocess
import time

import pytest

from studiovideo.edit import silence
from studiovideo.edit.model import Timeline
from studiovideo.media import ffmpeg
from studiovideo.transcribe.model import TSegment, Transcript, Word

pytestmark = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")


def test_keep_ranges_and_apply():
    tl = Timeline()
    seg = tl.add_segment(1, 0, 10)
    seg.reframe = [[0, 0.4]]
    other = tl.add_segment(2, 0, 3)
    res = silence.apply(tl, {seg.id: [(2.0, 3.5), (6.0, 8.0)]}, pad=0.15)
    assert [(s.src_in, s.src_out) for s in tl.segments[:3]] == [(0, 2.15), (3.35, 6.15), (7.85, 10)]
    assert tl.segments[0].id == seg.id and tl.segments[3] is other
    assert tl.segments[1].reframe == [[0, 0.4]]
    assert res["removed"] == pytest.approx(10 - (2.15 + 2.8 + 2.15), abs=0.01) and res["cuts"] == 2


def test_from_transcript():
    tr = Transcript([TSegment(0, 4, "a b", [Word(0.5, 1.0, " a"), Word(3.0, 3.5, " b")]),
                     TSegment(6, 7, "c", [Word(6.2, 6.8, " c")])], "fr")
    assert silence.from_transcript(tr, 0, 8, 0.6) == [(1.0, 3.0), (3.5, 6.2), (6.8, 8)]
    assert silence.from_transcript(tr, 2, 4, 0.6) == [(2, 3.0)]


def test_job_on_real_audio(tmp_path):
    from studiovideo.core import build_context
    v = tmp_path / "talk.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:d=10:r=25",
                    "-f", "lavfi", "-i", "sine=f=300:d=10", "-af",
                    "volume='if(between(t,2,3.5)+between(t,6,8),0,1)':eval=frame", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(v)], check=True)
    app = build_context(tmp_path / "d")
    try:
        aid = app.importer().import_file(v).asset_id
        tl = Timeline()
        seg = tl.add_segment(aid, 0, 10)
        jid = app.jobs.submit("find_silences", "Silences", {"segments": [{"id": seg.id, "asset_id": aid, "src_in": 0,
                                                                            "src_out": 10}], "min_len": 0.6})
        end = time.time() + 30
        while app.jobs.get(jid).status in ("pending", "running") and time.time() < end:
            time.sleep(0.05)
        j = app.jobs.get(jid)
        assert j.status == "succeeded", j.error
        assert j.result["used"]["son"] == 1
        sil = j.result["silences"][seg.id]
        assert len(sil) == 2 and abs(sil[0][0] - 2.0) < 0.1 and abs(sil[1][1] - 8.0) < 0.1
    finally:
        app.close()
