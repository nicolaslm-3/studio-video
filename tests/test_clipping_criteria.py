"""Clipping : critères choisis pendant la transcription, personne qui parle, légende du clip, erreurs lisibles."""
import subprocess
import time

import pytest

from studiovideo.ai import highlights as hl
from studiovideo.core import build_context
from studiovideo.edit import reframe as r
from studiovideo.media import downloader, ffmpeg
from studiovideo.transcribe.model import Transcript, TSegment


def wait(app, jid, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        j = app.jobs.get(jid)
        if j.status in ("succeeded", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise TimeoutError(app.jobs.get(jid))


def test_criteria_request_defaults_to_politics():
    req = hl.criteria_request({}, 3600)
    assert req.categories == ["politique"] and req.count == 8                   # rien de coché : politique
    assert (req.min_duration, req.max_duration) == (30.0, 60.0)
    req = hl.criteria_request({"categories": ["humour", "emotion"], "duration": "court", "count": 12}, 3600)
    assert req.categories == ["humour", "emotion"] and req.count == 12 and req.max_duration == 30.0
    short = hl.criteria_request({"count": 20}, 40)                              # vidéo courte : extraits courts
    assert short.max_duration < 20 and short.count <= 3


def test_auto_clips_uses_project_criteria(tmp_path, monkeypatch):
    if not ffmpeg.available():
        pytest.skip("FFmpeg absent")
    vid = tmp_path / "debat.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x120:d=3:r=25",
                    "-f", "lavfi", "-i", "sine=f=300:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(vid)], check=True)
    seen = []
    monkeypatch.setattr(hl.LocalEngine, "propose", lambda self, tr, req, progress=None: seen.append(req) or [])
    app = build_context(tmp_path / "d")
    try:
        pid = app.projects.quick_create(app.concepts, "smart_clipping", "Débat")
        aid = app.importer().import_file(vid, project_id=pid).asset_id
        app.transcripts.store_original(aid, Transcript([TSegment(0, 2, "Bonjour", [])], "fr", "t"))
        data = app.projects.load_data(pid)
        data["clip_criteria"] = {"categories": ["debat"], "duration": "court", "count": 5}
        app.projects.save_data(pid, data)
        j = wait(app, app.jobs.submit("auto_clips", "Extraits", {"asset_id": aid, "project_id": pid}))
        assert j.status == "succeeded", j.error
        assert seen and seen[-1].categories == ["debat"]                        # choisis pendant la transcription
        data["clip_criteria"] = {"categories": []}
        app.projects.save_data(pid, data)
        assert wait(app, app.jobs.submit("auto_clips", "Extraits", {"asset_id": aid, "project_id": pid})).status \
            == "succeeded"
        assert seen[-1].categories == ["politique"]                             # rien de coché : politique
    finally:
        app.close()


def _face(c, w=0.1):
    return (c - w / 2, 0.3, c + w / 2, 0.5, 0.99)


def test_speaker_centers_follow_who_talks():
    n = 64                                                       # 8 s à 8 images/s, deux visages fixes
    t = [i / 8 for i in range(n)]
    dets = [[_face(0.25), _face(0.75)] for _ in range(n)]
    # gauche parle jusqu'à 4 s, puis droite ; des petits mouvements de l'autre ne font pas sauter le cadre
    acts = [[12.0, 2.0] if i < 32 else [2.0, 12.0] for i in range(n)]
    acts[10] = [12.0, 30.0]
    c = r.speaker_centers(t, dets, acts)
    assert c[5] == 0.25 and c[12] == 0.25                        # une syllabe isolée ne change rien
    assert c[30] == 0.25 and c[-1] == 0.75                       # bascule sur celui qui parle (maintien)
    switch = next(i for i, x in enumerate(c) if x == 0.75)
    assert 32 <= switch <= 32 + 12
    keys = r.track(t, dets, centers=c)
    assert abs(r.cx_at(keys, 1) - 0.25) < 0.02 and abs(r.cx_at(keys, 7.5) - 0.75) < 0.02
    # changement de plan : le visage suivi disparaît, le cadre se replace aussitôt sur celui qui parle
    dets2 = [[_face(0.25), _face(0.75)] for _ in range(24)] + [[_face(0.45), _face(0.9)] for _ in range(24)]
    acts2 = [[10.0, 1.0] for _ in range(24)] + [[1.0, 10.0] for _ in range(24)]
    c2 = r.speaker_centers([i / 8 for i in range(48)], dets2, acts2)
    assert c2[20] == 0.25 and c2[27] == 0.9
    assert r.speaker_centers([0, 0.125], [[], []], [[], []]) == [None, None]


def test_mouth_activity_ignores_camera_motion():
    import numpy as np
    rng = np.random.default_rng(1)
    img = rng.uniform(0, 255, (480, 640)).astype(np.float32)
    box = (0.4, 0.3, 0.6, 0.7, 1.0)
    moved = np.roll(img, 2, axis=1)                              # la caméra glisse de 2 pixels
    assert r.mouth_activity(img, moved, box) < 1.0
    talk = img.copy()
    talk[int(0.6 * 480):int(0.66 * 480), int(0.46 * 640):int(0.54 * 640)] = 0       # la bouche s'ouvre
    assert r.mouth_activity(img, talk, box) > 10.0
    assert r.mouth_activity(None, img, box) == 0.0


def test_llm_errors_are_readable():
    body = b'{"error": {"message": "The model `oui` does not exist or you do not have access to it.", ' \
           b'"type": "invalid_request_error", "param": null}}'
    msg = hl.http_error_text(404, body, "oui")
    assert "« oui » n'existe pas" in msg and "Réglages" in msg and "{" not in msg
    assert "Clé API refusée" in hl.http_error_text(401, b'{"error": {"message": "bad key"}}')
    assert hl.http_error_text(500, b"oops") == "Le service a répondu 500 : oops"


def test_downloader_enables_installed_js_runtime(monkeypatch):
    monkeypatch.setattr(downloader.shutil, "which", lambda n: r"C:\nodejs\node.exe" if n == "node" else None)
    assert downloader.js_runtimes() == {"node": {"path": r"C:\nodejs\node.exe"}}
    monkeypatch.setattr(downloader.shutil, "which", lambda n: None)
    assert downloader.js_runtimes() == {"deno": {}}                             # valeur par défaut de yt-dlp


def test_export_writes_caption_next_to_clip(tmp_path):
    from studiovideo.edit.model import Timeline
    if not ffmpeg.available():
        pytest.skip("FFmpeg absent")
    vid = tmp_path / "c.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:d=1:r=25",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(vid)], check=True)
    app = build_context(tmp_path / "d")
    try:
        pid = app.projects.quick_create(app.concepts, "smart_clipping", "P")
        aid = app.importer().import_file(vid, project_id=pid).asset_id
        tl = Timeline()
        tl.set_aspect("9:16", 180)
        tl.audio.normalize = False
        tl.export.preset = "ultrafast"
        tl.add_segment(aid, 0, 1)
        dest = tmp_path / "out" / "Mon clip.mp4"
        j = wait(app, app.jobs.submit("export_timeline", "Clip", {"timeline": tl.to_dict(), "dest": str(dest),
                                                                    "project_id": pid, "caption": "Légende #tag"}))
        assert j.status == "succeeded", j.error
        assert dest.exists() and dest.with_suffix(".txt").read_text(encoding="utf-8") == "Légende #tag"
    finally:
        app.close()


def test_chat_models_keep_only_text_models():
    ids = ["gpt-4o-mini", "text-embedding-3-small", "tts-1", "whisper-1", "gpt-4o-realtime-preview", "o3-mini",
           "dall-e-3", "gpt-4o", "gpt-4o-mini"]
    assert hl.chat_models(ids) == ["gpt-4o", "gpt-4o-mini", "o3-mini"]
    assert hl.chat_models(["mistral-large", "mistral-embed"], "https://api.mistral.ai", "openai") == ["mistral-large"]
