"""Intervenants : regroupement, report sur les traductions, et identification réelle (modèle WeSpeaker)."""
import os
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from studiovideo.ai import diarize
from studiovideo.media import ffmpeg
from studiovideo.transcribe.model import TSegment, Transcript

SPK_MODEL = os.environ.get("STUDIOVIDEO_SPK_MODEL", "")


def test_cluster_auto_and_forced():
    rng = np.random.default_rng(1)
    centers = rng.standard_normal((3, 64))
    truth = [0, 1, 0, 2, 1, 2, 0, 1]
    v = np.array([centers[t] + 0.25 * rng.standard_normal(64) for t in truth])
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    assert diarize.cluster(v) == truth                      # numéros dans l'ordre d'apparition
    assert len(set(diarize.cluster(v, n_speakers=2))) == 2
    assert diarize.cluster(v[:1]) == [0] and diarize.cluster(np.zeros((0, 64))) == []


def test_fbank_shape_and_cmn():
    x = np.sin(2 * np.pi * 440 * np.arange(16000) / 16000).astype(np.float32) * 0.3
    f = diarize.fbank(x)
    assert f.shape == (98, 80) and np.abs(f.mean(axis=0)).max() < 1e-3


def test_labels_and_translation_sync():
    tr = Transcript([TSegment(0, 1, "a"), TSegment(1.2, 2, "b"), TSegment(2.5, 3, "c")], "fr")
    diarize.apply_labels(tr, [0, 1, 0])
    assert [s.speaker for s in tr.segments] == ["Intervenant 1", "Intervenant 2", "Intervenant 1"]
    en = Transcript([TSegment(0, 1, "A"), TSegment(1.2, 2, "B"), TSegment(2.5, 3, "C")], "en")
    assert diarize.copy_speakers(tr, en) and en.segments[1].speaker == "Intervenant 2"
    assert not diarize.copy_speakers(tr, Transcript([TSegment(0, 1, "A")], "en"))


def test_missing_model_is_reported(tmp_path):
    with pytest.raises(diarize.DiarizationError, match="non installé"):
        diarize.Embedder(tmp_path)


LINES = [("en", "Hello and welcome to the show, today we talk about video editing."),
         ("en+f3", "Thanks for having me, I am really happy to be here with you."),
         ("en", "So tell us, how did you start making videos for social media?"),
         ("en+f3", "It started three years ago when I bought my first camera."),
         ("en+whisper", "Sorry to interrupt, but we have a question from the audience."),
         ("en+f3", "Of course, I would love to answer it right now."),
         ("en+whisper", "How do you choose the music for your short videos?"),
         ("en", "Great question, go ahead and tell us.")]
TRUTH = [0, 1, 0, 1, 2, 1, 2, 0]


@pytest.mark.skipif(not (SPK_MODEL and Path(SPK_MODEL).exists() and shutil.which("espeak-ng") and ffmpeg.available()),
                    reason="modèle d'empreintes vocales (STUDIOVIDEO_SPK_MODEL) ou espeak-ng absent")
def test_real_diarization_job(tmp_path):
    from studiovideo.core import build_context
    parts, segs, t = [], [], 0.0
    for i, (voice, text) in enumerate(LINES):
        w = tmp_path / f"{i}.wav"
        subprocess.run(["espeak-ng", "-v", voice, "-s", "160", "-w", str(w), text], check=True)
        x = np.frombuffer(subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-i", str(w), "-ar", "16000", "-ac", "1",
                                          "-f", "f32le", "-"], capture_output=True, check=True).stdout, "<f4")
        parts += [x, np.zeros(6400, np.float32)]
        segs.append(TSegment(round(t, 3), round(t + x.size / 16000, 3), text))
        t += x.size / 16000 + 0.4
    raw = tmp_path / "conv.f32"
    np.concatenate(parts).tofile(raw)
    video = tmp_path / "conv.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "f32le", "-ar", "16000", "-ac", "1", "-i", str(raw),
                    "-f", "lavfi", "-i", f"color=c=gray:s=320x240:d={t:.2f}", "-shortest", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)], check=True)
    app = build_context(tmp_path / "d")
    try:
        (app.paths.models / "speakers").mkdir(parents=True)
        shutil.copy(SPK_MODEL, diarize.model_path(app.paths.models))
        aid = app.importer().import_file(video).asset_id
        app.transcripts.store_original(aid, Transcript(segs, "en", "test"))
        app.transcripts.store_translation(aid, Transcript([TSegment(s.start, s.end, s.text.upper()) for s in segs],
                                                          "fr", "test"))
        jid = app.jobs.submit("diarize", "Intervenants", {"asset_id": aid, "n_speakers": None})
        end = time.time() + 120
        while app.jobs.get(jid).status in ("pending", "running") and time.time() < end:
            time.sleep(0.1)
        job = app.jobs.get(jid)
        assert job.status == "succeeded", job.error
        assert job.result["speakers"] == 3 and job.result["translations"] == ["fr"]
        got = [s.speaker for s in app.transcripts.get(aid).segments]
        assert got == [f"Intervenant {k + 1}" for k in TRUTH]
        assert [s.speaker for s in app.transcripts.translation(aid, "fr").segments] == got
        assert app.transcripts.original(aid).segments[0].speaker == ""        # l'original reste intact
    finally:
        app.close()
