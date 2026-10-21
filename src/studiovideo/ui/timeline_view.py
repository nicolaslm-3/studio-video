"""Timeline : pistes vidéo, textes et musique, tête de lecture, zoom (§8.2)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QScrollArea, QSlider, QVBoxLayout, QWidget

from ..edit.model import Timeline
from ..media.ffmpeg import fmt_timecode
from . import theme
from .widgets import label

RULER_H = 22
TRACK_H = 40
TRACK_GAP = 6
LABEL_W = 70
TRACKS = ("Vidéo", "Textes", "Musique")


class TimelineCanvas(QWidget):
    seek = Signal(float)
    segment_selected = Signal(object)   # id | None
    text_selected = Signal(object)
    music_selected = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tl = Timeline()
        self.names: dict[int, str] = {}
        self.music_name = ""
        self.pps = 40.0            # pixels par seconde
        self.playhead = 0.0
        self.sel_segment: str | None = None
        self.sel_text: str | None = None
        self.in_out: tuple[float | None, float | None] = (None, None)
        self._drag_head = False
        self.setMinimumHeight(RULER_H + 3 * (TRACK_H + TRACK_GAP) + 8)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setMouseTracking(True)

    # -- données -----------------------------------------------------------
    def set_timeline(self, tl: Timeline, names: dict[int, str], music_name: str = "") -> None:
        self.tl, self.names, self.music_name = tl, names, music_name
        self._resize()
        self.update()

    def set_playhead(self, t: float) -> None:
        self.playhead = max(0.0, t)
        self.update()

    def set_zoom(self, pps: float) -> None:
        self.pps = max(2.0, min(400.0, pps))
        self._resize()
        self.update()

    def _resize(self) -> None:
        self.setMinimumWidth(int(LABEL_W + max(10.0, self.tl.duration + 5) * self.pps))

    def x_of(self, t: float) -> float:
        return LABEL_W + t * self.pps

    def t_of(self, x: float) -> float:
        return max(0.0, (x - LABEL_W) / self.pps)

    def track_rect(self, i: int) -> QRectF:
        return QRectF(LABEL_W, RULER_H + 4 + i * (TRACK_H + TRACK_GAP), self.width() - LABEL_W, TRACK_H)

    # -- dessin -------------------------------------------------------------
    def paintEvent(self, _e) -> None:
        c = theme.current
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(c["surface"]))
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, self.font().pointSizeF() - 1.5))
        p.setFont(small)
        # règle
        step = next(s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600) if s * self.pps >= 60)
        minor = step / 5 if step >= 5 else step / 2
        p.setPen(QColor(c["faint"]))
        k = 0
        while self.x_of(k * minor) < self.width():
            t = k * minor
            x = self.x_of(t)
            major = abs(t / step - round(t / step)) < 1e-6
            p.drawLine(QPointF(x, RULER_H - (7 if major else 3)), QPointF(x, RULER_H))
            if major:
                p.drawText(QPointF(x + 3, RULER_H - 8), fmt_timecode(t))
            k += 1
        # pistes
        for i, name in enumerate(TRACKS):
            r = self.track_rect(i)
            p.fillRect(QRectF(0, r.top(), self.width(), r.height()), QColor(c["bg"]))
            p.setPen(QColor(c["muted"]))
            p.drawText(QRectF(8, r.top(), LABEL_W - 10, r.height()), Qt.AlignVCenter, name)
        # segments
        r0 = self.track_rect(0)
        acc = 0.0
        for k, s in enumerate(self.tl.segments):
            rect = QRectF(self.x_of(acc) + 1, r0.top() + 2, max(2.0, s.duration * self.pps - 2), r0.height() - 4)
            sel = s.id == self.sel_segment
            col = QColor(c["accent"])
            col.setAlphaF(0.55 if sel else 0.28)
            p.setBrush(col)
            p.setPen(QPen(QColor(c["text"] if sel else c["border"]), 1.5 if sel else 1))
            p.drawRoundedRect(rect, 5, 5)
            p.setPen(QColor(c["text"]))
            txt = f"{k + 1}. {self.names.get(s.asset_id, '?')}" + ("  (muet)" if s.muted else "")
            p.drawText(rect.adjusted(6, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft,
                       p.fontMetrics().elidedText(txt, Qt.ElideRight, int(rect.width() - 10)))
            acc += s.duration
        # textes
        r1 = self.track_rect(1)
        for item in self.tl.texts:
            rect = QRectF(self.x_of(item.start) + 1, r1.top() + 4, max(4.0, (item.end - item.start) * self.pps - 2),
                          r1.height() - 8)
            sel = item.id == self.sel_text
            col = QColor(c["warn"])
            col.setAlphaF(0.55 if sel else 0.3)
            p.setBrush(col)
            p.setPen(QPen(QColor(c["text"] if sel else c["border"]), 1.5 if sel else 1))
            p.drawRoundedRect(rect, 4, 4)
            p.setPen(QColor(c["text"]))
            p.drawText(rect.adjusted(5, 0, -3, 0), Qt.AlignVCenter,
                       p.fontMetrics().elidedText(item.text.replace("\n", " "), Qt.ElideRight, int(rect.width() - 8)))
        # musique
        r2 = self.track_rect(2)
        if self.tl.music.asset_id:
            rect = QRectF(self.x_of(self.tl.music.start) + 1, r2.top() + 4,
                          max(4.0, (self.tl.duration - self.tl.music.start) * self.pps - 2), r2.height() - 8)
            col = QColor(c["ok"])
            col.setAlphaF(0.3)
            p.setBrush(col)
            p.setPen(QPen(QColor(c["border"])))
            p.drawRoundedRect(rect, 4, 4)
            p.setPen(QColor(c["text"]))
            p.drawText(rect.adjusted(5, 0, -3, 0), Qt.AlignVCenter,
                       f"♪ {self.music_name}  {self.tl.music.volume_db:+.0f} dB" + ("  · atténuée sous la voix"
                                                                                   if self.tl.music.duck else ""))
        if not self.tl.segments:
            p.setPen(QColor(c["faint"]))
            p.drawText(r0.adjusted(10, 0, 0, 0), Qt.AlignVCenter,
                       "Ajoutez un extrait : bouton « Ajouter à la timeline » ou double-clic sur un clip.")
        # tête de lecture
        x = self.x_of(self.playhead)
        p.setPen(QPen(QColor(c["danger"]), 1.5))
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.setBrush(QColor(c["danger"]))
        p.drawPolygon([QPointF(x - 5, 0), QPointF(x + 5, 0), QPointF(x, 7)])
        p.end()

    # -- souris -------------------------------------------------------------
    def mousePressEvent(self, e) -> None:
        pos = e.position()
        t = self.t_of(pos.x())
        if pos.y() < RULER_H or e.button() != Qt.LeftButton:
            self._drag_head = True
            self.seek.emit(t)
            return
        if self.track_rect(0).contains(pos):
            loc = self.tl.locate(t)
            seg = self.tl.segments[loc[0]].id if loc and t <= self.tl.duration else None
            self.sel_segment, self.sel_text = seg, None
            self.segment_selected.emit(seg)
        elif self.track_rect(1).contains(pos):
            hit = next((x for x in reversed(self.tl.texts) if x.start <= t <= x.end), None)
            self.sel_text = hit.id if hit else None
            self.sel_segment = None
            self.text_selected.emit(self.sel_text)
        elif self.track_rect(2).contains(pos) and self.tl.music.asset_id:
            self.music_selected.emit()
        self._drag_head = True
        self.seek.emit(min(t, self.tl.duration))
        self.update()

    def mouseMoveEvent(self, e) -> None:
        if self._drag_head and e.buttons() & Qt.LeftButton:
            self.seek.emit(min(self.t_of(e.position().x()), self.tl.duration))

    def mouseReleaseEvent(self, _e) -> None:
        self._drag_head = False

    def wheelEvent(self, e) -> None:
        if e.modifiers() & Qt.ControlModifier:
            self.set_zoom(self.pps * (1.15 if e.angleDelta().y() > 0 else 1 / 1.15))
            e.accept()
        else:
            super().wheelEvent(e)


class TimelineView(QWidget):
    """Timeline avec barre de défilement et réglage du zoom temporel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        head = QHBoxLayout()
        self.info = label("", "faint")
        head.addWidget(self.info)
        head.addStretch(1)
        head.addWidget(label("Zoom", "faint"))
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(2, 300)
        self.zoom.setValue(40)
        self.zoom.setFixedWidth(140)
        self.zoom.setToolTip("Zoom temporel (Ctrl + molette sur la timeline)")
        head.addWidget(self.zoom)
        lay.addLayout(head)
        self.canvas = TimelineCanvas()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.canvas)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(self.canvas.minimumHeight() + 16)
        lay.addWidget(self.scroll)
        self.zoom.valueChanged.connect(lambda v: self.canvas.set_zoom(float(v)))

    def set_timeline(self, tl: Timeline, names: dict[int, str], music_name: str = "") -> None:
        self.canvas.set_timeline(tl, names, music_name)
        self.info.setText(f"{len(tl.segments)} extrait(s) · durée {fmt_timecode(tl.duration, True)} · "
                          f"{tl.format.aspect} {tl.format.width}×{tl.format.height}")

    def fit(self) -> None:
        d = max(5.0, self.canvas.tl.duration + 2)
        pps = (self.scroll.viewport().width() - LABEL_W - 10) / d
        self.zoom.setValue(int(max(2, min(300, pps))))
