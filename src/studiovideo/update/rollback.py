"""Restauration de la version précédente (§28, point 12).

Avant chaque mise à jour, l'installateur copie le programme en place dans
%LOCALAPPDATA%\\StudioVideo\\rollback\\app (avec version.txt) et dépose le script
restore-previous.cmd à côté. Ce script :
1. ferme l'application ;
2. recopie le programme précédent dans le dossier d'installation ;
3. restaure la sauvegarde de base faite juste avant la mise à jour
   (la nouvelle version a pu migrer la base vers un schéma que l'ancienne ne connaît pas) ;
4. relance l'application.
Il est aussi accessible depuis le menu Démarrer si l'application ne démarre plus.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ..paths import AppPaths, install_dir


def rollback_root(paths: AppPaths) -> Path:
    return paths.root / "rollback"


def previous_version(paths: AppPaths) -> str | None:
    root = rollback_root(paths)
    if not (root / "app" / "StudioVideo.exe").exists() or not (root / "restore-previous.cmd").exists():
        return None
    try:
        return (root / "app" / "version.txt").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def launch_rollback(paths: AppPaths) -> None:
    if sys.platform != "win32":
        raise RuntimeError("La restauration n'est disponible que sous Windows.")
    if not previous_version(paths):
        raise RuntimeError("Aucune version précédente n'est conservée.")
    script = rollback_root(paths) / "restore-previous.cmd"
    subprocess.Popen(["cmd", "/c", str(script), str(install_dir())],
                     creationflags=subprocess.CREATE_NEW_CONSOLE)
