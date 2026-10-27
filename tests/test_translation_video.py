"""Traduction en un clic (espace Traduction) : chaque langue produit une vraie vidéo vérifiée."""
import subprocess
import time

import pytest

from studiovideo.core import build_context
from studiovideo.edit import translation as trn
from studiovideo.edit.model import Timeline
from studiovideo.media import ffmpeg
from studiovideo.transcribe.model import Transcript, TSegment, Word

needs_ffmpeg = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")


def wait(app, jid, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        j = app.jobs.get(jid)
        if j.status in ("succeeded", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise TimeoutError(app.jobs.get(jid))


def test_presets_and_timeline():
    tr = Transcript([TSegment(0.0, 2.0, "Hello everyone", [Word(0.0, 0.9, "Hello"), Word(1.0, 2.0, "everyone")])],
                    "en", "t")
    tpl = Timeline()
    tl = trn.translated_timeline(tpl, 7, 4.0, "en", tr, "yellow", aspect=trn.source_aspect(1080, 1920),
                                 top_text="Recette facile", masks=[{"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.1,
                                                                    "mode": "delogo"}], dubbed=True,
                                 keep_music=False)
    assert tl.format.aspect == "9:16" and [s.asset_id for s in tl.segments] == [7]
    assert tl.subtitles.style.color == "#FFD54A" and tl.subtitles.cues and tl.subtitles.language == "en"
    assert tl.voice.mode == "dub" and tl.voice.original_level_db == -60.0          # voix d'origine retirée
    assert tl.texts[0].text == "Recette facile" and tl.texts[0].position == "top" and tl.video.masks
    keep = trn.translated_timeline(tpl, 7, 4.0, "en", tr, "band", dubbed=True, keep_music=True)
    assert keep.voice.background == "ambience" and keep.subtitles.style.box
    assert trn.source_aspect(1920, 1080) == "16:9" and trn.source_aspect(None, None) == "9:16"


@needs_ffmpeg
def test_translate_video_job_exports_each_language(tmp_path, monkeypatch):
    from studiovideo.ai import translate as tr_mod
    app = build_context(tmp_path / "data")
    try:
        vid = tmp_path / "court.mp4"
        subprocess.run([ffmpeg.ffmpeg_bin(), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=360x640:rate=25:duration=3", "-f", "lavfi", "-i",
                        "sine=frequency=300:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", str(vid)], check=True)
        pid = app.projects.quick_create(app.concepts, "translation", "Recette", "9:16")
        aid = app.importer().import_file(vid, project_id=pid).asset_id
        app.transcripts.store_original(aid, Transcript([TSegment(0.2, 2.5, "Bonjour tout le monde", [])], "fr",
                                                       "test"))

        class Fake:
            name = "faux"

            def translate(self, texts, src, dst):
                return [f"[{dst}] {t}" for t in texts]
        monkeypatch.setattr(tr_mod, "make_translator", lambda cfg, secret: Fake())
        out = tmp_path / "sorties"
        tpl = Timeline()
        tpl.set_aspect("9:16", short_side=360)          # format du projet (le même dans tous les onglets)
        tpl.export.preset = "ultrafast"
        j = wait(app, app.jobs.submit("translate_video", "Traduction", {
            "asset_id": aid, "project_id": pid, "languages": ["en", "fr", "de"], "engine": "llm", "voice": None,
            "top_text": "Recette", "caption": "Ma légende", "preset": "yellow", "keep_music": True,
            "erase_texts": False, "timeline": tpl.to_dict(), "folder": str(out)}))
        assert j.status == "succeeded", j.error
        r = j.result
        assert r["languages"] == ["en", "de"] and r["skipped"] == ["fr"]        # le français est la langue d'origine
        assert len(r["files"]) == 2 and not r["errors"]
        for lang, f in zip(("en", "de"), r["files"]):
            info = ffmpeg.probe(f)
            assert (info.width, info.height) == (360, 640) and info.audio_tracks == 1    # format du projet
            assert (out / f"court_{lang}.txt").read_text(encoding="utf-8") == f"[{lang}] Ma légende"
            assert app.transcripts.translation(aid, lang).segments[0].text == f"[{lang}] Bonjour tout le monde"
        assert app.montage.exports(pid)                                          # exports enregistrés au projet
    finally:
        app.close()
