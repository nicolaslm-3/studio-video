"""Recadrage intelligent : suivi (logique pure), export réel vérifié par le détecteur de visages."""
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest

from studiovideo.edit import reframe as r
from studiovideo.media import ffmpeg

MODEL, FACE = os.environ.get("STUDIOVIDEO_FACE_MODEL", ""), os.environ.get("STUDIOVIDEO_FACE_IMG", "")


def _det(c, w=0.1):
    return [(c - w / 2, 0.3, c + w / 2, 0.5, 0.99)]


def test_track_static_moving_and_cut():
    t = [i / 4 for i in range(40)]
    jitter = [_det(0.4 + 0.02 * np.sin(i)) for i in range(40)]
    keys = r.track(t, jitter)
    assert len({c for _, c in keys}) == 1 and abs(keys[0][1] - 0.4) < 0.03       # cadre fixe
    moving = [_det(0.2 + 0.05 * ti) for ti in t]                                  # 0,05 largeur / s
    keys = r.track(t, moving)
    assert abs(r.cx_at(keys, 5) - 0.45) < 0.03 and len(keys) <= 6               # suit, peu de points clés
    cut = [_det(0.2) for _ in range(20)] + [_det(0.8) for _ in range(20)]
    keys = r.track(t, cut)
    assert abs(r.cx_at(keys, 4.9) - 0.2) < 0.01 and abs(r.cx_at(keys, 5.0) - 0.8) < 0.01   # coupe franche
    none = r.track(t, [[] for _ in t])
    assert r.cx_at(none, 3) == 0.5
    gaps = [_det(0.7) if i % 3 == 0 else [] for i in range(40)]                   # visage intermittent
    assert abs(r.cx_at(r.track(t, gaps), 6) - 0.7) < 0.02


def test_crop_expression_is_valid_ffmpeg(tmp_path):
    if not ffmpeg.available():
        pytest.skip("FFmpeg absent")
    keys = [[0, 0.2], [2, 0.8]]
    expr = r.crop_x_expr(keys, 0, 2, 1138, 360)
    src = tmp_path / "s.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1138x640:d=2",
                    "-vf", f"crop=360:640:x='{expr}':y=0", "-frames:v", "10", str(src)], check=True)


@pytest.mark.skipif(not (MODEL and FACE and Path(MODEL).exists() and ffmpeg.available()),
                    reason="détecteur (STUDIOVIDEO_FACE_MODEL) ou photo (STUDIOVIDEO_FACE_IMG) absents")
def test_reframed_export_keeps_face_centered(tmp_path):
    import shutil

    from studiovideo.edit.model import Timeline
    from studiovideo.edit.render import Source, export
    models = tmp_path / "m"
    (models / "faces").mkdir(parents=True)
    shutil.copy(MODEL, r.model_path(models))
    face = tmp_path / "face.png"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-i", FACE, "-vf", "crop=200:260:510:30", str(face)],
                   check=True)
    vid = tmp_path / "moving.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=1280x720:d=8:r=25",
                    "-i", str(face), "-filter_complex", "[0][1]overlay=x='if(lt(t,5),100+t*120,1000)':y=200",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(vid)], check=True)
    res = r.analyze(vid, 0, 8, models)
    assert res["with_face"] >= res["frames"] - 1
    tl = Timeline()
    tl.set_aspect("9:16", 640)
    tl.audio.normalize = False
    seg = tl.add_segment(1, 0, 8)
    seg.reframe = [list(k) for k in res["keys"]]

    def run(cmd, on_line):
        p = subprocess.run(cmd, capture_output=True, text=True)
        assert p.returncode == 0, p.stderr[-800:]
        return 0
    out = tmp_path / "out.mp4"
    export(tl, {1: Source(str(vid), 1280, 720, 8.0, False)}, None, out, run)
    det = r.FaceDetector(models)
    for t in (0.5, 2.5, 4.5, 6.0, 7.5):
        p = subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1",
                            "-vf", f"scale={r.IW}:{r.IH},format=rgb24", "-f", "rawvideo", "-"],
                           capture_output=True, check=True)
        faces = det.detect(np.frombuffer(p.stdout, np.uint8).reshape(r.IH, r.IW, 3))
        assert faces, t
        assert abs((faces[0][0] + faces[0][2]) / 2 - 0.5) < 0.1, (t, faces[0])     # visage au centre du cadre


def test_export_job_resolves_auto_reframe(tmp_path, monkeypatch):
    import time

    from studiovideo.core import build_context
    from studiovideo.edit.model import Timeline
    if not ffmpeg.available():
        pytest.skip("FFmpeg absent")
    vid = tmp_path / "h.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:d=2:r=25",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(vid)], check=True)
    calls = []
    monkeypatch.setattr(r, "analyze", lambda path, a, b, m, *x, **k: calls.append((a, b)) or
                        {"keys": [(0.0, 0.2), (2.0, 0.8)], "frames": 8, "with_face": 8})
    app = build_context(tmp_path / "d")
    try:
        c = app.concepts.create_concept("C")
        pid = app.projects.create(c.id, "P")
        aid = app.importer().import_file(vid, project_id=pid).asset_id
        tl = Timeline()
        tl.set_aspect("9:16", 360)
        tl.video.auto_reframe = True
        tl.audio.normalize = False
        tl.add_segment(aid, 0, 2)
        jid = app.jobs.submit("export_timeline", "Export", {"timeline": tl.to_dict(), "dest": str(tmp_path / "o.mp4"),
                                                             "project_id": pid})
        end = time.time() + 60
        while app.jobs.get(jid).status in ("pending", "running") and time.time() < end:
            time.sleep(0.05)
        j = app.jobs.get(jid)
        assert j.status == "succeeded", j.error
        assert calls == [(0.0, 2.0)] and (tmp_path / "o.mp4").exists()
    finally:
        app.close()
