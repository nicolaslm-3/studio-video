"""Rendu des textes en images transparentes (PNG) avec Qt.

Les textes sont dessinés par Qt puis incrustés par FFmpeg : l'aperçu et l'export
utilisent exactement le même rendu (police, contour, fond), et on évite les
problèmes d'échappement et de polices de drawtext sous Windows.
QImage / QPainter fonctionnent hors du thread graphique.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QGuiApplication, QImage, QPainter, QPainterPath, QPen,
                           QTextLayout, QTextOption)

from . import emoji
from .model import TextItem

REFERENCE_HEIGHT = 1920


def _ensure_app() -> None:
    if QGuiApplication.instance() is None:  # utilisation hors interface (tests, CLI)
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        # QApplication (et non QGuiApplication) pour rester compatible avec une interface ouverte ensuite
        from PySide6.QtWidgets import QApplication
        _ensure_app.app = QApplication([])  # type: ignore[attr-defined]


def _lines(text: str, font: QFont, max_width: float) -> list[str]:
    out = []
    for para in text.splitlines() or [""]:
        layout = QTextLayout(para, font)
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(opt)
        layout.beginLayout()
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max_width)
            out.append(para[line.textStart(): line.textStart() + line.textLength()].rstrip())
        layout.endLayout()
    return out


def anchor(item: TextItem, width: int = 1080, height: int = 1920) -> tuple[float, float]:
    """Centre du bloc de texte, en fractions de l'image : celui de la position choisie (en haut, au centre, en bas)
    — les réglages X / Y partent de là, sans saut."""
    if item.position not in ("top", "center", "bottom"):
        return item.x, item.y
    _ensure_app()
    font = QFont(item.font)
    font.setPixelSize(max(8, int(item.size * height / REFERENCE_HEIGHT)))
    font.setBold(item.bold)
    font.setItalic(item.italic)
    fm = QFontMetricsF(font)
    block_h = fm.height() * 1.05 * len(_lines(item.text, font, width * item.max_width))
    top = {"top": height * 0.12, "center": (height - block_h) / 2, "bottom": height * 0.78 - block_h}[item.position]
    top = max(0.0, min(height - block_h, top))
    return 0.5, round((top + block_h / 2) / height, 3)


def render_text(item: TextItem, width: int, height: int, dest: Path) -> Path:
    """Dessine `item` sur une image transparente de la taille de la vidéo et l'enregistre en PNG."""
    img = render_text_image(item, width, height)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not img.save(str(dest), "PNG"):
        raise RuntimeError(f"Impossible d'écrire l'image du texte : {dest}")
    return dest


def render_text_image(item: TextItem, width: int, height: int) -> QImage:
    """Même rendu que l'export, en mémoire (utilisé par l'aperçu en direct)."""
    _ensure_app()
    k = height / REFERENCE_HEIGHT
    img = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
    font = QFont(item.font)
    font.setPixelSize(max(8, int(item.size * k)))
    font.setBold(item.bold)
    font.setItalic(item.italic)
    fm = QFontMetricsF(font)
    max_w = width * item.max_width
    lines = _lines(item.text, font, max_w)
    line_h = fm.height() * 1.05
    block_w = max((emoji.width(fm, font, l) for l in lines), default=0)
    block_h = line_h * len(lines)

    cx = width / 2
    if item.position == "top":
        top = height * 0.12
    elif item.position == "center":
        top = (height - block_h) / 2
    elif item.position == "bottom":
        top = height * 0.78 - block_h
    else:
        cx = width * item.x
        top = height * item.y - block_h / 2
    if item.align == "left":
        left = cx - block_w / 2
    elif item.align == "right":
        left = cx + block_w / 2
    else:
        left = cx
    top = max(0.0, min(height - block_h, top))

    if item.box and lines:
        pad = font.pixelSize() * 0.35
        c = QColor(item.box_color)
        c.setAlphaF(max(0.0, min(1.0, item.box_opacity)))
        rect = QRectF(cx - block_w / 2 - pad, top - pad * 0.6, block_w + 2 * pad, block_h + pad * 1.2)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(rect, pad * 0.6, pad * 0.6)

    path = QPainterPath()
    marks = []                                            # emojis : en couleur, par-dessus le texte
    for i, line in enumerate(lines):
        lw = emoji.width(fm, font, line)
        if item.align == "left":
            x = left
        elif item.align == "right":
            x = left - lw
        else:
            x = cx - lw / 2
        marks += emoji.add_line(path, font, fm, x, top + i * line_h + fm.ascent(), line)
    if item.outline > 0:
        pen = QPen(QColor(item.outline_color), item.outline * k * 2)
        pen.setJoinStyle(Qt.RoundJoin)
        p.strokePath(path, pen)
    p.fillPath(path, QColor(item.color))
    emoji.draw(p, marks)
    p.end()
    return img
