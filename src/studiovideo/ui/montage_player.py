"""Lecteur du montage avec aperçu en direct du cadrage (§8.1, §8.3, §9.5).

Le lecteur enchaîne les segments de la timeline. L'image est affichée dans le
format de sortie (9:16, 1:1…) avec le recadrage, le zoom, le miroir et les textes
appliqués en direct. Les corrections de couleur et le fond flou ne sont visibles
que dans l'« Aperçu fidèle », qui est une image réellement calculée par FFmpeg.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QRectF, QSize, QSizeF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QBrush, QColor, QGuiApplication, QKeySequence, QPainter, QPainterPath, QPen, QPixmap,
                           QShortcut, QTransform)
from PySide6.QtWidgets import (QCheckBox, QFrame, QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsView, QHBoxLayout, QMenu, QSlider, QStackedLayout,
                               QToolButton, QVBoxLayout, QWidget)

from ..edit import compose
from ..edit.model import Timeline
from ..edit.subrender import render_state, strip_geometry
from ..edit.subtitles import State, states
from ..edit.textrender import render_text_image
from . import theme
from .widgets import PlayButton, button, label, tc_html, tool

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
    from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
    MULTIMEDIA = True
except ImportError:  # pragma: no cover
    MULTIMEDIA = False

# Zones occupées par l'interface des applications (fractions de l'image 9:16), configurables ici.
SAFE_ZONES = {
    "TikTok": [(0.0, 0.0, 1.0, 0.09), (0.85, 0.35, 0.15, 0.5), (0.0, 0.78, 0.82, 0.22)],
    "Reels / Shorts": [(0.0, 0.0, 1.0, 0.08), (0.86, 0.45, 0.14, 0.4), (0.0, 0.8, 0.84, 0.2)],
}


def reframe_offset(tl: Timeline, seg, src_t: float, sw: int, sh: int) -> float | None:
    """Décalage horizontal (-1..1) équivalent au recadrage automatique à l'instant source src_t."""
    keys = getattr(seg, "reframe", None) if seg is not None else None
    if not keys or tl.format.fit != "fill" or tl.video.rotation % 360 not in (0, 180):
        return None
    from ..edit.reframe import cx_at
    W, H = tl.format.width, tl.format.height
    s = max(W / sw, H / sh) * max(1.0, tl.video.zoom) * max(1.0, getattr(seg, "zoom", 1.0) or 1.0)
    w = sw * s
    if w <= W + 1:
        return None
    c = cx_at(keys, src_t)
    if tl.video.mirror:
        c = 1 - c
    left = min(max(0.0, c * w - W / 2), w - W)
    return max(-1.0, min(1.0, left / ((w - W) / 2) - 1))


def display_rect(tl: Timeline, sw: int, sh: int, ox_override: float | None = None,
                 seg_zoom: float = 1.0, pan: bool = False) -> tuple[float, float, float, float]:
    """Position (x, y, w, h) de l'image source (déjà tournée) dans l'image de sortie ; `pan` : mouvement subtil
    en cours (image un peu agrandie pour glisser)."""
    W, H = tl.format.width, tl.format.height
    v = tl.video
    zoom = max(1.0, v.zoom) * max(1.0, seg_zoom)
    if pan and tl.format.fit == "fill":
        from ..edit.render import pan_zoom
        zoom *= pan_zoom(tl)
    ox, oy = max(-1.0, min(1.0, v.offset_x)), max(-1.0, min(1.0, v.offset_y))
    if ox_override is not None:
        ox = ox_override
    if tl.format.fit == "fill":
        s = max(W / sw, H / sh) * zoom
    else:
        s = min(W / sw, H / sh) * zoom
    w, h = sw * s, sh * s
    if tl.format.fit == "fill" or w > W or h > H:
        x = -(w - W) / 2 * (1 + ox) if w > W else (W - w) / 2 * (1 + ox)
        y = -(h - H) / 2 * (1 + oy) if h > H else (H - h) / 2 * (1 + oy)
    else:
        x, y = (W - w) / 2 * (1 + ox), (H - h) / 2 * (1 + oy)
    return x, y, w, h


# filtres approchés dans l'aperçu (l'image exacte est celle de l'export) : teinte, force à pleine intensité
LOOK_TINTS = {"chaud": ("#FF9A3C", 0.22), "froid": ("#3C8CFF", 0.22), "cinema": ("#2E8C8C", 0.15),
              "vintage": ("#B38B59", 0.35), "nb": ("#808080", 1.0), "vif": None, "doux": None}


class _Frame(QGraphicsPathItem):
    """Cadre de l'image, aux coins arrondis (comme les aperçus de l'onglet Export) ; découpe son contenu."""

    def setRect(self, x: float, y: float, w: float, h: float) -> None:      # noqa: N802 — comme QGraphicsRectItem
        path = QPainterPath()
        r = min(w, h) * 0.035
        path.addRoundedRect(QRectF(x, y, w, h), r, r)
        self.setPath(path)


class _View:
    """Une vue d'une mise en page : cadre extérieur (contour), cadre intérieur qui découpe, image."""

    def __init__(self, parent, z: int):
        self.outer = QGraphicsRectItem(parent)
        self.outer.setPen(Qt.NoPen)
        self.outer.setZValue(z)
        self.inner = QGraphicsRectItem(self.outer)
        self.inner.setPen(Qt.NoPen)
        self.inner.setBrush(QColor("#000000"))
        self.inner.setFlag(QGraphicsRectItem.ItemClipsChildrenToShape, True)
        self.pix = QGraphicsPixmapItem(self.inner)
        self.pix.setTransformationMode(Qt.SmoothTransformation)
        self.outer.setVisible(False)
        self.rect = (0, 0, 0, 0)            # zone intérieure (x, y, w, h) en pixels de sortie
        self.src = (0, 0)                   # taille logique de la source (celle du rendu FFmpeg)


