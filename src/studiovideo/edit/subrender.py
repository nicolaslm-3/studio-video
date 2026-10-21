"""Dessin des sous-titres (Qt) et préparation de leur incrustation par FFmpeg.

Chaque état d'affichage (sous-titre, mot surligné, étape d'animation) est dessiné
une fois en PNG transparent dans une bande horizontale de l'image. FFmpeg reçoit
la liste des images avec leurs durées (démultiplexeur « concat ») et les incruste
en une seule opération, quel que soit le nombre de sous-titres.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QTransform

from .subtitles import State, SubtitleStyle, SubtitleTrack, states, wrap_lines
from .textrender import REFERENCE_HEIGHT, _ensure_app

STRIP_FRACTION = 0.36


def strip_geometry(style: SubtitleStyle, W: int, H: int) -> tuple[int, int]:
    """(y, hauteur) de la bande où sont dessinés les sous-titres."""
    h = int(H * STRIP_FRACTION) // 2 * 2
    center = {"top": 0.2, "center": 0.5, "bottom": 0.70}.get(style.position, style.y)
    y = int(round(center * H - h / 2))
    y = max(0, min(H - h, y)) // 2 * 2
    return y, h


def _font(style: SubtitleStyle, H: int) -> QFont:
    f = QFont(style.font)
    f.setPixelSize(max(8, int(style.size * H / REFERENCE_HEIGHT)))
    f.setBold(style.bold)
    f.setItalic(style.italic)
    return f


def render_state(track: SubtitleTrack, state: State, W: int, H: int) -> QImage:
    """Image transparente W × hauteur de bande pour un état."""
    _ensure_app()
    style = track.style
    y0, sh = strip_geometry(style, W, H)
    img = QImage(W, sh, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    cues = sorted(track.cues, key=lambda c: c.start)
    if state.cue_index < 0 or state.cue_index >= len(cues):
        return img
    cue = cues[state.cue_index]
    words = [w for _, _, w in cue.word_list()] or cue.text.split()
    if style.uppercase:
        words = [w.upper() for w in words]
    font = _font(style, H)
    fm = QFontMetricsF(font)
    max_w = W * style.max_width
    lines = wrap_lines(words, track.rules.max_chars, track.rules.max_lines)
    # sécurité : une ligne trop large est rognée en réduisant la police
    widest = max((fm.horizontalAdvance(" ".join(l)) for l in lines), default=0)
    if widest > max_w:
        font.setPixelSize(max(8, int(font.pixelSize() * max_w / widest)))
        fm = QFontMetricsF(font)
    line_h = fm.height() * style.line_spacing
    block_h = line_h * len(lines)
    space = fm.horizontalAdvance(" ")
    top = (sh - block_h) / 2

    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
    if state.scale != 1.0:
        t = QTransform()
        t.translate(W / 2, sh / 2)
        t.scale(state.scale, state.scale)
        t.translate(-W / 2, -sh / 2)
        p.setTransform(t)
    p.setOpacity(max(0.0, min(1.0, state.alpha)))

    if style.box:
        widths = [fm.horizontalAdvance(" ".join(l)) for l in lines]
        pad = font.pixelSize() * 0.3
        c = QColor(style.box_color)
        c.setAlphaF(max(0.0, min(1.0, style.box_opacity)))
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        for i, lw in enumerate(widths):
            r = QRectF((W - lw) / 2 - pad, top + i * line_h - pad * 0.25, lw + 2 * pad, line_h + pad * 0.3)
            p.drawRoundedRect(r, pad * 0.5, pad * 0.5)

    k = 0
    outline_w = style.outline * H / REFERENCE_HEIGHT * 2
    for i, line in enumerate(lines):
        lw = fm.horizontalAdvance(" ".join(line))
        x = (W - lw) / 2
        base = top + i * line_h + fm.ascent()
        for w in line:
            visible = state.visible < 0 or k < state.visible
            if visible:
                path = QPainterPath()
                path.addText(QPointF(x, base), font, w)
                if style.shadow:
                    sp = QPainterPath(path)
                    sp.translate(outline_w * 0.35 + 1, outline_w * 0.45 + 1)
                    p.fillPath(sp, QColor(0, 0, 0, 110))
                if style.outline > 0:
                    pen = QPen(QColor(style.outline_color), outline_w)
                    pen.setJoinStyle(Qt.RoundJoin)
                    p.strokePath(path, pen)
                col = style.highlight_color if k == state.highlight and track.animation != "word" else style.color
                if track.animation == "word" and k == state.highlight:
                    col = style.highlight_color
                p.fillPath(path, QColor(col))
            x += fm.horizontalAdvance(w) + space
            k += 1
    p.end()
    return img


def write_overlay(track: SubtitleTrack, total: float, W: int, H: int, fps: float, out_dir: Path,
                  progress=None) -> tuple[Path, int, int] | None:
    """Prépare les images et la liste « concat ». Retourne (liste, y de la bande, nombre d'images)."""
    if not track.enabled or not track.burn or not track.cues:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    y, _h = strip_geometry(track.style, W, H)
    seq = states(track, total, fps)
    files: dict[State, str] = {}
    lines = ["ffconcat version 1.0"]
    for n, (a, b, st) in enumerate(seq):
        key = st if st.cue_index >= 0 else State(-1)
        if key not in files:
            name = f"sub_{len(files):05d}.png"
            if not render_state(track, key, W, H).save(str(out_dir / name), "PNG"):
                raise RuntimeError("Écriture d'une image de sous-titre impossible.")
            files[key] = name
        lines += [f"file '{files[key]}'", f"duration {b - a:.4f}"]
        if progress and n % 25 == 0:
            progress(n / max(1, len(seq)))
    blank = files.get(State(-1))
    if blank is None:
        blank = "sub_blank.png"
        render_state(track, State(-1), W, H).save(str(out_dir / blank), "PNG")
    lines += [f"file '{blank}'", "duration 1.0", f"file '{blank}'"]
    lst = out_dir / "subtitles.ffconcat"
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lst, y, len(files)
