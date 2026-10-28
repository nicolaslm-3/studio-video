"""Emojis dans les textes incrustés (textes, sous-titres) : dessinés en couleur, à part du tracé du texte.

Le texte est un tracé (contour, couleur unie) : un emoji y deviendrait une silhouette. On le dessine donc à sa place
avec la police emoji de Windows ; les drapeaux, que Windows n'a pas (il écrit « FR »), sont des images
(`ui/flags/<pays>.png`, emojis 3D Noto, Apache 2.0)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QFontMetricsF, QImage, QPainter, QPainterPath

FLAG_DIR = Path(__file__).resolve().parent.parent / "ui" / "flags"
EMOJI_FONT = "Segoe UI Emoji"
_images: dict[str, QImage | None] = {}


def _ri(c: str) -> bool:
    return 0x1F1E6 <= ord(c) <= 0x1F1FF                  # lettres des drapeaux


def _emoji(c: str) -> bool:
    o = ord(c)
    return 0x1F000 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF or 0x2B00 <= o <= 0x2BFF or 0x2300 <= o <= 0x23FF


def _joined(c: str) -> bool:
    o = ord(c)
    return o in (0xFE0F, 0x20E3) or 0x1F3FB <= o <= 0x1F3FF or 0xE0020 <= o <= 0xE007F


def runs(text: str) -> list[tuple[str, bool]]:
    """Le texte découpé en morceaux de texte et en emojis (un drapeau, une famille… = un emoji)."""
    out: list[tuple[str, bool]] = []
    buf, i = "", 0
    while i < len(text):
        c = text[i]
        if _ri(c) and i + 1 < len(text) and _ri(text[i + 1]):
            j = i + 2
        elif _emoji(c):
            j = i + 1
            while j < len(text):
                if _joined(text[j]):
                    j += 1
                elif text[j] == "‍" and j + 1 < len(text):
                    j += 2
                else:
                    break
        else:
            buf += c
            i += 1
            continue
        if buf:
            out.append((buf, False))
            buf = ""
        out.append((text[i:j], True))
        i = j
    if buf:
        out.append((buf, False))
    return out


def flag_code(cluster: str) -> str | None:
    """« 🇫🇷 » → « fr » (code du pays)."""
    if len(cluster) == 2 and all(_ri(c) for c in cluster):
        return "".join(chr(ord(c) - 0x1F1E6 + ord("a")) for c in cluster)
    return None


def _side(font: QFont) -> float:
    return font.pixelSize() * 1.15


def width(fm: QFontMetricsF, font: QFont, text: str) -> float:
    return sum(_side(font) if e else fm.horizontalAdvance(s) for s, e in runs(text))


def add_line(path: QPainterPath, font: QFont, fm: QFontMetricsF, x: float, base: float,
             text: str) -> list[tuple[QRectF, str]]:
    """Ajoute le texte au tracé ; renvoie la place de chaque emoji, à dessiner ensuite avec `draw`."""
    marks = []
    side = _side(font)
    for s, e in runs(text):
        if e:
            y = base - fm.ascent() + (fm.height() - side) / 2
            marks.append((QRectF(x, y, side, side), s))
            x += side
        else:
            path.addText(QPointF(x, base), font, s)
            x += fm.horizontalAdvance(s)
    return marks


def _flag(code: str) -> QImage | None:
    if code not in _images:
        p = FLAG_DIR / f"{code}.png"
        img = QImage(str(p)) if p.exists() else QImage()
        _images[code] = None if img.isNull() else img
    return _images[code]


def draw(p: QPainter, marks: list[tuple[QRectF, str]]) -> None:
    for r, s in marks:
        code = flag_code(s)
        img = _flag(code) if code else None
        if img is not None:
            p.save()
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(r, img)
            p.restore()
            continue
        f = QFont(EMOJI_FONT)
        f.setPixelSize(max(6, int(r.height() * 0.8)))
        p.save()
        p.setFont(f)
        p.setPen(Qt.black)
        p.drawText(r, Qt.AlignCenter, s)
        p.restore()