class LivePreview(QGraphicsView):
    layout_edited = Signal()                # vue déplacée ou zoomée à la souris
    clicked = Signal()                      # clic sur l'aperçu (lecteur compact : lecture / pause)
    frame_moved = Signal(float, float)      # bords gauche et droit de l'image dans l'aperçu (barre de lecture)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.click_toggles = False
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # autour de l'image : le fond de la page (pas de bandes noires), image posée sur la barre de lecture
        self.setBackgroundBrush(Qt.NoBrush)
        self.setStyleSheet("QGraphicsView { background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)
        self.viewport().setStyleSheet("background: transparent;")
        self.setAlignment(Qt.AlignHCenter | Qt.AlignBottom)
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.frame = _Frame()
        self.frame.setBrush(QColor("#000000"))
        self.frame.setPen(Qt.NoPen)
        self.frame.setFlag(QGraphicsRectItem.ItemClipsChildrenToShape, True)
        self.scene_.addItem(self.frame)
        self.video_item = QGraphicsVideoItem(self.frame) if MULTIMEDIA else None
        if self.video_item is not None:
            self.video_item.setAspectRatioMode(Qt.IgnoreAspectRatio)
        self.placeholder = self.scene_.addText("")
        self.placeholder.setDefaultTextColor(QColor("#8E9198"))
        self.texts: dict[str, QGraphicsPixmapItem] = {}
        self._text_keys: dict[str, tuple] = {}
        self.zones: list[QGraphicsRectItem] = []
        self.fader = QGraphicsRectItem(self.frame)          # fondus au noir des extraits
        self.fader.setBrush(QColor("#000000"))
        self.fader.setPen(Qt.NoPen)
        self.fader.setZValue(5)
        self.fader.setOpacity(0.0)
        self.sub_item = QGraphicsPixmapItem(self.frame)
        self.sub_item.setZValue(9)
        self._sub_states: list = []
        self._sub_cache: dict = {}
        self._sub_key = None
        self._sub_shown = None
        self.zone_name: str | None = None
        self.tl = Timeline()
        self.src_size = None
        self.ox_override: float | None = None
        # mise en page à deux vues : les images décodées sont recadrées ici avec la géométrie de l'export
        self.lay = compose.norm(None)
        self.composite = False
        self.second_size: tuple[int, int] | None = None
        self._imgs: list = [None, None]
        self._drag = None
        self.views = [_View(self.frame, 1), _View(self.frame, 2)]
        self.sinks = []
        if MULTIMEDIA:
            for k in range(2):
                sk = QVideoSink(self)
                sk.videoFrameChanged.connect(lambda fr, k=k: self.set_frame(k, fr))
                self.sinks.append(sk)

    def set_ox(self, ox: float | None, panning: bool = False) -> None:
        """Recadrage automatique ou mouvement subtil : décalage horizontal à l'instant affiché (None = réglage
        manuel)."""
        if panning == getattr(self, "panning", False) and (ox == self.ox_override or (
                ox is not None and self.ox_override is not None and abs(ox - self.ox_override) < 0.002)):
            return
        self.ox_override, self.panning = ox, panning
        self._geometry()

    def set_fade(self, level: float) -> None:
        """Opacité de l'image (1 = normale, 0 = noir)."""
        self.fader.setOpacity(max(0.0, min(1.0, 1.0 - level)))

    def set_seg_zoom(self, z: float) -> None:
        """Zoom propre à l'extrait affiché."""
        z = max(1.0, float(z or 1.0))
        if abs(z - getattr(self, "seg_zoom", 1.0)) < 1e-4:
            return
        self.seg_zoom = z
        self._geometry()

    def apply(self, tl: Timeline, src_size: tuple[int, int] | None) -> None:
        self.tl = tl
        self.src_size = src_size
        W, H = tl.format.width, tl.format.height
        self.frame.setRect(0, 0, W, H)
        self.fader.setRect(0, 0, W, H)
        self.frame.setBrush(QColor("#1A1B1E") if tl.format.fit == "blur" else QColor("#000000"))
        self.scene_.setSceneRect(0, 0, W, H)
        self.lay = compose.norm(getattr(tl.video, "layout", None))
        self.composite = self.lay["kind"] != "single"
        if self.video_item is not None:
            self.video_item.setVisible(not self.composite)
        self.setCursor(Qt.OpenHandCursor if self.composite else Qt.ArrowCursor)
        self._place_views()
        self._geometry()
        self._sync_texts()
        self._sync_subs()
        self._draw_zones()
        self.fit()

    def _geometry(self) -> None:
        tl, src_size = self.tl, getattr(self, "src_size", None)
        if self.video_item is not None and src_size:
            sw, sh = src_size
            rot = tl.video.rotation % 360
            rw, rh = (sh, sw) if rot in (90, 270) else (sw, sh)
            x, y, w, h = display_rect(tl, rw, rh, getattr(self, "ox_override", None), getattr(self, "seg_zoom", 1.0),
                                      getattr(self, "panning", False))
            iw, ih = (h, w) if rot in (90, 270) else (w, h)
            self.video_item.setSize(QSizeF(iw, ih))
            t = QTransform()
            tilt = float(getattr(tl.video, "tilt", 0.0) or 0.0)
            if abs(tilt) > 1e-3:                  # légère rotation : autour du centre du cadre, image agrandie
                from ..edit.render import tilt_cover
                W, H = tl.format.width, tl.format.height
                c = tilt_cover(tl)
                t.translate(W / 2, H / 2)
                t.rotate(tilt)
                t.scale(c, c)
                t.translate(-W / 2, -H / 2)
            t.translate(x + w / 2, y + h / 2)
            t.scale(-1 if tl.video.mirror else 1, -1 if tl.video.flip else 1)
            t.rotate(rot)
            t.translate(-iw / 2, -ih / 2)
            self.video_item.setTransform(t)
            self.video_item.setPos(0, 0)
            self._look()

    def _look(self) -> None:
        """Filtre colorimétrique approché dans l'aperçu (teinte réglée par l'intensité)."""
        from PySide6.QtWidgets import QGraphicsColorizeEffect
        v = self.tl.video
        tint = LOOK_TINTS.get(getattr(v, "look", "") or "")
        amount = float(getattr(v, "look_amount", 0.0) or 0.0)
        key = (tint, round(amount, 3))
        if key == getattr(self, "_look_key", None):
            return
        self._look_key = key
        if not tint or amount <= 0:
            self.video_item.setGraphicsEffect(None)
            return
        fx = QGraphicsColorizeEffect()
        fx.setColor(QColor(tint[0]))
        fx.setStrength(tint[1] * amount)
        self.video_item.setGraphicsEffect(fx)

    # -- mise en page à deux vues ------------------------------------------------------
    def _view_source(self, i: int) -> int:
        """Image utilisée par la vue i : 0 = vidéo principale, 1 = seconde vidéo."""
        return 1 if i == 1 and compose.uses_second(self.lay) else 0

    def set_frame(self, k: int, frame) -> None:
        if not self.composite:
            return
        img = frame.toImage() if frame is not None and frame.isValid() else None
        if img is None or img.isNull():
            return
        rot = self.tl.video.rotation % 360
        if k == 0 and rot:
            img = img.transformed(QTransform().rotate(rot))
        self._imgs[k] = img
        pm = QPixmap.fromImage(img)
        for i, v in enumerate(self.views):
            if self._view_source(i) == k:
                v.pix.setPixmap(pm)
                self._place_view(i)

    def _place_views(self) -> None:
        for i, v in enumerate(self.views):
            v.outer.setVisible(self.composite)
            if self.composite:
                img = self._imgs[self._view_source(i)]
                if img is not None and v.pix.pixmap().isNull():
                    v.pix.setPixmap(QPixmap.fromImage(img))
                self._place_view(i)

    def _logical_size(self, i: int) -> tuple[int, int] | None:
        if self._view_source(i) == 1:
            return self.second_size
        if not self.src_size:
            return None
        sw, sh = self.src_size
        return (sh, sw) if self.tl.video.rotation % 360 in (90, 270) else (sw, sh)

    def _place_view(self, i: int) -> None:
        v = self.views[i]
        W, H = self.tl.format.width, self.tl.format.height
        zx, zy, zw, zh = compose.zones(self.lay, W, H)[i]
        b = int(self.lay.get("border") or 0) * W // 1080 if self.lay["kind"] == "pip" and i == 1 else 0
        v.outer.setRect(zx, zy, zw, zh)
        v.outer.setBrush(QColor(self.lay.get("border_color") or "#FFFFFF") if b else QColor("#000000"))
        x0, y0, w, h = zx + b, zy + b, zw - 2 * b, zh - 2 * b
        v.inner.setRect(x0, y0, w, h)
        v.rect = (x0, y0, w, h)
        pm = v.pix.pixmap()
        size = self._logical_size(i) or ((pm.width(), pm.height()) if not pm.isNull() else None)
        if not size or pm.isNull():
            return
        v.src = size
        SW, SH, x, y = compose.view_crop(size[0], size[1], w, h, self.lay["views"][i])
        mirror = self._view_source(i) == 0
        t = QTransform()
        t.translate(x0 + w / 2, y0 + h / 2)
        if mirror:
            t.scale(-1 if self.tl.video.mirror else 1, -1 if self.tl.video.flip else 1)
        t.translate(-w / 2 - x, -h / 2 - y)
        t.scale(SW / pm.width(), SH / pm.height())
        v.pix.setTransform(t)

    def _view_at(self, pos) -> int | None:
        order = (1, 0) if self.lay["kind"] == "pip" else (0, 1)
        for i in order:
            x, y, w, h = self.views[i].rect
            if x <= pos.x() <= x + w and y <= pos.y() <= y + h:
                return i
        return None

    def mousePressEvent(self, e) -> None:
        if self.click_toggles and e.button() == Qt.LeftButton:
            self.clicked.emit()
            return
        if not self.composite or e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        pos = self.mapToScene(e.position().toPoint())
        i = self._view_at(pos)
        if i is None or not self.views[i].src[0]:
            return
        v = self.views[i]
        x, y, w, h = v.rect
        _SW, _SH, cx0, cy0 = compose.view_crop(v.src[0], v.src[1], w, h, self.lay["views"][i])
        self._drag = (i, pos, cx0, cy0)
        self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e) -> None:
        if self._drag is None:
            return super().mouseMoveEvent(e)
        i, start, x0, y0 = self._drag
        pos = self.mapToScene(e.position().toPoint())
        dx, dy = pos.x() - start.x(), pos.y() - start.y()
        if self._view_source(i) == 0:
            dx, dy = (-dx if self.tl.video.mirror else dx), (-dy if self.tl.video.flip else dy)
        v = self.views[i]
        view = self.lay["views"][i]
        view["cx"], view["cy"] = compose.center_for(v.src[0], v.src[1], v.rect[2], v.rect[3], view, x0 - dx, y0 - dy)
        self.tl.video.layout = self.lay
        self._place_view(i)

    def mouseReleaseEvent(self, e) -> None:
        if self._drag is None:
            return super().mouseReleaseEvent(e)
        self._drag = None
        self.setCursor(Qt.OpenHandCursor)
        self.layout_edited.emit()

    def wheelEvent(self, e) -> None:
        if not self.composite:
            return super().wheelEvent(e)
        i = self._view_at(self.mapToScene(e.position().toPoint()))
        if i is None:
            return
        view = self.lay["views"][i]
        view["zoom"] = max(1.0, min(6.0, round(view["zoom"] * (1.08 if e.angleDelta().y() > 0 else 1 / 1.08), 3)))
        self.tl.video.layout = self.lay
        self._place_view(i)
        self.layout_edited.emit()

    def _sync_texts(self) -> None:
        W, H = self.tl.format.width, self.tl.format.height
        live = {t.id for t in self.tl.texts}
        for tid in list(self.texts):
            if tid not in live:
                self.scene_.removeItem(self.texts.pop(tid))
                self._text_keys.pop(tid, None)
        for item in self.tl.texts:
            key = (tuple(sorted((k, v) for k, v in item.__dict__.items() if k not in ("start", "end", "id"))), W, H)
            if self._text_keys.get(item.id) != key:
                pm = QPixmap.fromImage(render_text_image(item, W, H))
                if item.id in self.texts:
                    self.texts[item.id].setPixmap(pm)
                else:
                    gi = QGraphicsPixmapItem(pm, self.frame)
                    gi.setZValue(10)
                    self.texts[item.id] = gi
                self._text_keys[item.id] = key

    def _sync_subs(self) -> None:
        st = self.tl.subtitles
        key = (repr(st.to_dict()), self.tl.format.width, self.tl.format.height, round(self.tl.duration, 3))
        if key != self._sub_key:
            self._sub_key = key
            self._sub_cache.clear()
            self._sub_shown = None
            self._sub_states = states(st, self.tl.duration, self.tl.format.fps) if st.enabled and st.cues else []
        W, H = self.tl.format.width, self.tl.format.height
        self.sub_item.setPos(0, strip_geometry(st.style, W, H)[0])

    def _sub_state_at(self, t: float):
        for a, b, s_ in self._sub_states:
            if a <= t < b:
                return s_
        return None

    def set_time(self, t: float) -> None:
        st = self._sub_state_at(t) if self._sub_states else None
        if st is None or st.cue_index < 0 or not self.tl.subtitles.burn:
            self.sub_item.setVisible(False)
        else:
            if st != self._sub_shown:
                pm = self._sub_cache.get(st)
                if pm is None:
                    pm = QPixmap.fromImage(render_state(self.tl.subtitles, st, self.tl.format.width,
                                                        self.tl.format.height))
                    if len(self._sub_cache) > 400:
                        self._sub_cache.clear()
                    self._sub_cache[st] = pm
                self.sub_item.setPixmap(pm)
                self._sub_shown = st
            self.sub_item.setVisible(True)
        for item in self.tl.texts:
            gi = self.texts.get(item.id)
            if gi:
                gi.setVisible(item.start <= t <= item.end)

    def set_zone(self, name: str | None) -> None:
        self.zone_name = name
        self._draw_zones()

    def _draw_zones(self) -> None:
        for z in self.zones:
            self.scene_.removeItem(z)
        self.zones = []
        if not self.zone_name:
            return
        W, H = self.tl.format.width, self.tl.format.height
        for fx, fy, fw, fh in SAFE_ZONES[self.zone_name]:
            r = QGraphicsRectItem(fx * W, fy * H, fw * W, fh * H, self.frame)
            r.setBrush(QBrush(QColor(208, 122, 114, 70)))
            r.setPen(QPen(QColor(208, 122, 114, 160), 2, Qt.DashLine))
            r.setZValue(20)
            self.zones.append(r)

    def set_placeholder(self, text: str) -> None:
        self.placeholder.setPlainText(text)
        f = self.placeholder.font()
        f.setPixelSize(max(12, int(self.tl.format.height / 40)))
        self.placeholder.setFont(f)
        br = self.placeholder.boundingRect()
        self.placeholder.setPos((self.tl.format.width - br.width()) / 2, (self.tl.format.height - br.height()) / 2)
        self.placeholder.setZValue(30)

    def fit(self) -> None:
        self.fitInView(self.scene_.sceneRect(), Qt.KeepAspectRatio)
        r = self.mapFromScene(self.scene_.sceneRect()).boundingRect()
        self.frame_moved.emit(float(max(0, r.left())), float(max(0, self.width() - r.right() - 1)))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.fit()


