"""Transcription (modèle, exports, import) et sous-titres (génération, rendu, incrustation)."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from studiovideo.edit.model import Timeline
from studiovideo.edit.render import Source, export, preview_frame
from studiovideo.edit.subtitles import (SubtitleRules, SubtitleTrack, build_cues, generate, states, timeline_words,
                                        to_srt, wrap_lines)
from studiovideo.media import ffmpeg
from studiovideo.transcribe.model import TSegment, Transcript, Word, export_text, import_subtitles


def make_tr():
    words = "Bonjour à tous, aujourd'hui on parle de montage vidéo. C'est parti pour la démonstration".split()
    ws, t = [], 0.5
    for w in words:
        ws.append(Word(t, t + 0.3, " " + w))
        t += 0.35
    return Transcript([TSegment(0.5, ws[8].end, " ".join(words[:9]), ws[:9], "Alice"),
                       TSegment(ws[9].start + 1.0, ws[-1].end + 1.0, " ".join(words[9:]),
                                [Word(w.start + 1.0, w.end + 1.0, w.text) for w in ws[9:]], "Bob")], "fr", "test")


def test_transcript_roundtrip_edit_search():
    tr = make_tr()
    back = Transcript.from_json(tr.to_json())
    assert back.text() == tr.text() and back.segments[0].speaker == "Alice"
    assert tr.search("MONTAGE") == [0] and tr.search("demonstration") == [1]
    tr.edit_segment(0, "Salut à tous, aujourd'hui on parle de montage vidéo.")
    assert tr.segments[0].words[0].text.strip() == "Salut"
    tr.edit_segment(1, "C'est parti")
    assert len(tr.segments[1].words) == 2
    assert tr.segments[1].words[0].start == tr.segments[1].start
    tr.rename_speaker("Bob", "Robert")
    assert tr.speakers() == ["Alice", "Robert"]


@pytest.mark.parametrize("fmt", ["txt", "srt", "vtt", "json", "ass"])
def test_exports(fmt):
    out = export_text(make_tr(), fmt)
    assert "montage" in out
    if fmt == "srt":
        assert "00:00:00,500 --> " in out and "Alice : " in out
    if fmt == "vtt":
        assert out.startswith("WEBVTT") and "<v Bob>" in out
    if fmt == "json":
        assert json.loads(out)["language"] == "fr"
    if fmt == "ass":
        assert "Dialogue: 0,0:00:00.50" in out


def test_import_srt_and_vtt():
    srt = export_text(make_tr(), "srt")
    tr = import_subtitles(srt)
    assert len(tr.segments) == 2 and tr.segments[0].start == 0.5 and tr.segments[1].words
    vtt = export_text(make_tr(), "vtt")
    tv = import_subtitles(vtt)
    assert tv.segments[1].speaker == "Bob"
    with pytest.raises(ValueError):
        import_subtitles("pas des sous-titres")


def test_wrap_lines_balanced():
    lines = wrap_lines("un deux trois quatre cinq six sept".split(), 20, 2)
    assert len(lines) == 2 and abs(len(" ".join(lines[0])) - len(" ".join(lines[1]))) < 10
    assert wrap_lines(["court"], 20, 2) == [["court"]]


def test_timeline_mapping_across_cuts():
    tr = make_tr()
    tl = Timeline()
    tl.add_segment(7, 0.0, 2.0)     # « Bonjour à tous, aujourd'hui on »
    tl.add_segment(7, 4.0, 6.5)     # 2e phrase décalée de 1 s
    words = timeline_words(tl, {7: tr})
    assert words[0][2] == "Bonjour" and words[0][0] == pytest.approx(0.5)
    second = [w for w in words if w[3] == 1]
    assert second and second[0][0] >= 2.0          # remappé après la coupe
    assert all(0 <= a < b <= tl.duration + 1e-6 for a, b, _, _ in words)


def test_cue_rules():
    tr = make_tr()
    tl = Timeline()
    tl.add_segment(7, 0, 8)
    rules = SubtitleRules(max_words=4, max_chars=18, max_lines=2, pause_split=0.6)
    cues = build_cues(timeline_words(tl, {7: tr}), rules, tl.duration)
    assert all(len(c.text.split()) <= 4 for c in cues)
    assert any(c.text.endswith("vidéo.") for c in cues)       # coupure en fin de phrase
    assert all(cues[i].end <= cues[i + 1].start + 1e-6 for i in range(len(cues) - 1))
    assert all(c.end - c.start >= 0.2 for c in cues)
    srt = to_srt(cues)
    assert srt.count("-->") == len(cues)


@pytest.mark.parametrize("anim", ["none", "highlight", "word", "pop", "fade"])
def test_states_cover_timeline(anim):
    tl = Timeline()
    tl.add_segment(7, 0, 8)
    tl.subtitles = SubtitleTrack(animation=anim)
    tl.subtitles.cues = generate(tl, {7: make_tr()})
    seq = states(tl.subtitles, tl.duration, 30)
    assert seq[0][0] == 0 and seq[-1][1] == pytest.approx(tl.duration)
    assert all(abs(seq[i][1] - seq[i + 1][0]) < 1e-9 for i in range(len(seq) - 1))
    if anim == "highlight":
        assert any(s.highlight == 2 for _, _, s in seq)


def test_concept_defaults_keep_subtitle_style():
    from studiovideo.edit.model import apply_concept_defaults, concept_defaults_from
    a = Timeline()
    a.subtitles.enabled = True
    a.subtitles.animation = "pop"
    a.subtitles.style.highlight_color = "#00FF00"
    a.subtitles.cues = generate(a, {})
    b = apply_concept_defaults(Timeline(), concept_defaults_from(a))
    assert b.subtitles.animation == "pop" and b.subtitles.style.highlight_color == "#00FF00"
    assert b.subtitles.cues == []


needs_ffmpeg = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    if not ffmpeg.available():
        pytest.skip("FFmpeg absent")
    p = tmp_path_factory.mktemp("v") / "v.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=25:duration=8", "-f", "lavfi", "-i", "sine=f=300:d=8",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p)], check=True)
    return p


def run_cmd(cmd, on_line):
    p = subprocess.run(cmd, capture_output=True, text=True)
    for line in p.stderr.splitlines(keepends=True):
        on_line(line)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-1500:])


@needs_ffmpeg
@pytest.mark.parametrize("anim", ["highlight", "pop"])
def test_export_with_burned_subtitles(video, tmp_path, anim):
    tl = Timeline()
    tl.set_aspect("9:16", 360)
    tl.format.fps = 25
    tl.export.preset = "ultrafast"
    tl.add_segment(1, 0, 3)
    tl.add_segment(1, 4, 7)
    tl.subtitles = SubtitleTrack(animation=anim, sidecar_srt=True)
    tl.subtitles.cues = generate(tl, {1: make_tr()})
    assert tl.subtitles.cues
    src = {1: Source(str(video), 640, 360, 8.0, True)}
    rep = export(tl, src, None, tmp_path / "s.mp4", run_cmd)
    assert (rep["width"], rep["height"]) == (360, 640) and abs(rep["duration"] - 6.0) < 0.2
    assert Path(rep["srt"]).read_text(encoding="utf-8").count("-->") == len(tl.subtitles.cues)
    # l'image contient bien des sous-titres : comparaison avec un export sans sous-titres
    frame_with = tmp_path / "a.png"
    cue = tl.subtitles.cues[0]
    t = (cue.start + cue.end) / 2
    subprocess.run([ffmpeg.ffmpeg_bin(), "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", rep["path"],
                    "-frames:v", "1", str(frame_with)], check=True)
    tl.subtitles.enabled = False
    rep2 = export(tl, src, None, tmp_path / "n.mp4", run_cmd)
    frame_without = tmp_path / "b.png"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", rep2["path"],
                    "-frames:v", "1", str(frame_without)], check=True)
    from PySide6.QtGui import QImage
    a, b = QImage(str(frame_with)), QImage(str(frame_without))
    y0 = int(640 * 0.70)
    diff = sum(1 for x in range(0, 360, 4) if a.pixelColor(x, y0) != b.pixelColor(x, y0))
    assert diff > 3, "aucun sous-titre visible dans la zone attendue"


@needs_ffmpeg
def test_preview_includes_subtitles(video, tmp_path):
    tl = Timeline()
    tl.add_segment(1, 0, 6)
    tl.subtitles = SubtitleTrack(animation="highlight")
    tl.subtitles.cues = generate(tl, {1: make_tr()})
    p = preview_frame(tl, {1: Source(str(video), 640, 360, 8.0, True)}, 1.0, tmp_path / "p.png", 480)
    assert p.exists()


# -- transcription réelle (exécutée dans l'intégration continue, où le modèle est téléchargeable) --------
@pytest.mark.skipif(not os.environ.get("STUDIOVIDEO_TEST_WHISPER"), reason="test Whisper réel désactivé")
def test_real_whisper_transcription(tmp_path):
    from studiovideo.transcribe import engines
    root = Path(os.environ.get("STUDIOVIDEO_TEST_MODELS", tmp_path))
    if not engines.is_installed(root, "tiny"):
        engines.download_model(root, "tiny", lambda f, m: None, lambda: False)
    speech = Path(os.environ["STUDIOVIDEO_TEST_SPEECH"])
    eng = engines.FasterWhisperEngine(root, "tiny")
    tr = engines.transcribe_file(eng, speech, "en", lambda f, m: None, lambda: False)
    txt = tr.text().lower()
    assert "video" in txt or "studio" in txt, txt
    words = tr.all_words()
    assert words and all(w.end >= w.start for w in words)
