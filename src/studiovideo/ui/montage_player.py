"""Lecteur du montage avec aperçu en direct du cadrage (§8.1, §8.3, §9.5).

Le lecteur enchaîne les segments de la timeline. L'image est affichée dans le
format de sortie (9:16, 1:1…) avec le recadrage, le zoom, le miroir et les textes
appliqués en direct. Les corrections de couleur et le fond flou ne sont visibles
que dans l'« Aperçu fidèle », qui est une image réellement calculée par FFmpeg.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QRectF, QSizeF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QKeySequence, QPainter, QPen, QPixmap, QShortcut, QTransform
from PySide6.QtWidgets import (QCheckBox, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView,
                               QHBoxLayout, QLabel, QSlider, QStackedLayout, QVBoxLayout, QWidget)

from ..edit.model import Timeline
from ..edit.subrender import render_state, strip_geometry
from ..edit.subtitles import State, states
from ..edit.textrender import render_text_image
from ..media.ffmpeg import fmt_timecode
from .widgets import button, label

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
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
                 seg_zoom: float = 1.0) -> tuple[float, float, float, float]:
    """Position (x, y, w, h) de l'image source (déjà tournée) dans l'image de sortie."""
    W, H = tl.format.width, tl.format.height
    v = tl.video
    zoom = max(1.0, v.zoom) * max(1.0, seg_zoom)
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


class LivePreview(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QColor("#0B0C0E"))
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.frame = QGraphicsRectItem()
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

    def set_ox(self, ox: float | None) -> None:
        """Recadrage automatique : décalage horizontal à l'instant affiché (None = réglage manuel)."""
        if ox == self.ox_override or (ox is not None and self.ox_override is not None
                                      and abs(ox - self.ox_override) < 0.002):
            return
        self.ox_override = ox
        self._geometry()

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
        self.frame.setBrush(QColor("#1A1B1E") if tl.format.fit == "blur" else QColor("#000000"))
        self.scene_.setSceneRect(0, 0, W, H)
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
            x, y, w, h = display_rect(tl, rw, rh, getattr(self, "ox_override", None), getattr(self, "seg_zoom", 1.0))
            iw, ih = (h, w) if rot in (90, 270) else (w, h)
            self.video_item.setSize(QSizeF(iw, ih))
            t = QTransform()
            t.translate(x + w / 2, y + h / 2)
            t.scale(-1 if tl.video.mirror else 1, -1 if tl.video.flip else 1)
            t.rotate(rot)
            t.translate(-iw / 2, -ih / 2)
            self.video_item.setTransform(t)
            self.video_item.setPos(0, 0)

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


