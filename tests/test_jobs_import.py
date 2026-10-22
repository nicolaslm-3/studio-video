import subprocess
import threading
import time

import pytest

from studiovideo.core import build_context
from studiovideo.jobs.manager import JobManager
from studiovideo.media import ffmpeg
from studiovideo.media.importer import DuplicateAsset, fingerprint

needs_ffmpeg = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")


def wait(jm: JobManager, jid: int, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        j = jm.get(jid)
        if j.status in ("succeeded", "failed", "cancelled"):
            return j
        time.sleep(0.02)
    raise TimeoutError(jm.get(jid))


@pytest.fixture
def app(tmp_path):
    a = build_context(tmp_path / "data")
    yield a
    a.close()


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    if not ffmpeg.available():
        pytest.skip("FFmpeg absent")
    p = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p)], check=True)
    return p


def test_job_success_progress_and_failure(app):
    seen = []
    app.jobs.register("ok", lambda ctx, p: (ctx.progress(0.5, "mi-chemin"), {"v": p["x"] * 2})[1])
    app.jobs.register("ko", lambda ctx, p: 1 / 0)
    app.jobs.add_listener(seen.append)
    j1 = wait(app.jobs, app.jobs.submit("ok", "OK", {"x": 21}))
    assert j1.status == "succeeded" and j1.result == {"v": 42} and j1.progress == 1.0
    j2 = wait(app.jobs, app.jobs.submit("ko", "KO"))
    assert j2.status == "failed" and "division" in j2.error
    assert seen


def test_job_cancel_and_retry(app):
    started = threading.Event()

    def slow(ctx, p):
        started.set()
        for _ in range(500):
            ctx.check()
            time.sleep(0.01)
        return {}
    app.jobs.register("slow", slow)
    jid = app.jobs.submit("slow", "Lent")
    assert started.wait(5)
    app.jobs.cancel(jid)
    assert wait(app.jobs, jid).status == "cancelled"
    app.jobs.register("slow", lambda ctx, p: {"ok": True})
    app.jobs.retry(jid)
    assert wait(app.jobs, jid).status == "succeeded"


def test_cancel_kills_external_process(app, tmp_path):
    started = threading.Event()

    def proc(ctx, p):
        started.set()
        ctx.run_process(["sleep", "30"])
    app.jobs.register("proc", proc)
    jid = app.jobs.submit("proc", "Processus")
    started.wait(5)
    time.sleep(0.2)
    t0 = time.time()
    app.jobs.cancel(jid)
    assert wait(app.jobs, jid).status == "cancelled"
    assert time.time() - t0 < 5


def test_running_jobs_marked_interrupted_on_restart(tmp_path):
    a = build_context(tmp_path / "d")
    a.db.connect().execute("INSERT INTO job(kind,label,status) VALUES ('x','x','running')")
    a.close()
    b = build_context(tmp_path / "d")
    (j,) = b.jobs.list()
    assert j.status == "failed" and "Interrompue" in j.error
    b.close()


def test_critical_jobs_tracking(app):
    gate = threading.Event()
    app.jobs.register("export", lambda ctx, p: gate.wait(5) and {})
    jid = app.jobs.submit("export", "Export", critical=True)
    time.sleep(0.1)
    assert [j.id for j in app.jobs.critical_running()] == [jid]
    assert not app.jobs.wait_idle(timeout=0.2, critical_only=True)
    gate.set()
    assert app.jobs.wait_idle(timeout=5, critical_only=True)


@needs_ffmpeg
def test_probe_and_import(app, sample_video):
    info = ffmpeg.probe(sample_video)
    assert info.has_video and info.width == 640 and info.height == 360
    assert info.fps == 25 and info.audio_tracks == 1 and 2.9 < info.duration_s < 3.2
    before = sample_video.read_bytes()
    j = wait(app.jobs, app.jobs.submit("import_file", "Import", {"path": str(sample_video)}))
    assert j.status == "succeeded", j.error
    a = app.library.get(j.result["asset_id"])
    assert a["kind"] == "video" and a["width"] == 640 and a["thumbnail_path"]
    assert sample_video.read_bytes() == before  # non destructif
    with pytest.raises(DuplicateAsset):
        app.importer().import_file(sample_video)
    j2 = wait(app.jobs, app.jobs.submit("import_file", "Import", {"path": str(sample_video)}))
    assert j2.status == "failed" and "déjà" in j2.error
    r = app.importer().import_file(sample_video, allow_duplicate=True)
    assert r.asset_id != a["id"]


@needs_ffmpeg
def test_missing_file_detection(app, sample_video, tmp_path):
    copy = tmp_path / "copy.mp4"
    copy.write_bytes(sample_video.read_bytes() + b"\0")
    r = app.importer().import_file(copy)
    copy.unlink()
    assert r.asset_id in app.importer().refresh_missing()


def test_fingerprint_large_file(tmp_path):
    p = tmp_path / "big.bin"
    with p.open("wb") as f:
        f.seek(50 * 1024 * 1024)
        f.write(b"x")
    a = fingerprint(p)
    with p.open("r+b") as f:
        f.seek(25 * 1024 * 1024)
        f.write(b"y")
    assert fingerprint(p) != a


def test_cancel_during_process_launch_is_not_lost(app, monkeypatch):
    """Annulation reçue pendant le lancement du processus (avant qu'il soit connu) : il est quand même tué."""
    import subprocess as sp

    from studiovideo.jobs import manager as mgr
    real = sp.Popen
    holder = {}

    def slow_popen(*a, **k):
        app.jobs.cancel(holder["jid"])              # l'annulation tombe pile pendant le lancement
        return real(*a, **k)

    def proc(ctx, p):
        monkeypatch.setattr(mgr.subprocess, "Popen", slow_popen)
        try:
            ctx.run_process(["sleep", "30"])
        finally:
            monkeypatch.setattr(mgr.subprocess, "Popen", real)
    app.jobs.register("proc2", proc)
    t0 = time.time()
    holder["jid"] = app.jobs.submit("proc2", "Processus")
    assert wait(app.jobs, holder["jid"]).status == "cancelled"
    assert time.time() - t0 < 5