def _gain(db: float) -> float:
    return 0.0 if db <= -59 else 10 ** (db / 20)


def audio_plan(tl: Timeline, tracks: dict | None) -> dict:
    """Ce que l'aperçu doit faire entendre pour un média (§10.2) — fonction pure, testable.

    tracks : {"dub": chemin | None, "voice": chemin | None, "music": chemin | None}.
    Retour : {"main": gain du son d'origine, "aux": [(chemin, gain), …] (2 pistes au plus),
              "note": explication courte}.
    """
    tracks = tracks or {}
    v = getattr(tl, "voice", None)
    if v is None:
        return {"main": 1.0, "aux": [], "note": ""}
    if v.mode == "dub":
        if not tracks.get("dub"):
            return {"main": 1.0, "aux": [], "note": "Pas de doublage pour ce média : voix d'origine."}
        aux = [(tracks["dub"], _gain(v.dub_gain_db))]
        if v.background == "ambience" and tracks.get("music"):
            aux.append((tracks["music"], _gain(v.ambience_db)))
            return {"main": 0.0, "aux": aux, "note": "Écoute : doublage + musique et ambiance séparées."}
        return {"main": _gain(v.original_level_db), "aux": aux, "note": "Écoute : doublage."}
    if v.isolate and tracks.get("voice"):
        return {"main": 0.0, "aux": [(tracks["voice"], 1.0)], "note": "Écoute : voix isolée."}
    return {"main": 1.0, "aux": [], "note": ""}


