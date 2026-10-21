"""Auto-test de l'application compilée.

    StudioVideo.exe --self-test resultat.json [--models DOSSIER] [--speech FICHIER.wav]

Vérifie, dans l'exécutable réellement distribué, que les composants embarqués
fonctionnent : base et migrations, FFmpeg, rendu des textes, export vidéo avec
sous-titres, moteur de transcription (et transcription réelle si un modèle et un
enregistrement de voix sont fournis). Le résultat est écrit en JSON (l'exécutable
n'a pas de console). Code de sortie 0 si tout est correct.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path


def run(argv: list[str]) -> int:
    out = Path(argv[argv.index("--self-test") + 1])

    def opt(name):
        return argv[argv.index(name) + 1] if name in argv else None
    models, speech = opt("--models"), opt("--speech")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    report: dict = {"ok": False, "checks": {}, "python": sys.version, "frozen": bool(getattr(sys, "frozen", False))}
    checks = report["checks"]

    def check(name, fn):
        t0 = time.time()
        try:
            checks[name] = {"ok": True, "detail": fn(), "s": round(time.time() - t0, 2)}
        except Exception as exc:
            checks[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                            "trace": traceback.format_exc()[-1500:], "s": round(time.time() - t0, 2)}

    tmp = Path(tempfile.mkdtemp(prefix="sv-selftest-"))
    os.environ["STUDIOVIDEO_HOME"] = str(tmp / "data")
    from . import __version__
    report["version"] = __version__

    def db():
        from .core import build_context
        ctx = build_context()
        v = ctx.db.schema_version()
        ctx.close()
        return f"schéma v{v}"
    check("database", db)

    def ff():
        from .media import ffmpeg
        return ffmpeg.version()
    check("ffmpeg", ff)

    def updater():
        from .paths import bundle_dir, install_dir
        from .update import config
        dll = [str(d / "WinSparkle.dll") for d in (bundle_dir(), install_dir()) if (d / "WinSparkle.dll").exists()]
        if sys.platform == "win32" and report["frozen"] and not dll:
            raise RuntimeError("WinSparkle.dll absent")
        return {"dll": dll, "public_key": bool(config.PUBLIC_KEY), "feed": config.appcast_url()}
    check("updater", updater)

    def asr_engine():
        import ctranslate2
        import faster_whisper
        return {"faster_whisper": faster_whisper.__version__, "ctranslate2": ctranslate2.__version__,
                "cpu_types": sorted(ctranslate2.get_supported_compute_types("cpu"))}
    check("asr_engine", asr_engine)

    transcript = {}

    def asr_real():
        if not (models and speech):
            return "ignoré (pas de modèle ou de voix fournis)"
        from .transcribe import engines
        eng = engines.FasterWhisperEngine(Path(models), "tiny")
        tr = engines.transcribe_file(eng, Path(speech), "en", lambda f, m: None, lambda: False)
        if not tr.segments:
            raise RuntimeError("aucune parole reconnue")
        transcript["tr"] = tr
        return {"text": tr.text(), "words": len(tr.all_words()), "language": tr.language}
    check("asr_transcription", asr_real)

    def export_with_subs():
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])  # noqa: F841
        from .edit.model import Timeline
        from .edit.render import Source, export
        from .edit.subtitles import SubtitleTrack, generate
        from .media import ffmpeg
        from .transcribe.model import TSegment, Transcript, Word
        vid = tmp / "src.mp4"
        dur = 4.0
        subprocess.run([ffmpeg.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"testsrc2=size=640x360:rate=25:duration={dur}", "-f", "lavfi", "-i",
                        f"sine=frequency=300:duration={dur}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", str(vid)], check=True, capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        tr = transcript.get("tr") or Transcript([TSegment(0.2, 3.5, "Bonjour et bienvenue dans le studio", [
            Word(0.2 + i * 0.5, 0.6 + i * 0.5, " " + w) for i, w in enumerate("Bonjour et bienvenue dans le studio".split())])])
        tl = Timeline()
        tl.set_aspect("9:16", 360)
        tl.export.preset = "ultrafast"
        tl.add_segment(1, 0, dur)
        tl.add_text("Auto-test", 0, 2, box=True)
        tl.subtitles = SubtitleTrack(animation="highlight", sidecar_srt=True)
        tl.subtitles.cues = generate(tl, {1: tr})
        if not tl.subtitles.cues:
            raise RuntimeError("aucun sous-titre généré")

        def runner(cmd, on_line):
            p = subprocess.run(cmd, capture_output=True, text=True,
                               creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            for line in p.stderr.splitlines(keepends=True):
                on_line(line)
            if p.returncode != 0:
                raise RuntimeError(p.stderr[-800:])
        rep = export(tl, {1: Source(str(vid), 640, 360, dur, True)}, None, tmp / "out.mp4", runner)
        return {k: rep[k] for k in ("width", "height", "duration", "lufs", "true_peak")} | {
            "cues": len(tl.subtitles.cues), "srt": Path(rep["srt"]).exists()}
    check("export_subtitles", export_with_subs)

    required = ["database", "ffmpeg", "updater", "asr_engine", "export_subtitles"]
    if models and speech:
        required.append("asr_transcription")
    report["ok"] = all(checks.get(k, {}).get("ok") for k in required)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return 0 if report["ok"] else 1