class MontagePlayer(QWidget):
    time_changed = Signal(float)
    render_requested = Signal(float)

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
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.stack = QStackedLayout()
        self.view = LivePreview()
        self.view.setMinimumHeight(260)
        self.rendered = QLabel()
        self.rendered.setAlignment(Qt.AlignCenter)
        self.rendered.setStyleSheet("background:#0B0C0E; border-radius:8px;")
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.rendered)
        holder = QWidget()
        holder.setLayout(self.stack)
        lay.addWidget(holder, 1)

        headless = QGuiApplication.platformName() in ("offscreen", "minimal")
        self.enabled = MULTIMEDIA and not headless
        if self.enabled:
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.player.setAudioOutput(self.audio)
            self.player.setVideoOutput(self.view.video_item)
            self.player.positionChanged.connect(self._on_pos)
            self.player.mediaStatusChanged.connect(self._on_status)
            self.aux = []
            for _ in range(2):
                ap, ao = QMediaPlayer(self), QAudioOutput(self)
                ap.setAudioOutput(ao)
                self.aux.append((ap, ao))
        else:
            self.player = None
            self.aux = []
            self.view.set_placeholder("Lecture vidéo indisponible ici")

        self.slider = QSlider(Qt.Horizontal)
        self.slider.sliderMoved.connect(lambda v: self.seek(v / 1000))
        lay.addWidget(self.slider)
        ctr = QHBoxLayout()
        ctr.addWidget(button("◀|", tooltip="Image précédente (←)", on_click=lambda: self.step(-1)))
        self.play_btn = button("▶", tooltip="Lire le montage (Espace)", on_click=self.toggle)
        self.play_btn.setFixedWidth(44)
        ctr.addWidget(self.play_btn)
        ctr.addWidget(button("|▶", tooltip="Image suivante (→)", on_click=lambda: self.step(1)))
        self.tc = label("0:00.000 / 0:00", "muted")
        self.tc.setMinimumWidth(150)
        ctr.addWidget(self.tc)
        ctr.addStretch(1)
        ctr2 = QHBoxLayout()
        self.zone = QCheckBox("Zones TikTok / Reels")
        self.zone.setToolTip("Affiche les zones masquées par l'interface TikTok / Reels (boutons, légende).")
        self.zone.toggled.connect(lambda on: self.view.set_zone("TikTok" if on else None))
        ctr2.addWidget(self.zone)
        ctr2.addStretch(1)
        self.render_btn = button("Aperçu exact de l'export", tooltip="Calcule l'image exacte de l'export à cet instant "
                                 "(couleurs, fond flou, textes)", on_click=self._ask_render)
        self.render_btn.setCheckable(True)
        ctr2.addWidget(self.render_btn)
        if self.enabled:
            self.vol = QSlider(Qt.Horizontal)
            self.vol.setRange(0, 100)
            self.vol.setValue(80)
            self.vol.setFixedWidth(80)
            self.vol.setToolTip("Volume d'écoute")
            self.vol.valueChanged.connect(lambda _v: self._apply_volumes())
            self._apply_volumes()
            ctr.addWidget(self.vol)
        lay.addLayout(ctr)
        lay.addLayout(ctr2)
        self.note = label("", "faint", wrap=True)
        lay.addWidget(self.note)
        for key, fn in (("Space", self.toggle), ("Left", lambda: self.step(-1)), ("Right", lambda: self.step(1))):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

    # -- données -----------------------------------------------------------
    def set_timeline(self, tl: Timeline, assets: dict[int, dict]) -> None:
        self.tl, self.assets = tl, assets
        self.slider.setRange(0, int(tl.duration * 1000))
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
        extra = (" " + self._plan["note"]) if self._plan.get("note") else ""
        self.note.setText("Aperçu en direct : cadrage, miroir et textes. Couleurs et fond flou : « Aperçu exact de "
                          "l'export »." + extra if tl.segments else "")
        self.seek(min(self.t, tl.duration), keep_playing=self._playing)
        if self.render_btn.isChecked():
            self._ask_render()

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
            self.player.setPosition(int(src_t * 1000))
        self._sync_aux(src_t, keep_playing)
        if keep_playing:
            self.player.play()
        elif self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        if self.render_btn.isChecked() and not keep_playing:
            self._render_timer().start()

    def toggle(self) -> None:
        if not self.player or not self.tl.segments:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            for ap, _ in self.aux:
                ap.pause()
            self._playing = False
        else:
            if self.render_btn.isChecked():
                self.render_btn.setChecked(False)
                self.stack.setCurrentIndex(0)
            if self.t >= self.tl.duration - 0.05:
                self.seek(0)
            self._playing = True
            self.seek(self.t, keep_playing=True)
        self._update_ui()

    def pause(self) -> None:
        if self.player:
            self.player.pause()
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
        self.audio.setVolume(base * min(1.0, self._plan["main"]))
        for k, (ap, ao) in enumerate(self.aux):
            gain = self._plan["aux"][k][1] if k < len(self._plan["aux"]) else 0.0
            ao.setVolume(base * min(1.0, gain))

    def _sync_aux(self, src_t: float, play: bool) -> None:
        for k, (ap, _) in enumerate(self.aux):
            path = self._plan["aux"][k][0] if k < len(self._plan["aux"]) else None
            path = str(path) if path else None
            if path != self._aux_loaded[k]:
                self._aux_loaded[k] = path
                ap.setSource(QUrl.fromLocalFile(path) if path else QUrl())
            if not path:
                continue
            ap.setPosition(int(src_t * 1000))
            if play:
                ap.play()
            else:
                ap.pause()

    def _on_status(self, status) -> None:
        if self._pending is not None and status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            self.player.setPosition(int(self._pending * 1000))
            self._pending = None
            if self._playing:
                self.player.play()

    def _on_pos(self, ms: int) -> None:
        if self._pending is not None or self.index < 0 or self.index >= len(self.tl.segments):
            return
        seg = self.tl.segments[self.index]
        src = ms / 1000
        if src < seg.src_in - 0.25:
            return
        self.t = self.tl.segment_start(self.index) + max(0.0, src - seg.src_in)
        for k, (ap, _) in enumerate(self.aux):               # recalage si une piste dérive (> 120 ms)
            if self._aux_loaded[k] and self._playing and abs(ap.position() - ms) > 120:
                ap.setPosition(ms)
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
        self.tc.setText(f"{fmt_timecode(self.t, True)} / {fmt_timecode(self.tl.duration)}")
        self.play_btn.setText("❚❚" if self._playing else "▶")
        loc = self.tl.locate(self.t)
        ox, z = None, 1.0
        if loc:
            seg = self.tl.segments[loc[0]]
            z = getattr(seg, "zoom", 1.0) or 1.0
            a = self.assets.get(seg.asset_id) or {}
            if a.get("width"):
                ox = reframe_offset(self.tl, seg, loc[1], a["width"], a["height"])
        self.view.set_seg_zoom(z)
        self.view.set_ox(ox)
        self.view.set_time(self.t)
        self.time_changed.emit(self.t)

    # -- aperçu fidèle ------------------------------------------------------
    def _render_timer(self) -> QTimer:
        if not hasattr(self, "_rt"):
            self._rt = QTimer(self, singleShot=True, interval=350)
            self._rt.timeout.connect(lambda: self.render_requested.emit(self.t))
        return self._rt

    def _ask_render(self) -> None:
        if not self.render_btn.isChecked():
            self.stack.setCurrentIndex(0)
            return
        if not self.tl.segments:
            self.render_btn.setChecked(False)
            return
        self.pause()
        self.stack.setCurrentIndex(1)
        self.rendered.setText("Calcul de l'image…")
        self._render_timer().start()

    def show_render(self, path: str) -> None:
        if not self.render_btn.isChecked():
            return
        pm = QPixmap(path)
        self.rendered.setPixmap(pm.scaled(self.rendered.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def render_failed(self, msg: str) -> None:
        if self.render_btn.isChecked():
            self.rendered.setText(f"Aperçu impossible :\n{msg[:300]}")
