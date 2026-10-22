"""Auto-test de l'application compilée.

    StudioVideo.exe --self-test resultat.json [--models DOSSIER] [--speech FICHIER.wav] [--piper DOSSIER]

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

    def separation():
        from .edit import separate
        if not (models and speech and separate.installed(Path(models))):
            return "ignoré (pas de modèle de séparation ou de voix fournis)"
        import numpy as np

        from .media import ffmpeg
        nowin = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        clean, mix = tmp / "sep_clean.wav", tmp / "sep_mix.wav"
        subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-i", speech, "-af",
                        "aresample=44100,pan=stereo|c0=c0|c1=c0,apad=whole_dur=8", "-t", "8", str(clean)],
                       check=True, capture_output=True, creationflags=nowin)
        subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-i", str(clean), "-f", "lavfi", "-i",
                        "sine=frequency=220:duration=8", "-f", "lavfi", "-i", "sine=frequency=330:duration=8",
                        "-filter_complex", "[1][2]amix=inputs=2,volume=1.5,aresample=44100,"
                        "pan=stereo|c0=c0|c1=c0[m];[0][m]amix=inputs=2:normalize=0", "-t", "8", str(mix)],
                       check=True, capture_output=True, creationflags=nowin)
        t0 = time.time()
        res = separate.separate_file(mix, tmp / "sep", Path(models))
        c, m, v = (separate._read_mix(Path(x)) for x in (clean, mix, res["voice"]))
        n = min(c.shape[1], m.shape[1], v.shape[1])

        def sdr(ref, est):
            return float(10 * np.log10((ref ** 2).sum() / (((ref - est) ** 2).sum() + 1e-9)))
        before, after = sdr(c[:, :n], m[:, :n]), sdr(c[:, :n], v[:, :n])
        if after < before + 6:
            raise RuntimeError(f"séparation insuffisante : {before:.1f} → {after:.1f} dB")
        return {"sdr_before": round(before, 1), "sdr_after": round(after, 1), "seconds": round(time.time() - t0, 1)}
    check("separation", separation)

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

    def pdf_and_original():
        """Lecture d'un PDF (pypdf embarqué) puis fabrication d'une vidéo « création originale »."""
        from .edit import original
        pdf = tmp / "doc.pdf"
        stream = b"BT /F1 18 Tf 72 720 Td (Le cafe vient d Ethiopie. Il est bu partout.) Tj ET"
        objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
                b"/Resources << /Font << /F1 5 0 R >> >> >>",
                b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
        data, offs = b"%PDF-1.4\n", []
        for i, ob in enumerate(objs, 1):
            offs.append(len(data))
            data += f"{i} 0 obj\n".encode() + ob + b"\nendobj\n"
        xref = len(data)
        data += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        data += b"".join(f"{x:010d} 00000 n \n".encode() for x in offs)
        data += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
        pdf.write_bytes(data)
        text = original.clean_text(original.read_source(pdf))
        if "Ethiopie" not in text:
            raise RuntimeError(f"texte du PDF non lu : {text!r}")
        scenes = original.script_from_text(text, 6)
        res = original.build(scenes, 360, 640, 25, tmp / "creation.mp4", tmp / "orig")
        return {"pdf_text": text, "scenes": len(scenes), "duration": res["duration"]}
    check("pdf_original", pdf_and_original)

    piper_dir = opt("--piper")

    def piper_voice():
        if not piper_dir:
            return "ignoré (pas de moteur Piper fourni)"
        from .ai.tts import PiperTTS
        from .ai import piper as pmod
        os.environ["STUDIOVIDEO_PIPER_EXE"] = str(Path(piper_dir) / "piper" / ("piper.exe" if sys.platform == "win32"
                                                                               else "piper"))
        voice = next(Path(piper_dir).glob("*.onnx"))
        mdir = tmp / "pmodels"
        (mdir / "piper").mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy(voice, mdir / "piper" / voice.name)
        shutil.copy(str(voice) + ".json", mdir / "piper" / (voice.name + ".json"))
        wav = PiperTTS(mdir).synth("Bonjour et bienvenue dans le studio vidéo.", voice.stem, "fr", tmp / "piper.wav")
        out = {"voice": voice.stem, "bytes": wav.stat().st_size, "engine": str(pmod.exe_path(mdir))}
        if models:
            from .transcribe import engines
            eng = engines.FasterWhisperEngine(Path(models), "tiny")
            tr = engines.transcribe_file(eng, wav, "fr", lambda f, m: None, lambda: False)
            out["heard"] = tr.text()
            if not any(w in tr.text().lower() for w in ("bienvenue", "studio", "bonjour")):
                raise RuntimeError(f"voix incompréhensible : {tr.text()!r}")
        return out
    check("piper_voice", piper_voice)

    required = ["database", "ffmpeg", "updater", "asr_engine", "export_subtitles", "pdf_original"]
    if piper_dir:
        required.append("piper_voice")
    if models and speech:
        required.append("asr_transcription")
    report["ok"] = all(checks.get(k, {}).get("ok") for k in required)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return 0 if report["ok"] else 1