# Formats proposés partout (sous chaque aperçu) : format, icône, nom
FORMATS = (("16:9", "rectangle-horizontal", "Horizontal 16:9"), ("1:1", "square", "Carré 1:1"),
           ("9:16", "rectangle-vertical", "Vertical 9:16"))


class FormatPicker(QToolButton):
    """Format de la vidéo : un bouton (icône du format choisi) et son menu (horizontal, carré, vertical), puis,
    séparées, les zones masquées par TikTok / Reels (`zone_act`)."""
    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value: str | None = None
        self.setPopupMode(QToolButton.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(18, 18))
        self.setFixedHeight(34)
        self.setCursor(Qt.PointingHandCursor)
        self.menu_ = QMenu(self)
        self.actions_: dict[str, object] = {}
        for asp, ic, name in FORMATS:
            a = self.menu_.addAction(name, lambda asp=asp: self._pick(asp))
            a.setCheckable(True)
            a.setData(ic)
            self.actions_[asp] = a
        self.menu_.addSeparator()
        self.zone_act = self.menu_.addAction("Zones masquées par TikTok / Reels")
        self.zone_act.setCheckable(True)
        self.zone_act.setData("smartphone")
        self.setMenu(self.menu_)
        self.restyle()

    def _pick(self, asp: str) -> None:
        self.set_value(asp)
        self.changed.emit(asp)

    def value(self) -> str | None:
        return self._value

    def set_value(self, aspect: str) -> None:
        """Format du montage (aucun coché si c'est un autre format, 4:5 d'un ancien projet par exemple)."""
        self._value = aspect if aspect in self.actions_ else None
        for asp, a in self.actions_.items():
            a.setChecked(asp == aspect)
        name = next((n for a, _i, n in FORMATS if a == aspect), f"Format {aspect}")
        self.setToolTip(f"Format de la vidéo : {name}")
        self.restyle()

    def restyle(self) -> None:
        mut = theme.color("muted")
        ic = next((i for a, i, _n in FORMATS if a == self._value), "rectangle-vertical")
        self.setIcon(theme.icon(ic, mut, 18))
        self.setText("▾")
        self.setStyleSheet(f"QToolButton {{ padding: 4px 6px; color: {mut}; font-size: 11px; }} "
                           "QToolButton::menu-indicator { image: none; }")
        for a in (*self.actions_.values(), self.zone_act):
            a.setIcon(theme.icon(a.data(), mut, 18))


