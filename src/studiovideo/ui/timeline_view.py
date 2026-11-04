"""Timeline de Montage libre : 4 pistes vidéo (la 1 : le montage ; 2 à 4 : une autre vidéo chacune, montrée dans
la mise en page, `edit/compose.py`), musique et voix (doublage), tête de lecture, zoom (§8.2). Les textes et les
sous-titres se règlent dans l'onglet Textes."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QMenu, QScrollArea, QSlider, QVBoxLayout, QWidget

from ..edit import compose
from ..edit.model import Timeline
from ..media.ffmpeg import fmt_timecode
from . import theme
from .widgets import label, tc_html

RULER_H = 20
TRACK_H = 24
TRACK_GAP = 3
LABEL_W = 70
TRACKS = ("Vidéo 1", "Vidéo 2", "Vidéo 3", "Vidéo 4", "Musique", "Voix")
MUSIC, VOICE = 4, 5                     # numéros des pistes musique et voix


class TimelineCanvas(QWidget):
    seek = Signal(float)
    segment_selected = Signal(object)   # id | None
    text_selected = Signal(object)
    music_selected = Signal()
    cue_selected = Signal(str)
    edited = Signal(str)                # "segment" | "track" : modification par glisser terminée
    track_removed = Signal(int)         # piste vidéo 2 à 4 (1..3) vidée (clic droit)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tl = Timeline()
        self.names: dict[int, str] = {}
        self.music_name = ""
        self.pps = 40.0            # pixels par seconde
        self.playhead = 0.0
        self.sel_segment: str | None = None
        self.sel_text: str | None = None
        self.sel_track: int | None = None       # piste vidéo 2 à 4 sélectionnée (1..3)
        self.in_out: tuple[float | None, float | None] = (None, None)
        self._drag_head = False
        self.durations: dict = {}         # asset_id -> durée du média (limite des poignées)
        self._drag: dict | None = None    # poignée en cours : {"kind", "id", "edge", "t0", valeurs d'origine}
        self.peaks: dict = {}             # asset_id -> pics (forme d'onde), voir media/waveform.py
        self._cols: dict = {}             # cache des colonnes dessinées
        self.voice_lines: list[tuple[float, float, str]] = []   # phrases doublées (temps du montage)
        self.voice_name = ""                                    # « Voix doublée · anglais » ; vide : voix d'origine
        self.setMinimumHeight(RULER_H + len(TRACKS) * (TRACK_H + TRACK_GAP) + 8)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setMouseTracking(True)

    # -- données -----------------------------------------------------------
    def set_timeline(self, tl: Timeline, names: dict[int, str], music_name: str = "") -> None:
        self.tl, self.names, self.music_name = tl, names, music_name
        self._resize()
        self.update()

    def set_peaks(self, asset_id: int, peaks) -> None:
        self.peaks[asset_id] = peaks
        self._cols.clear()
        self.update()

    def _wave(self, s, width: int):
        pk = self.peaks.get(s.asset_id)
        if pk is None or width < 2:
            return None
        key = (s.asset_id, round(s.src_in, 3), round(s.src_out, 3), width)
        if key not in self._cols:
            from ..media.waveform import columns
            if len(self._cols) > 400:
                self._cols.clear()
            self._cols[key] = columns(pk, s.src_in, s.src_out, width)
        return self._cols[key]

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
            cols = None if s.muted else self._wave(s, int(rect.width()))
            if cols is not None:
                wc = QColor(c["text"])
                wc.setAlphaF(0.35)
                p.setPen(QPen(wc, 1))
                base, hh = rect.bottom() - 2, rect.height() * 0.62
                x0 = rect.left()
                for j in range(0, len(cols), 1 if len(cols) < 3000 else 2):
                    v = float(cols[j])
                    if v > 0.02:
                        p.drawLine(QPointF(x0 + j, base), QPointF(x0 + j, base - v * hh))
            fi, fo = s.fades() if hasattr(s, "fades") else (0.0, 0.0)
            if fi or fo:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 0, 0, 110))
                if fi:
                    p.drawPolygon([rect.topLeft(), rect.bottomLeft(), QPointF(rect.left() + fi * self.pps, rect.top())])
                if fo:
                    p.drawPolygon([rect.topRight(), rect.bottomRight(),
                                   QPointF(rect.right() - fo * self.pps, rect.top())])
            p.setPen(QColor(c["text"]))
            txt = f"{k + 1}. {self.names.get(s.asset_id, '?')}" + ("  (muet)" if s.muted else "")
            p.drawText(rect.adjusted(6, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft,
                       p.fontMetrics().elidedText(txt, Qt.ElideRight, int(rect.width() - 10)))
            acc += s.duration
        # pistes vidéo 2 à 4 : une autre vidéo chacune, à partir de l'instant choisi
        for j, tr in enumerate(compose.norm(getattr(self.tl.video, "layout", None))["tracks"], start=1):
            if tr["asset"] is None or j >= MUSIC:
                continue
            rj = self.track_rect(j)
            rect = QRectF(self.x_of(tr["start"]) + 1, rj.top() + 2, max(4.0, self._track_len(tr) * self.pps - 2),
                          rj.height() - 4)
            sel = self.sel_track == j
            col = QColor(c["accent"])
            col.setAlphaF(0.5 if sel else 0.22)
            p.setBrush(col)
            p.setPen(QPen(QColor(c["text"] if sel else c["border"]), 1.5 if sel else 1))
            p.drawRoundedRect(rect, 5, 5)
            p.setPen(QColor(c["text"]))
            p.drawText(rect.adjusted(6, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft,
                       p.fontMetrics().elidedText(self.names.get(tr["asset"], "?"), Qt.ElideRight,
                                                  int(rect.width() - 10)))
        # musique
        r2 = self.track_rect(MUSIC)
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
        # voix : les phrases de la voix doublée (version traduite), sinon la voix d'origine de la vidéo
        r4 = self.track_rect(VOICE)
        if self.voice_name:
            for a, b, txt in self.voice_lines:
                rect = QRectF(self.x_of(a) + 0.5, r4.top() + 5, max(2.0, (b - a) * self.pps - 1), r4.height() - 10)
                col = QColor(c["accent"])
                col.setAlphaF(0.4)
                p.setBrush(col)
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(rect, 3, 3)
                if rect.width() > 24:
                    p.setPen(QColor(c["text"]))
                    p.drawText(rect.adjusted(3, 0, -2, 0), Qt.AlignVCenter,
                               p.fontMetrics().elidedText(txt, Qt.ElideRight, int(rect.width() - 5)))
            if not self.voice_lines:
                p.setPen(QColor(c["faint"]))
                p.drawText(r4.adjusted(10, 0, 0, 0), Qt.AlignVCenter, self.voice_name)
        elif self.tl.segments:
            p.setPen(QColor(c["faint"]))
            p.drawText(r4.adjusted(10, 0, 0, 0), Qt.AlignVCenter, "Voix d'origine de la vidéo")
        if not self.tl.segments:
            p.setPen(QColor(c["faint"]))
            p.drawText(r0.adjusted(10, 0, 0, 0), Qt.AlignVCenter,
                       "Importez une vidéo : elle se place ici, les suivantes sur les pistes 2 à 4.")
        # tête de lecture
        x = self.x_of(self.playhead)
        p.setPen(QPen(QColor(c["danger"]), 1.5))
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.setBrush(QColor(c["danger"]))
        p.drawPolygon([QPointF(x - 5, 0), QPointF(x + 5, 0), QPointF(x, 7)])
        p.end()

    # -- souris -------------------------------------------------------------
    HANDLE_PX = 6

    def handle_at(self, pos) -> dict | None:
        """Poignée sous le pointeur : bord d'un extrait (rogner) ou texte (déplacer / bords)."""
        x, hp = pos.x(), self.HANDLE_PX
        if self.track_rect(0).contains(pos):
            acc = 0.0
            for i, s in enumerate(self.tl.segments):
                xa, xb = self.x_of(acc), self.x_of(acc + s.duration)
                if xa <= x <= xa + hp and (i == 0 or x > xa):
                    return {"kind": "segment", "id": s.id, "edge": "in"}
                if xb - hp <= x <= xb:
                    return {"kind": "segment", "id": s.id, "edge": "out"}
                acc += s.duration
        j = self.video_track_at(pos)
        if j:
            tr = compose.norm(getattr(self.tl.video, "layout", None))["tracks"][j - 1]
            if self.x_of(tr["start"]) <= x <= self.x_of(tr["start"] + self._track_len(tr)):
                return {"kind": "track", "id": j, "edge": "move"}
        return None

    def _track_len(self, tr: dict) -> float:
        return float(self.durations.get(tr["asset"]) or 0) or max(1.0, self.tl.duration - tr["start"])

    def video_track_at(self, pos) -> int | None:
        """Piste vidéo 2 à 4 (1..3) sous le pointeur, si elle porte une vidéo."""
        tracks = compose.norm(getattr(self.tl.video, "layout", None))["tracks"]
        for j in range(1, MUSIC):
            if self.track_rect(j).contains(pos) and j <= len(tracks) and tracks[j - 1]["asset"] is not None:
                return j
        return None

    def mousePressEvent(self, e) -> None:
        pos = e.position()
        t = self.t_of(pos.x())
        h = self.handle_at(pos) if e.button() == Qt.LeftButton and pos.y() >= RULER_H else None
        if h:
            h["t0"] = t
            if h["kind"] == "segment":
                s = self.tl.segments[self.tl.index_of(h["id"])]
                h["orig"] = (s.src_in, s.src_out)
                self.sel_segment, self.sel_track = s.id, None
                self.segment_selected.emit(s.id)
            else:
                tr = compose.norm(self.tl.video.layout)["tracks"][h["id"] - 1]
                h["orig"] = (tr["start"], tr["start"])
                self.sel_track, self.sel_segment = h["id"], None
            h["changed"] = False
            self._drag = h
            self.update()
            return
        if e.button() == Qt.RightButton and self.video_track_at(pos):
            j = self.video_track_at(pos)
            m = QMenu(self)
            m.addAction("Retirer cette vidéo de la piste", lambda: self.track_removed.emit(j))
            m.exec(e.globalPosition().toPoint())
            return
        if pos.y() < RULER_H or e.button() != Qt.LeftButton:
            self._drag_head = True
            self.seek.emit(t)
            return
        if self.track_rect(0).contains(pos):
            loc = self.tl.locate(t)
            seg = self.tl.segments[loc[0]].id if loc and t <= self.tl.duration else None
            self.sel_segment, self.sel_track = seg, None
            self.segment_selected.emit(seg)
        elif self.track_rect(MUSIC).contains(pos) and self.tl.music.asset_id:
            self.music_selected.emit()
        self._drag_head = True
        self.seek.emit(min(t, self.tl.duration))
        self.update()

    def mouseMoveEvent(self, e) -> None:
        pos = e.position()
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self._drag_to(self.t_of(pos.x()) - self._drag["t0"])
            return
        if self._drag_head and e.buttons() & Qt.LeftButton:
            self.seek.emit(min(self.t_of(pos.x()), self.tl.duration))
            return
        h = self.handle_at(pos)
        self.setCursor(Qt.SizeHorCursor if h and h["edge"] != "move" else
                       Qt.SizeAllCursor if h else Qt.ArrowCursor)

    def _drag_to(self, dt: float) -> None:
        d = self._drag
        a, b = d["orig"]
        if d["kind"] == "segment":
            from ..edit.model import MIN_SEGMENT
            seg = self.tl.segments[self.tl.index_of(d["id"])]
            dur = self.durations.get(seg.asset_id) or None
            if d["edge"] == "in":
                self.tl.trim(d["id"], src_in=min(max(0.0, a + dt), seg.src_out - MIN_SEGMENT), source_duration=dur)
            else:
                self.tl.trim(d["id"], src_out=max(b + dt, seg.src_in + MIN_SEGMENT), source_duration=dur)
        else:                               # vidéo d'une piste 2 à 4 : glissée dans le temps
            lay = compose.norm(self.tl.video.layout)
            lay["tracks"][d["id"] - 1]["start"] = round(max(0.0, a + dt), 2)
            self.tl.video.layout = lay
        d["changed"] = True
        self._resize()
        self.update()

    def mouseReleaseEvent(self, _e) -> None:
        self._drag_head = False
        if self._drag is not None:
            d, self._drag = self._drag, None
            if d["changed"]:
                self.edited.emit(d["kind"])

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
        self.info.setTextFormat(Qt.RichText)
        head.addWidget(self.info)
        head.addStretch(1)
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
        self.info.setText(f"{len(tl.segments)} extrait(s) · durée {tc_html(tl.duration)} · "
                          f"{tl.format.aspect} {tl.format.width}×{tl.format.height}")

    def fit(self) -> None:
        d = max(5.0, self.canvas.tl.duration + 2)
        pps = (self.scroll.viewport().width() - LABEL_W - 10) / d
        self.zoom.setValue(int(max(2, min(300, pps))))
