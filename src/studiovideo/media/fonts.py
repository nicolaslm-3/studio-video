"""Polices ajoutées par l'utilisateur : copiées dans <données>/fonts et chargées au démarrage (textes, sous-titres)."""
from __future__ import annotations

import shutil
from pathlib import Path


def fonts_dir(root: Path) -> Path:
    return Path(root) / "fonts"


def load_user_fonts(root: Path) -> list[str]:
    from PySide6.QtGui import QFontDatabase
    fams: list[str] = []
    d = fonts_dir(root)
    if d.is_dir():
        for f in sorted(d.iterdir()):
            if f.suffix.lower() in (".ttf", ".otf"):
                fid = QFontDatabase.addApplicationFont(str(f))
                if fid >= 0:
                    fams += QFontDatabase.applicationFontFamilies(fid)
    return fams


def install_font(root: Path, path) -> list[str]:
    from PySide6.QtGui import QFontDatabase
    src = Path(path)
    if src.suffix.lower() not in (".ttf", ".otf") or not src.is_file():
        raise ValueError("Choisissez un fichier de police .ttf ou .otf.")
    d = fonts_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / src.name
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    fid = QFontDatabase.addApplicationFont(str(dest))
    if fid < 0:
        dest.unlink(missing_ok=True)
        raise ValueError("Cette police n'a pas pu être chargée (fichier endommagé ou format non pris en charge).")
    return list(QFontDatabase.applicationFontFamilies(fid))