class MontagePlayer(QWidget):
    time_changed = Signal(float)
    layout_edited = Signal()
    fx_changed = Signal()                   # effets de l'image modifiés (bouton robot)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tl = Timeline()
        self.assets: dict[int, dict] = {}
        self.t = 0.0
        self.index = -1
        self.loaded_asset: int | None = None
        self._pending: float | None = None
        self._playing = False
        self.track_resolver = None          # asset_id -> {"dub", "voice", "music"} (chemins ou None)
        self._plan = {"main": 1.0, "aux": [], "note": ""}
        self._aux_loaded: list[str | None] = [None, None]
        self._aux_pending: list[int | None] = [None, None]   # position à prendre une fois la piste chargée
        self._aux_fixed = [0.0, 0.0]                         # dernier recalage de chaque piste (horloge)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.stack = QStackedLayout()
        self.view = LivePreview()
        self.view.setMinimumHeight(170)
        self.view.layout_edited.connect(self.layout_edited)
        self._second_loaded: int | None = None
        self._output = self.view.video_item          # sortie d'image actuelle du lecteur
        self.stack.addWidget(self.view)
        holder = QFrame()
        holder.setProperty("role", "dim")                  # transparent : le fond de la carte autour de l'image
        holder.setLayout(self.stack)
        lay.addWidget(holder, 1)

        headless = QGuiApplication.platformName() in ("offscreen", "minimal")
        self.enabled = MULTIMEDIA and not headless
        if self.enabled:
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.player.setAudioOutput(self.audio)
            self.player.setVideoOutput(self.view.video_item)
            self._output = self.view.video_item
            # seconde vidéo d'une mise en page (deux vidéos, incrustation) : lecteur calé sur le principal
            self.player2, self.audio2 = QMediaPlayer(self), QAudioOutput(self)
            self.player2.setAudioOutput(self.audio2)
            self.player2.setVideoOutput(self.view.sinks[1])
            self.player.positionChanged.connect(self._on_pos)
            self.player.mediaStatusChanged.connect(self._on_status)
            self.aux = []
            for k in range(2):
                ap, ao = QMediaPlayer(self), QAudioOutput(self)
                ap.setAudioOutput(ao)
                ap.mediaStatusChanged.connect(lambda st, k=k: self._aux_status(k, st))
                self.aux.append((ap, ao))
            from .audio_out import follow_default_output    # écouteurs Bluetooth : le son les suit
            self._sync_audio = follow_default_output(
                self, lambda: [self.audio, self.audio2] + [ao for _, ao in self.aux])
        else:
            self.player = self.player2 = None
            self.aux = []
            self.view.set_placeholder("Lecture vidéo indisponible ici")

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setFixedHeight(22)                  # la poignée ronde n'est jamais coupée
        self.slider.sliderMoved.connect(lambda v: self.seek(v / 1000))
        self.slider_row = QHBoxLayout()                  # barre de lecture : exactement sous l'image
        self.slider_row.setContentsMargins(0, 0, 0, 0)
        self.slider_row.addWidget(self.slider)
        lay.addLayout(self.slider_row)
        self.view.frame_moved.connect(self._frame_moved)
        ctr = QHBoxLayout()
        ctr.setSpacing(2)                                 # outils collés les uns aux autres
        self.ctr = ctr
        self.play_btn = PlayButton("Lire le montage (Espace)", self.toggle, size=26)
        ctr.addWidget(self.play_btn)
        self.tc = label("", "muted")
        self.tc.setTextFormat(Qt.RichText)
        self.tc.setMinimumWidth(92)
        ctr.addSpacing(6)
        ctr.addWidget(self.tc)
        ctr.addStretch(1)
        self.zone = QCheckBox("Zones TikTok / Reels")
        self.zone.setToolTip("Affiche les zones masquées par l'interface TikTok / Reels (boutons, légende).")
        self.zone.toggled.connect(lambda on: self.view.set_zone("TikTok" if on else None))
        self.zone.hide()
        self.fx_btn = tool("bot", "Effets de l'image : miroir, zoom, mouvement, filtre, rotation, vitesse",
                           self._open_fx)
        ctr.addWidget(self.fx_btn)
        self.format_pick = FormatPicker()                 # format, et dans son menu les zones TikTok / Reels
        self.format_pick.zone_act.toggled.connect(self.zone.setChecked)
        ctr.addWidget(self.format_pick)
        self.fx_target = None                             # () -> montage à modifier (celui du projet)
        if self.enabled:
            self.vol = QSlider(Qt.Horizontal)
            self.vol.setRange(0, 100)
            self.vol.setValue(80)
            self.vol.setFixedWidth(60)
            self.vol.setToolTip("Volume d'écoute")
            self.vol.valueChanged.connect(lambda _v: self._apply_volumes())
            self._apply_volumes()
            ctr.addWidget(self.vol)
        lay.addLayout(ctr)
        ctr.addWidget(self.zone)
        self.vol_ = self.vol if self.enabled else None
        self.note = label("", "faint", wrap=True)
        lay.addWidget(self.note)
        self.fit_top = False                # True : aperçu à la hauteur de l'image (la place en trop va dessous)
        for key, fn in (("Space", self.toggle), ("Left", lambda: self.step(-1)), ("Right", lambda: self.step(1))):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

    def _frame_moved(self, a: float, b: float) -> None:
        """Barre de lecture sous l'image, à sa largeur ; les outils aussi s'ils y tiennent, sinon toute la largeur
        (jamais les uns sur les autres)."""
        self.slider_row.setContentsMargins(int(a), 0, int(b), 0)
        need = self.ctr.minimumSize().width() - self.ctr.contentsMargins().left() - self.ctr.contentsMargins().right()
        if self.width() - a - b < need:
            a = b = 0
        self.ctr.setContentsMargins(int(a), 0, int(b), 0)

    def _open_fx(self) -> None:
        """Le rectangle des effets ; ils modifient la vidéo du projet (`fx_target`), pas les textes."""
        from .effects import EffectsPopover
        tl = self.fx_target() if self.fx_target else self.tl
        self.fx_pop = EffectsPopover(tl.video, self)
        self.fx_pop.changed.connect(self.fx_changed.emit)
        if tl is self.tl:
            self.fx_pop.changed.connect(lambda: (self._apply_geometry(), self._apply_rate()))
        self.fx_pop.show_at(self.fx_btn)

    def _apply_rate(self) -> None:
        """Vitesse de l'aperçu : celle de la vidéo exportée."""
        if not self.player:
            return
        from ..edit.render import speed_of
        r = speed_of(self.tl)
        for p in [self.player, self.player2] + [ap for ap, _ in self.aux]:
            if p is not None and hasattr(p, "playbackRate") and abs(p.playbackRate() - r) > 1e-3:
                p.setPlaybackRate(r)

    def add_tool(self, w: QWidget) -> None:
        """Bouton ajouté à la barre de lecture, juste avant le robot (même taille)."""
        w.setFixedSize(self.fx_btn.size())
        self.ctr.insertWidget(self.ctr.indexOf(self.fx_btn), w)        # robot et format restent côte à côte

    def set_compact(self) -> None:
        """Lecteur réduit : barre de lecture et temps seulement ; un clic sur l'image lit ou met en pause."""
        for w in (self.play_btn, self.format_pick, self.vol_):
            if w is not None:
                w.hide()
        self.view.click_toggles = True
        self.view.clicked.connect(self._compact_click)
        self.view.setCursor(Qt.PointingHandCursor)
        self.view.setToolTip("Clic : lecture / pause")
        from PySide6.QtWidgets import QLabel
        self._flash = QLabel(self.view)                   # icône lecture / pause au centre, 1,5 s
        self._flash.setFixedSize(54, 54)
        self._flash.setAlignment(Qt.AlignCenter)
        self._flash.setStyleSheet("background: rgba(0, 0, 0, 115); border-radius: 27px;")
        self._flash.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._flash.hide()
        self._flash_timer = QTimer(self, singleShot=True, interval=1500)
        self._flash_timer.timeout.connect(self._flash.hide)

    def _compact_click(self) -> None:
        self.toggle()
        from . import theme
        self._flash.setPixmap(theme.icon("play" if self._playing else "pause", "#FFFFFF", 24).pixmap(24, 24))
        self._flash.move((self.view.width() - 54) // 2, (self.view.height() - 54) // 2)
        self._flash.show()
        self._flash.raise_()
        self._flash_timer.start()

    def rewind(self) -> None:
        """Autre vidéo (extrait, version, projet) : retour au début, à l'arrêt."""
        self.pause()
        self.t = 0.0

    def _fit_height(self) -> None:
        """`fit_top` : l'aperçu ne prend que la hauteur de l'image pour sa largeur ; il reste ainsi en haut de sa
        colonne, la barre de lecture juste dessous."""
        if not self.fit_top:
            return
        fw, fh = max(1, self.tl.format.width), max(1, self.tl.format.height)
        extra = self.layout().sizeHint().height() - self.view.sizeHint().height()
        h = int(self.width() * fh / fw) + max(0, extra)
        if self.maximumHeight() != h:
            self.setMaximumHeight(h)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._fit_height()

    # -- données -----------------------------------------------------------
    def set_timeline(self, tl: Timeline, assets: dict[int, dict]) -> None:
        self.tl, self.assets = tl, assets
        self._fit_height()
        self.slider.setRange(0, int(tl.duration * 1000))
        self._apply_rate()
        self._setup_layout()
        self._apply_geometry()
        if not tl.segments:
            self.view.set_placeholder("Timeline vide")
        elif not self.enabled:
            self.view.set_placeholder("Lecture vidéo indisponible ici")
        else:
            self.view.set_placeholder("")
        loc = tl.locate(self.t)
        if loc:
            self._update_plan(tl.segments[loc[0]].asset_id)
        self.note.setText(self._plan.get("note", "") if tl.segments else "")
        self.seek(min(self.t, tl.duration), keep_playing=self._playing)

    def _setup_layout(self) -> None:
        """Mise en page à deux vues : images décodées dirigées vers l'aperçu recadré, seconde vidéo chargée."""
        lay = compose.norm(self.tl.video.layout)
        a2 = self.assets.get(lay["asset2"]) if compose.uses_second(lay) else None
        self.view.second_size = (a2["width"], a2["height"]) if a2 and a2.get("width") else None
        if not self.player:
            return
        out = self.view.sinks[0] if lay["kind"] != "single" else self.view.video_item
        if out is not self._output:
            self._output = out
            self.player.setVideoOutput(out)
        aid = a2["id"] if a2 and self.player2 else None
        if aid != self._second_loaded:
            self._second_loaded = aid
            self.player2.setSource(QUrl.fromLocalFile(a2.get("preview_path") or a2["path"]) if a2 else QUrl())
        self._apply_volumes()

    def _sync_second(self, src_t: float, play: bool) -> None:
        if not self.player or self._second_loaded is None:
            return
        self.player2.setPosition(int(max(0.0, src_t + float(compose.norm(self.tl.video.layout)["offset"])) * 1000))
        if play:
            self.player2.play()
        else:
            self.player2.pause()

    def _apply_geometry(self) -> None:
        loc = self.tl.locate(self.t)
        size = None
        if loc:
            a = self.assets.get(self.tl.segments[loc[0]].asset_id)
            if a and a.get("width"):
                size = (a["width"], a["height"])
        self.view.apply(self.tl, size)
        self.view.set_time(self.t)

    # -- lecture -----------------------------------------------------------
    def seek(self, t: float, keep_playing: bool = False) -> None:
        self.t = max(0.0, min(t, self.tl.duration))
        loc = self.tl.locate(self.t)
        self._update_ui()
        if loc is None:
            self.index = -1
            if self.player:
                self.player.stop()
            return
        i, src_t = loc
        seg = self.tl.segments[i]
        new_asset = seg.asset_id != self.loaded_asset
        self.index = i
        if new_asset:
            self._apply_geometry()
        self._update_plan(seg.asset_id)
        if not self.player:
            return
        if new_asset:
            a = self.assets.get(seg.asset_id)
            if not a:
                return
            self.loaded_asset = seg.asset_id
            self._pending = src_t
            self.player.setSource(QUrl.fromLocalFile(a.get("preview_path") or a["path"]))
        else:
            st = getattr(self.player, "mediaStatus", None)
            if self._pending is not None or (st is not None and st() == QMediaPlayer.LoadingMedia):
                self._pending = src_t                     # encore en chargement : position reprise une fois chargé
            self.player.setPosition(int(src_t * 1000))
        self._sync_aux(src_t, keep_playing)
        self._sync_second(src_t, keep_playing)
        if keep_playing:
            self.player.play()
        elif self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()

    def toggle(self) -> None:
        if not self.player or not self.tl.segments:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            if self.player2:
                self.player2.pause()
            for ap, _ in self.aux:
                ap.pause()
            self._playing = False
        else:
            if self.t >= self.tl.duration - 0.05:
                self.seek(0)
            self._sync_audio()
            self._playing = True
            self.seek(self.t, keep_playing=True)
        self._update_ui()

    def pause(self) -> None:
        if self.player:
            self.player.pause()
            if self.player2:
                self.player2.pause()
        for ap, _ in self.aux:
            ap.pause()
        self._playing = False
        self._update_ui()

    def step(self, frames: int) -> None:
        self.pause()
        self.seek(self.t + frames / max(1.0, self.tl.format.fps))

    def stop(self) -> None:
        if self.player:
            self.player.stop()
            self.player.setSource(QUrl())
            if self.player2:
                self.player2.stop()
                self.player2.setSource(QUrl())
            self._second_loaded = None
        for ap, _ in self.aux:
            ap.stop()
            ap.setSource(QUrl())
        self._aux_loaded = [None, None]
        self.loaded_asset = None

    # -- pistes d'écoute supplémentaires (doublage, pistes séparées) ---------------
    def _update_plan(self, asset_id: int) -> None:
        tracks = self.track_resolver(asset_id) if self.track_resolver else None
        self._plan = audio_plan(self.tl, tracks)
        self._apply_volumes()

    def _apply_volumes(self) -> None:
        if not self.player:
            return
        base = self.vol.value() / 100 if hasattr(self, "vol") else 0.8
        lay = compose.norm(self.tl.video.layout)
        two = self._second_loaded is not None
        main = 0.0 if two and lay["audio"] == "second" else 1.0      # même son que l'export, jamais doublé
        self.audio.setVolume(base * min(1.0, self._plan["main"]) * main)
        if self.player2:
            self.audio2.setVolume(base if two and lay["audio"] in ("second", "both") else 0.0)
        for k, (ap, ao) in enumerate(self.aux):
            gain = self._plan["aux"][k][1] if k < len(self._plan["aux"]) else 0.0
            ao.setVolume(base * min(1.0, gain))

    def _sync_aux(self, src_t: float, play: bool) -> None:
        """Pistes d'écoute (voix doublée, musique séparée) calées sur la vidéo. Une piste qui vient d'être choisie
        se charge d'abord : sa position est reprise une fois chargée (`_aux_status`), sinon elle partirait du début
        ou resterait muette."""
        for k, (ap, _) in enumerate(self.aux):
            path = self._plan["aux"][k][0] if k < len(self._plan["aux"]) else None
            path = str(path) if path else None
            if path != self._aux_loaded[k]:
                self._aux_loaded[k] = path
                ap.setSource(QUrl.fromLocalFile(path) if path else QUrl())
                self._aux_pending[k] = None
            if not path:
                continue
            ap.setPosition(int(src_t * 1000))
            if not self._aux_ready(ap):
                self._aux_pending[k] = int(src_t * 1000)
            if play:
                ap.play()
            else:
                ap.pause()

    @staticmethod
    def _aux_ready(ap) -> bool:
        st = getattr(ap, "mediaStatus", None)
        return st is None or st() in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia)

    def _aux_status(self, k: int, status) -> None:
        """Piste d'écoute chargée : elle se place là où en est la vidéo et joue si la lecture est en cours."""
        if status not in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia) or self._aux_pending[k] is None:
            return
        ap = self.aux[k][0]
        pos = self.player.position() if (self._playing and self.player and self._pending is None) \
            else self._aux_pending[k]
        self._aux_pending[k] = None
        ap.setPosition(int(pos))
        if self._playing:
            ap.play()
        else:
            ap.pause()

    def _on_status(self, status) -> None:
        if self._pending is not None and status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            pos = int(self._pending * 1000)
            self.player.setPosition(pos)
            self._pending = None
            if self._playing:
                self.player.play()
            else:
                self._peek(pos)

    def _peek(self, pos: int) -> None:
        """Vidéo chargée à l'arrêt : une image est décodée (lecture très brève, son coupé) pour que l'aperçu
        montre l'endroit du timecode au lieu d'un écran noir."""
        self.audio.setMuted(True)
        self.player.play()

        def done() -> None:
            if not self._playing:
                self.player.pause()
                self.player.setPosition(pos)
            self.audio.setMuted(False)
        QTimer.singleShot(90, done)

    def _on_pos(self, ms: int) -> None:
        if self._pending is not None or self.index < 0 or self.index >= len(self.tl.segments):
            return
        seg = self.tl.segments[self.index]
        src = ms / 1000
        if src < seg.src_in - 0.25:
            return
        self.t = self.tl.segment_start(self.index) + max(0.0, src - seg.src_in)
        import time
        now = time.monotonic()
        for k, (ap, _) in enumerate(self.aux):
            # recalage si une piste dérive (> 250 ms), une fois chargée et au plus une fois par seconde : recaler
            # sans cesse une piste qui se charge encore la rendait muette
            if self._aux_loaded[k] and self._playing and self._aux_pending[k] is None and self._aux_ready(ap) \
                    and abs(ap.position() - ms) > 250 and now - self._aux_fixed[k] > 1.0:
                ap.setPosition(ms)
                self._aux_fixed[k] = now
        if self._second_loaded is not None and self._playing:
            want = ms + int(float(compose.norm(self.tl.video.layout)["offset"]) * 1000)
            if want >= 0 and abs(self.player2.position() - want) > 150:
                self.player2.setPosition(want)
        if src >= seg.src_out - 0.02 and self._playing:
            if self.index + 1 < len(self.tl.segments):
                nxt = self.tl.segments[self.index + 1]
                if nxt.asset_id == seg.asset_id and math.isclose(nxt.src_in, seg.src_out, abs_tol=0.05):
                    self.index += 1          # extraits contigus : pas de saut
                else:
                    self.seek(self.tl.segment_start(self.index + 1), keep_playing=True)
            else:
                self.pause()
                self.t = self.tl.duration
        self._update_ui()

    def _update_ui(self) -> None:
        if not self.slider.isSliderDown():
            self.slider.setValue(int(self.t * 1000))
        self.tc.setText(tc_html(self.t, self.tl.duration))
        self.play_btn.setText("❚❚" if self._playing else "▶")
        loc = self.tl.locate(self.t)
        ox, z, lv = None, 1.0, 1.0
        if loc:
            seg = self.tl.segments[loc[0]]
            z = getattr(seg, "zoom", 1.0) or 1.0
            lv = seg.fade_level(loc[1] - seg.src_in)
            a = self.assets.get(seg.asset_id) or {}
            if a.get("width"):
                ox = reframe_offset(self.tl, seg, loc[1], a["width"], a["height"])
        panning = False
        if loc and ox is None and float(getattr(self.tl.video, "pan", 0.0) or 0.0) > 0:
            seg = self.tl.segments[loc[0]]             # mouvement subtil : d'un bord à l'autre (sens alterné)
            p = max(0.0, min(1.0, (loc[1] - seg.src_in) / max(0.1, seg.duration)))
            ox, panning = ((1 - 2 * p) if loc[0] % 2 else (2 * p - 1)), True
        self.view.set_seg_zoom(z)
        self.view.set_fade(lv)
        self.view.set_ox(ox, panning)
        self.view.set_time(self.t)
        self.time_changed.emit(self.t)
