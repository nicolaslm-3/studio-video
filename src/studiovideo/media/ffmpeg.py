"""Accès à FFmpeg / FFprobe.

Recherche des binaires dans l'ordre :
1. dossier `ffmpeg/` livré à côté de l'exécutable (build Windows) ;
2. variable STUDIOVIDEO_FFMPEG_DIR ;
3. PATH du système.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from ..paths import bundle_dir, install_dir

_EXE = ".exe" if sys.platform == "win32" else ""
_NOWIN = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class FFmpegNotFound(RuntimeError):
    pass


def _find(name: str) -> str | None:
    candidates = [bundle_dir() / "ffmpeg" / f"{name}{_EXE}", install_dir() / "ffmpeg" / f"{name}{_EXE}"]
    env = os.environ.get("STUDIOVIDEO_FFMPEG_DIR")
    if env:
        candidates.insert(0, Path(env) / f"{name}{_EXE}")
    for c in candidates:
        if c.is_file():
            return str(c)
    return shutil.which(name)


def ffmpeg_bin() -> str:
    p = _find("ffmpeg")
    if not p:
        raise FFmpegNotFound("FFmpeg est introuvable. Réinstallez l'application ou indiquez son dossier.")
    return p


def ffprobe_bin() -> str:
    p = _find("ffprobe")
    if not p:
        raise FFmpegNotFound("FFprobe est introuvable.")
    return p


def available() -> bool:
    return bool(_find("ffmpeg") and _find("ffprobe"))


def version() -> str:
    try:
        out = subprocess.run([ffmpeg_bin(), "-version"], capture_output=True, text=True,
                             timeout=10, creationflags=_NOWIN).stdout
        return out.splitlines()[0] if out else "inconnue"
    except Exception as exc:
        return f"indisponible ({exc})"


@dataclass
class ProbeResult:
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    audio_tracks: int = 0
    audio_channels: int | None = None
    sample_rate: int | None = None
    has_video: bool = False
    is_still_image: bool = False
    raw: dict = field(default_factory=dict)


def _fps(rate: str | None) -> float | None:
    try:
        f = Fraction(rate) if rate else None
        return round(float(f), 3) if f else None
    except (ValueError, ZeroDivisionError):
        return None


def probe(path: Path) -> ProbeResult:
    cmd = [ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120, creationflags=_NOWIN)
    if proc.returncode != 0:
        raise ValueError(f"Fichier illisible : {proc.stderr.strip()[:300]}")
    data = json.loads(proc.stdout or "{}")
    res = ProbeResult(raw=data)
    fmt = data.get("format", {})
    try:
        res.duration_s = float(fmt["duration"]) if "duration" in fmt else None
    except ValueError:
        pass
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and not res.has_video:
            attached = s.get("disposition", {}).get("attached_pic") == 1
            if attached:
                continue
            res.has_video = True
            res.width, res.height = s.get("width"), s.get("height")
            res.video_codec = s.get("codec_name")
            res.fps = _fps(s.get("avg_frame_rate")) or _fps(s.get("r_frame_rate"))
            res.is_still_image = s.get("codec_name") in {"png", "mjpeg", "webp", "bmp", "tiff", "gif"} \
                and (res.duration_s is None or res.duration_s < 0.1 or fmt.get("format_name", "").endswith("_pipe"))
        elif s.get("codec_type") == "audio":
            res.audio_tracks += 1
            if res.audio_codec is None:
                res.audio_codec = s.get("codec_name")
                res.audio_channels = s.get("channels")
                try:
                    res.sample_rate = int(s.get("sample_rate")) if s.get("sample_rate") else None
                except ValueError:
                    pass
    return res


def make_thumbnail(src: Path, dest: Path, at_s: float = 1.0, width: int = 480) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, at_s):.3f}",
           "-i", str(src), "-frames:v", "1", "-vf", f"scale={width}:-2", str(dest)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, creationflags=_NOWIN)
    if proc.returncode != 0 or not dest.exists():
        # Vidéo très courte : réessayer sur la première image.
        cmd[cmd.index("-ss") + 1] = "0"
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, creationflags=_NOWIN)
        if proc.returncode != 0 or not dest.exists():
            raise RuntimeError(f"Miniature impossible : {proc.stderr.strip()[:300]}")
    return dest


def fmt_timecode(seconds: float | None, with_ms: bool = False) -> str:
    """« 1:23 » ; with_ms : au dixième de seconde, « 1:23.4 » (le millième n'apporte rien à l'écran)."""
    if seconds is None:
        return "—"
    s, d = divmod(int(round(max(0.0, seconds) * 10)), 10) if with_ms else (int(max(0.0, seconds)), 0)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    base = f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"
    return f"{base}.{d:d}" if with_ms else base
