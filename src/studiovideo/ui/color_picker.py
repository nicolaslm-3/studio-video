"""Choix de couleur intégré : un panneau flottant sous la pastille (carré saturation / luminosité, barre de teinte,
code hexadécimal, couleurs proposées et récentes), aux couleurs du thème. Remplace la fenêtre QColorDialog.

Le changement s'applique en direct ; Échap revient à la couleur de départ, un clic à l'extérieur valide."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QApplication, QGridLayout, QHBoxLayout, QLineEdit, QPushButton, QToolButton,
                               QWidget)

from . import theme
from .shell import Popover

PRESETS = ["#FFFFFF", "#000000", "#FFD54A", "#FF3B30", "#FF9500", "#34C759", "#00C7BE", "#007AFF", "#AF52DE",
           "#FF2D55", "#8E8E93", "#1C1C1E"]
_recent: list[str] = []                     # couleurs choisies pendant la session (les plus récentes d'abord)


def _hsv(c: QColor, hue: float) -> tuple[float, float, float]:
    h, s, v, _a = c.getHsvF()
    return (hue if h < 0 else h), s, v      # gris : teinte indéfinie, on garde la précédente


class SVSquare(QWidget):
    """Saturation (horizontal) et luminosité (vertical) pour la teinte courante."""
    changed = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(232, 156)
        self.setCursor(Qt.CrossCursor)
        self.h, self.s, self.v = 0.0, 1.0, 1.0

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(r, 8, 8)
        p.setClipPath(clip)
        p.fillRect(r, QColor.fromHsvF(self.h, 1, 1))
        g = QLinearGradient(r.topLeft(), r.topRight())
        g.setColorAt(0, QColor(255, 255, 255))
        g.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(r, g)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, QColor(0, 0, 0, 0))
        g.setColorAt(1, QColor(0, 0, 0))
        p.fillRect(r, g)
        p.setClipping(False)
        c = QPointF(r.left() + self.s * r.width(), r.top() + (1 - self.v) * r.height())
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 110), 3))
        p.drawEllipse(c, 7, 7)
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawEllipse(c, 7, 7)
        p.end()

    def _at(self, pos) -> None:
        self.s = min(1.0, max(0.0, pos.x() / self.width()))
        self.v = min(1.0, max(0.0, 1 - pos.y() / self.height()))
        self.update()
        self.changed.emit(self.s, self.v)

    def mousePressEvent(self, e) -> None:
        self._at(e.position())

    def mouseMoveEvent(self, e) -> None:
        if e.buttons() & Qt.LeftButton:
            self._at(e.position())


class HueBar(QWidget):
    changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(232, 14)
        self.setCursor(Qt.PointingHandCursor)
        self.h = 0.0

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 2.5, -0.5, -2.5)
        g = QLinearGradient(r.topLeft(), r.topRight())
        for i in range(7):
            g.setColorAt(i / 6, QColor.fromHsvF((i / 6) % 1.0, 1, 1))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(r, 4.5, 4.5)
        x = r.left() + self.h * r.width()
        p.setPen(QPen(QColor(0, 0, 0, 110), 3))
        p.setBrush(QColor.fromHsvF(self.h, 1, 1))
        p.drawEllipse(QPointF(x, self.height() / 2), 6, 6)
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawEllipse(QPointF(x, self.height() / 2), 6, 6)
        p.end()

    def _at(self, pos) -> None:
        self.h = min(0.999, max(0.0, pos.x() / self.width()))
        self.update()
        self.changed.emit(self.h)

    def mousePressEvent(self, e) -> None:
        self._at(e.position())

    def mouseMoveEvent(self, e) -> None:
        if e.buttons() & Qt.LeftButton:
            self._at(e.position())


def _swatch(col: str, size: int = 18) -> QToolButton:
    b = QToolButton()
    b.setFixedSize(size, size)
    b.setCursor(Qt.PointingHandCursor)
    b.setToolTip(col.upper())
    b.setStyleSheet(f"QToolButton {{ background: {col}; border: 1px solid rgba(128,128,128,.55); "
                    f"border-radius: {size // 2}px; }} QToolButton:hover {{ border: 2px solid {theme.color('text')}; }}")
    return b


class ColorPopover(Popover):
    """Panneau de couleur ; `changed` est émis en direct, `finished` à la fermeture (couleur retenue)."""
    changed = Signal(str)
    finished = Signal(str)

    def __init__(self, value: str, parent=None):
        super().__init__(parent)
        self.start = QColor(value) if QColor(value).isValid() else QColor("#FFFFFF")
        self.color = QColor(self.start)
        self._cancelled = False
        self.sv = SVSquare()
        self.hue = HueBar()
        h, s, v = _hsv(self.color, 0.0)
        self.sv.h, self.sv.s, self.sv.v, self.hue.h = h, s, v, h
        self.sv.changed.connect(lambda s_, v_: self._set(QColor.fromHsvF(self.hue.h, s_, v_), hsv=True))
        self.hue.changed.connect(self._hue)
        self.lay.addWidget(self.sv)
        self.lay.addWidget(self.hue)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.before = QPushButton()
        self.before.setFixedSize(28, 28)
        self.before.setToolTip("Couleur de départ (clic pour y revenir)")
        self.before.setCursor(Qt.PointingHandCursor)
        self.before.clicked.connect(lambda: self._set(QColor(self.start)))
        self.after = QWidget()
        self.after.setFixedSize(28, 28)
        row.addWidget(self.before)
        row.addWidget(self.after)
        self.hex = QLineEdit()
        self.hex.setMaxLength(7)
        self.hex.setToolTip("Code de la couleur (ex. #FFD54A)")
        self.hex.editingFinished.connect(self._hex_edited)
        self.hex.textEdited.connect(self._hex_typed)
        row.addWidget(self.hex, 1)
        self.lay.addLayout(row)
        self.lay.addWidget(self._grid(PRESETS))
        if _recent:
            self.lay.addWidget(self._grid(_recent[:12]))
        self.before.setStyleSheet(f"background: {self.start.name()}; border: 1px solid rgba(128,128,128,.55); "
                                  "border-radius: 6px;")
        self._refresh()

    def _grid(self, colors: list[str]) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 2, 0, 0)
        g.setHorizontalSpacing(2)
        g.setVerticalSpacing(4)
        for i, col in enumerate(colors):
            b = _swatch(col)
            b.clicked.connect(lambda _=False, c=col: self._set(QColor(c)))
            g.addWidget(b, 0, i)
        return w

    def _hue(self, h: float) -> None:
        self.sv.h = h
        self.sv.update()
        self._set(QColor.fromHsvF(h, self.sv.s, self.sv.v), hsv=True)

    def _hex_typed(self, t: str) -> None:
        t = t.strip()
        if not t.startswith("#"):
            t = "#" + t
        if len(t) == 7 and QColor.isValidColorName(t):
            self._set(QColor(t), from_text=True)

    def _hex_edited(self) -> None:
        self.hex.setText(self.color.name().upper())

    def _set(self, c: QColor, hsv: bool = False, from_text: bool = False) -> None:
        if not c.isValid():
            return
        self.color = c
        if not hsv:
            h, s, v = _hsv(c, self.hue.h)
            self.sv.h, self.sv.s, self.sv.v, self.hue.h = h, s, v, h
            self.sv.update()
            self.hue.update()
        self._refresh(keep_text=from_text)
        self.changed.emit(c.name().upper())

    def _refresh(self, keep_text: bool = False) -> None:
        self.after.setStyleSheet(f"background: {self.color.name()}; border: 1px solid rgba(128,128,128,.55); "
                                 "border-radius: 6px;")
        if not keep_text:
            self.hex.setText(self.color.name().upper())

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self._cancelled = True
            self.color = QColor(self.start)
            self.changed.emit(self.start.name().upper())
            self.hide()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._hex_typed(self.hex.text())
            self.hide()
        else:
            super().keyPressEvent(e)

    def hideEvent(self, e) -> None:
        name = self.color.name().upper()
        if not self._cancelled and name != self.start.name().upper():
            if name in _recent:
                _recent.remove(name)
            _recent.insert(0, name)
            del _recent[12:]
        self.finished.emit(name)
        super().hideEvent(e)

    def show_below(self, anchor: QWidget) -> None:
        self.adjustSize()
        g = anchor.mapToGlobal(QPoint(0, anchor.height()))
        x, y = g.x() - 14, g.y() - 6
        scr = QApplication.screenAt(g) or QApplication.primaryScreen()
        if scr:
            r = scr.availableGeometry()
            if y + self.height() > r.bottom():                      # pas la place en dessous : au-dessus
                y = anchor.mapToGlobal(QPoint(0, 0)).y() - self.height() + 6
            x = max(r.left() + 4, min(x, r.right() - self.width() - 4))
            y = max(r.top() + 4, y)
        self.move(x, y)
        self.show()
        self.hex.setFocus()
        self.hex.selectAll()


class ColorButton(QPushButton):
    """Pastille de couleur ; un clic ouvre le panneau de couleur. `picked` est émis à chaque changement."""
    picked = Signal(str)

    def __init__(self, value: str = "#FFFFFF", size: tuple[int, int] = (24, 24)):      # carré
        super().__init__()
        self.setFixedSize(*size)
        self.setCursor(Qt.PointingHandCursor)
        self.pop: ColorPopover | None = None
        self.set(value)
        self.clicked.connect(self._pick)

    def set(self, v: str) -> None:
        self.value = v
        self.setStyleSheet(f"QPushButton {{ background: {v}; border: 1px solid rgba(128,128,128,.6); "
                           "border-radius: 6px; }")

    set_value = set

    def _pick(self) -> None:
        # parent : la fenêtre (et non la pastille), sinon le panneau hériterait du fond coloré de la pastille
        self.pop = ColorPopover(self.value, self.window())
        self.pop.changed.connect(self._changed)
        self.pop.show_below(self)

    def _changed(self, v: str) -> None:
        if v != self.value:
            self.set(v)
            self.picked.emit(v)
