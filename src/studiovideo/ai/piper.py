"""Voix hors ligne Piper (cahier des charges §11) : gratuites, sur le PC, sans envoi de données.

- Moteur : programme Piper autonome (rhasspy/piper 2023.11.14-2, licence MIT), téléchargé une fois
  depuis GitHub et lancé comme programme séparé (comme FFmpeg).
- Voix : catalogue officiel rhasspy/piper-voices (Hugging Face) ; chaque voix est un fichier .onnx
  + .onnx.json, vérifié par son empreinte MD5 publiée. La licence propre à chaque voix figure dans
  sa fiche (MODEL_CARD).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

RELEASE = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/"
BINARIES = {"win32": "piper_windows_amd64.zip", "linux": "piper_linux_x86_64.tar.gz"}
VOICES = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/"


class PiperError(RuntimeError):
    pass


@dataclass
class VoiceInfo:
    key: str
    language: str          # « fr_FR »
    family: str            # « fr »
    name: str
    quality: str
    size_mb: float
    speakers: int
    files: dict            # chemin distant -> md5


def root(models_dir: Path) -> Path:
    return Path(models_dir) / "piper"


def exe_path(models_dir: Path) -> Path | None:
    override = os.environ.get("STUDIOVIDEO_PIPER_EXE")
    if override and Path(override).exists():
        return Path(override).resolve()
    p = root(models_dir) / "bin" / "piper" / ("piper.exe" if sys.platform == "win32" else "piper")
    return p if p.exists() else None


def _base(kind: str) -> str:
    env = os.environ.get("STUDIOVIDEO_PIPER_TEST_URL")       # serveur simulé (tests)
    if env:
        return env.rstrip("/") + "/"
    return RELEASE if kind == "bin" else VOICES


def _download(url: str, dest: Path, progress: Callable[[float, str], None], is_cancelled: Callable[[], bool],
              label: str, md5: str = "") -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".partial")
    h = hashlib.md5()
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while chunk := r.read(1 << 20):
                if is_cancelled():
                    raise PiperError("Annulé.")
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                progress(min(0.99, done / total) if total else 0.5, f"{label} : {done >> 20} Mo")
    except (OSError, urllib.error.URLError) as exc:
        part.unlink(missing_ok=True)
        raise PiperError(f"Téléchargement impossible ({label}) : {exc}") from exc
    except PiperError:
        part.unlink(missing_ok=True)
        raise
    if md5 and h.hexdigest() != md5:
        part.unlink(missing_ok=True)
        raise PiperError(f"Fichier corrompu ({label}) : empreinte différente.")
    part.replace(dest)
    return dest


def install_engine(models_dir: Path, progress=lambda f, m: None, is_cancelled=lambda: False) -> Path:
    plat = "win32" if sys.platform == "win32" else ("linux" if sys.platform.startswith("linux") else "")
    if plat not in BINARIES:
        raise PiperError("Les voix Piper ne sont disponibles que sous Windows dans cette version.")
    name = BINARIES[plat]
    arch = _download(_base("bin") + name, root(models_dir) / name, progress, is_cancelled, "moteur Piper")
    dest = root(models_dir) / "bin"
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    try:
        if name.endswith(".zip"):
            with zipfile.ZipFile(arch) as z:
                for m in z.namelist():                      # protection contre les chemins hors du dossier
                    if m.startswith("/") or ".." in Path(m).parts:
                        raise PiperError("Archive Piper invalide.")
                z.extractall(dest)
        else:
            with tarfile.open(arch) as t:
                t.extractall(dest, filter="data")
    finally:
        arch.unlink(missing_ok=True)
    exe = exe_path(models_dir)
    if exe is None:
        raise PiperError("Programme Piper introuvable après installation.")
    if sys.platform != "win32":
        exe.chmod(0o755)
    return exe


def catalog(models_dir: Path, refresh: bool = False) -> list[VoiceInfo]:
    cache = root(models_dir) / "voices.json"
    if refresh or not cache.exists():
        _download(_base("voices") + "voices.json", cache, lambda f, m: None, lambda: False, "catalogue")
    data = json.loads(cache.read_text(encoding="utf-8"))
    out = []
    for key, v in data.items():
        files = {p: (f or {}).get("md5_digest", "") for p, f in (v.get("files") or {}).items()
                 if p.endswith(".onnx") or p.endswith(".onnx.json")}
        if len(files) < 2:
            continue
        lang = v.get("language") or {}
        size = sum((f or {}).get("size_bytes", 0) for f in (v.get("files") or {}).values()) / 2 ** 20
        out.append(VoiceInfo(key, lang.get("code", ""), lang.get("family", key.split("_")[0]),
                             v.get("name", key), v.get("quality", ""), round(size, 1), int(v.get("num_speakers") or 1),
                             files))
    return sorted(out, key=lambda x: (x.family, x.language, {"high": 0, "medium": 1, "low": 2, "x_low": 3}
                                      .get(x.quality, 4), x.name))


def voice_path(models_dir: Path, key: str) -> Path:
    return root(models_dir) / f"{key}.onnx"


def installed_voices(models_dir: Path) -> list[str]:
    r = root(models_dir)
    return sorted(p.stem for p in r.glob("*.onnx") if p.with_name(p.name + ".json").exists()) if r.exists() else []


def download_voice(models_dir: Path, info: VoiceInfo, progress=lambda f, m: None, is_cancelled=lambda: False) -> Path:
    for remote, md5 in info.files.items():
        local = root(models_dir) / (f"{info.key}.onnx.json" if remote.endswith(".json") else f"{info.key}.onnx")
        _download(_base("voices") + remote, local, progress, is_cancelled, f"voix {info.key}", md5)
    return voice_path(models_dir, info.key)


def delete_voice(models_dir: Path, key: str) -> None:
    for suffix in (".onnx", ".onnx.json"):
        (root(models_dir) / f"{key}{suffix}").unlink(missing_ok=True)
