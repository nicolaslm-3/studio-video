"""Miniature de publication (§18) : une image de la vidéo, avec le titre en option.

Le rendu du titre est celui des textes de la vidéo (mêmes polices, contour, fond). JPEG de moins de
2 Mo (limite YouTube) ; le moment choisi sert aussi d'image de couverture sur TikTok et Instagram.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ..media import ffmpeg

_NOWIN = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
MAX_BYTES = 2 * 1024 * 1024


def frame(video: Path, t: float, dest: Path, long_side: int = 1280) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    vf = f"scale='if(gte(iw,ih),{long_side},-2)':'if(gte(iw,ih),-2,{long_side})':flags=lanczos"
    p = subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-ss", f"{max(0.0, t):.3f}", "-i",
                        str(video), "-frames:v", "1", "-vf", vf, str(dest)], capture_output=True, text=True,
                       creationflags=_NOWIN)
    if p.returncode != 0 or not dest.exists():
        raise RuntimeError(f"Image impossible à extraire : {p.stderr[-300:]}")
    return dest


def make(video: Path, t: float, title: str, dest: Path, style: dict | None = None) -> Path:
    """Miniature JPEG : image à l'instant t + titre (vide = sans titre)."""
    from PySide6.QtGui import QImage, QPainter

    from ..edit.model import TextItem
    from ..edit.textrender import _ensure_app, render_text_image
    _ensure_app()
    png = frame(video, t, dest.with_suffix(".frame.png"))
    img = QImage(str(png))
    png.unlink(missing_ok=True)
    if img.isNull():
        raise RuntimeError("Image de la vidéo illisible.")
    if title.strip():
        st = {"position": "bottom", "size": 120, "box": True, "box_opacity": 0.55, "outline": 8, "max_width": 0.9}
        st.update({k: v for k, v in (style or {}).items() if k in TextItem.__dataclass_fields__
                   and k not in ("text", "start", "end", "id")})
        txt = render_text_image(TextItem(text=title.strip(), **st), img.width(), img.height())
        p = QPainter(img)
        p.drawImage(0, 0, txt)
        p.end()
    dest.parent.mkdir(parents=True, exist_ok=True)
    for q in (92, 85, 75, 60):
        img.save(str(dest), "JPG", q)
        if dest.stat().st_size <= MAX_BYTES:
            break
    return dest
