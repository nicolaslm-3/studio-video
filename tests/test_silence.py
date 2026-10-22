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


def test_punch_in_alternates_within_a_take():
    from studiovideo.edit.model import Timeline
    from studiovideo.edit.silence import punch_in
    tl = Timeline()
    for a, b in ((0, 2), (3, 5), (6, 8)):
        tl.add_segment(1, a, b)
    tl.add_segment(2, 0, 2)                  # autre média : nouvelle prise
    tl.add_segment(2, 4, 6)
    tl.add_segment(2, 1, 3)                  # retour en arrière : nouvelle prise
    tl.segments[2].zoom = 1.3                # réglage manuel conservé
    assert punch_in(tl) == 2
    assert [s.zoom for s in tl.segments] == [1.0, 1.12, 1.3, 1.0, 1.12, 1.0]
    left, right = tl.split_at(tl.segment_start(1) + 1.0)
    assert right.zoom == 1.12                # une coupe garde le zoom


def test_segment_zoom_rendered(tmp_path):
    """Le zoom d'un extrait agrandit réellement l'image exportée (mire : bords différents)."""
    import subprocess

    import numpy as np

    from studiovideo.edit import render
    from studiovideo.edit.model import Timeline
    from studiovideo.media import ffmpeg
    if not ffmpeg.available():
        pytest.skip("FFmpeg absent")
    src = tmp_path / "s.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x320:d=1:r=10",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    tl = Timeline()
    tl.set_aspect("1:1", 160)
    s1 = tl.add_segment(1, 0, 1)
    source = render.Source(path=str(src), width=320, height=320, duration=1.0, has_audio=False)
    frames = []
    for z in (1.0, 2.0):
        s1.zoom = z
        chain = render.video_chain(tl, source, "0", s1)
        raw = subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-i", str(src), "-vf", chain, "-frames:v", "1",
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
        frames.append(np.frombuffer(raw, np.uint8).reshape(160, 160).astype(float))
    full = frames[0]
    # le centre du plan large, agrandi ×2, doit ressembler au plan zoomé
    centre = np.kron(full[40:120, 40:120], np.ones((2, 2)))
    assert np.abs(centre - frames[1]).mean() < np.abs(full - frames[1]).mean() * 0.6
