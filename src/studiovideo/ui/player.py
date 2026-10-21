"""Lecteur vidéo avec points d'entrée / sortie (§8.1)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from ..media.ffmpeg import fmt_timecode
from .widgets import button, label

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    MULTIMEDIA = True
except ImportError:  # pragma: no cover
    MULTIMEDIA = False


class PlayerWidget(QWidget):
    in_out_changed = Signal(object, object)      # in_s, out_s
    create_clip = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fps = 25.0
        self.in_s: float | None = None
        self.out_s: float | None = None
        self._duration = 0.0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        from PySide6.QtGui import QGuiApplication
        headless = QGuiApplication.platformName() in ("offscreen", "minimal")
        self.enabled = MULTIMEDIA and not headless
        if not self.enabled:
            box = QLabel("Aperçu vidéo indisponible dans cet environnement." if MULTIMEDIA
                         else "Module multimédia Qt indisponible.")
            box.setAlignment(Qt.AlignCenter)
            box.setMinimumHeight(240)
            box.setStyleSheet("background:#000; color:#8E9198; border-radius:8px;")
            lay.addWidget(box, 1)
            self.player, self.audio = _NullPlayer(), _NullAudio()
            self._build_controls(lay, self.player)
            return
        self.video = QVideoWidget()
        self.video.setMinimumHeight(240)
        self.video.setStyleSheet("background:#000; border-radius:8px;")
        lay.addWidget(self.video, 1)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._on_pos)
        self.player.durationChanged.connect(self._on_dur)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.errorOccurred.connect(lambda e, s: self.status.setText(f"Lecture impossible : {s}"))
        self._build_controls(lay, self.player)

    def _build_controls(self, lay, player) -> None:
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(lambda v: self.player.setPosition(v))
        lay.addWidget(self.slider)

        ctr = QHBoxLayout()
        self.play_btn = button("▶", tooltip="Lecture / pause (Espace)", on_click=self.toggle)
        self.play_btn.setFixedWidth(44)
        ctr.addWidget(button("◀|", tooltip="Image précédente (←)", on_click=lambda: self.step(-1)))
        ctr.addWidget(self.play_btn)
        ctr.addWidget(button("|▶", tooltip="Image suivante (→)", on_click=lambda: self.step(1)))
        self.tc = label("0:00.000 / 0:00", "muted")
        self.tc.setMinimumWidth(150)
        ctr.addWidget(self.tc)
        ctr.addStretch(1)
        self.speed = QComboBox()
        for s in (0.25, 0.5, 1.0, 1.25, 1.5, 2.0):
            self.speed.addItem(f"×{s:g}", s)
        self.speed.setCurrentIndex(2)
        self.speed.setToolTip("Vitesse de lecture")
        self.speed.currentIndexChanged.connect(lambda: self.player.setPlaybackRate(self.speed.currentData()))
        ctr.addWidget(self.speed)
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(80)
        self.vol.setFixedWidth(90)
        self.vol.setToolTip("Volume")
        self.vol.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        self.audio.setVolume(0.8)
        ctr.addWidget(self.vol)
        lay.addLayout(ctr)

        io = QHBoxLayout()
        io.addWidget(button("Début ici", tooltip="Définir le point d'entrée (I)", on_click=self.mark_in))
        io.addWidget(button("Fin ici", tooltip="Définir le point de sortie (O)", on_click=self.mark_out))
        self.io_lbl = label("Entrée — · Sortie — · Durée —", "faint")
        io.addWidget(self.io_lbl)
        io.addStretch(1)
        io.addWidget(button("Prévisualiser l'extrait", tooltip="Lire de l'entrée à la sortie (P)",
                            on_click=self.preview_range))
        self.clip_btn = button("Créer un clip", "primary", "Créer un clip à partir de la sélection (C)",
                               on_click=self._emit_clip)
        io.addWidget(self.clip_btn)
        lay.addLayout(io)
        self.status = label("", "faint")
        lay.addWidget(self.status)
        self._range_end: float | None = None

        for key, fn in (("Space", self.toggle), ("Left", lambda: self.step(-1)), ("Right", lambda: self.step(1)),
                        ("I", self.mark_in), ("O", self.mark_out), ("P", self.preview_range),
                        ("C", self._emit_clip)):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        self._refresh_io()

    # -- API ----------------------------------------------------------------
    def load(self, path: str, fps: float | None = None, in_s=None, out_s=None) -> None:
        self.fps = fps or 25.0
        self.in_s, self.out_s = in_s, out_s
        self.status.setText("")
        self.player.setSource(QUrl.fromLocalFile(path))
        self._refresh_io()

    def unload(self) -> None:
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())

    def position_s(self) -> float:
        return self.player.position() / 1000

    def toggle(self) -> None:
        if self.player.playbackState() == _PLAYING:
            self.player.pause()
        else:
            self._range_end = None
            self.player.play()

    def step(self, frames: int) -> None:
        self.player.pause()
        self.player.setPosition(max(0, int(self.player.position() + frames * 1000 / self.fps)))

    def mark_in(self) -> None:
        self.in_s = self.position_s()
        if self.out_s is not None and self.out_s <= self.in_s:
            self.out_s = None
        self._refresh_io()

    def mark_out(self) -> None:
        pos = self.position_s()
        if self.in_s is not None and pos <= self.in_s:
            self.status.setText("La fin doit être après le début.")
            return
        self.out_s = pos
        self._refresh_io()

    def preview_range(self) -> None:
        if self.in_s is None:
            return
        self.player.setPosition(int(self.in_s * 1000))
        self._range_end = self.out_s
        self.player.play()

    def _emit_clip(self) -> None:
        if self.in_s is None or self.out_s is None:
            self.status.setText("Définissez un début et une fin avant de créer un clip.")
            return
        self.create_clip.emit(self.in_s, self.out_s)

    # -- internes ---------------------------------------------------------
    def _refresh_io(self) -> None:
        dur = (self.out_s - self.in_s) if (self.in_s is not None and self.out_s is not None) else None
        self.io_lbl.setText(f"{fmt_timecode(self.in_s, True)} → {fmt_timecode(self.out_s, True)}"
                            + (f"  ({dur:.1f} s)" if dur is not None else ""))
        self.io_lbl.setToolTip("Point d'entrée → point de sortie (durée)")
        self.clip_btn.setEnabled(dur is not None)
        self.in_out_changed.emit(self.in_s, self.out_s)

    def _on_pos(self, ms: int) -> None:
        if not self.slider.isSliderDown():
            self.slider.setValue(ms)
        self.tc.setText(f"{fmt_timecode(ms / 1000, True)} / {fmt_timecode(self._duration)}")
        if self._range_end is not None and ms / 1000 >= self._range_end:
            self.player.pause()
            self._range_end = None

    def _on_dur(self, ms: int) -> None:
        self._duration = ms / 1000
        self.slider.setRange(0, ms)

    def _on_state(self, st) -> None:
        self.play_btn.setText("❚❚" if st == _PLAYING else "▶")


_PLAYING = QMediaPlayer.PlayingState if MULTIMEDIA else 1


class _NullPlayer:
    """Remplaçant sans rendu (environnement sans affichage) : garde l'état de position."""

    def __init__(self):
        self._pos = 0

    def position(self) -> int:
        return self._pos

    def setPosition(self, ms: int) -> None:
        self._pos = int(ms)

    def playbackState(self):
        return 0

    def play(self) -> None: ...
    def pause(self) -> None: ...
    def stop(self) -> None: ...
    def setSource(self, *_): ...
    def setPlaybackRate(self, *_): ...


class _NullAudio:
    def setVolume(self, *_): ...
