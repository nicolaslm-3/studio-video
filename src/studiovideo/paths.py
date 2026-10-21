"""Emplacements persistants des données utilisateur.

Les données ne sont JAMAIS stockées dans le dossier d'installation :
une mise à jour remplace le programme sans toucher à ces répertoires.

Windows : %LOCALAPPDATA%\\StudioVideo
Autres  : ~/.local/share/StudioVideo
La variable STUDIOVIDEO_HOME permet de forcer un autre dossier (tests, portable).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from . import __app_name__


def _default_root() -> Path:
    override = os.environ.get("STUDIOVIDEO_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / __app_name__
    return Path.home() / ".local" / "share" / __app_name__


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @property
    def database(self) -> Path:
        return self.root / "studio.db"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def thumbnails(self) -> Path:
        return self.cache / "thumbnails"

    @property
    def proxies(self) -> Path:
        return self.cache / "proxies"

    @property
    def temp(self) -> Path:
        return self.root / "tmp"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def presets(self) -> Path:
        return self.root / "presets"

    @property
    def projects(self) -> Path:
        return self.root / "projects"

    @property
    def dubs(self) -> Path:
        return self.root / "dubs"

    def ensure(self) -> "AppPaths":
        for p in (self.root, self.backups, self.logs, self.cache, self.thumbnails,
                  self.proxies, self.temp, self.models, self.presets, self.projects, self.dubs):
            p.mkdir(parents=True, exist_ok=True)
        return self


def app_paths(root: Path | None = None) -> AppPaths:
    return AppPaths(root or _default_root()).ensure()


def install_dir() -> Path:
    """Dossier du programme (lecture seule en pratique)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def bundle_dir() -> Path:
    """Dossier des fichiers embarqués (sys._MEIPASS = « _internal » avec PyInstaller 6)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent
