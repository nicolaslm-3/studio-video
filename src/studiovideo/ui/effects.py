"""Effets de l'image (bouton robot sous chaque aperçu) : miroir, zoom et recadrage, mouvement subtil, filtre
colorimétrique et son intensité, légère rotation, vitesse.

Ils s'appliquent à la vidéo de base du projet (`Timeline.video`), jamais aux textes ni aux sous-titres, posés par-dessus
à l'export (`edit/render.py`). L'aperçu montre miroir, zoom, rotation, mouvement et vitesse tels quels ; les filtres
y sont approchés, l'image exacte est celle de la vidéo exportée."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QSlider, QToolButton

from ..edit.render import LOOKS
from .scrub import ScrubNumber
from .widgets import Popover, ToggleSwitch, button, label

# effets de l'image : (clé, nom, ce que ça change) — dans l'ordre du menu
TOOLS = (("mirror", "Miroir horizontal", "Change l'orientation"),
         ("zoom", "Zoom et recadrage", "Modifie la composition et adapte au format (9:16…)"),
         ("pan", "Mouvement subtil", "Panoramique lent : rend le cadrage plus dynamique"),
         ("look", "Filtre colorimétrique", "Change les couleurs ; l'intensité se règle à droite"),
         ("tilt", "Légère rotation", "Modifie la géométrie du cadre (image agrandie : pas de coins vides)"),
         ("speed", "Vitesse", "Accélère ou ralentit la vidéo exportée (voix à la même hauteur)"))


# filtres : un emoji chacun, le nom au survol
LOOK_EMOJI = {"": "🚫", "chaud": "🔥", "froid": "❄️", "vif": "🌈", "doux": "☁️", "cinema": "🎬", "vintage": "🎞️",
              "nb": "🐼"}


def _slider(lo: int, hi: int, tip: str) -> QSlider:
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    s.setFixedWidth(96)
    s.setToolTip(tip)
    return s


class EffectsPopover(Popover):
    """Le petit rectangle des effets ; `changed` à chaque réglage (la vidéo du projet est déjà modifiée)."""
    changed = Signal()

    def __init__(self, video, parent=None):
        super().__init__(parent)
        self.v = video
        self._loading = True
        self.body.addWidget(label("Effets de l'image", "h2"))
        self.body.addWidget(label("La vidéo seulement : les textes et les sous-titres ne bougent pas.", "faint",
                                  hint=False))
        rows: dict[str, QHBoxLayout] = {}
        for key, name, tip in TOOLS:
            h = QHBoxLayout()
            h.setSpacing(8)
            lb = label(name)
            lb.setToolTip(tip)
            lb.setFixedWidth(150)
            h.addWidget(lb)
            rows[key] = h
            self.body.addLayout(h)
        # miroir
        self.mirror = ToggleSwitch("Miroir horizontal")
        self.mirror.setChecked(bool(video.mirror))
        self.mirror.toggled.connect(lambda on: self._set("mirror", bool(on)))
        rows["mirror"].addWidget(self.mirror)
        rows["mirror"].addStretch(1)
        # zoom et cadrage (décalage gauche / droite, haut / bas)
        self.zoom = ScrubNumber(suffix=" %", step=5)
        self.zoom.setRange(100, 250)
        self.zoom.setValue(round(video.zoom * 100))
        self.zoom.setFixedWidth(84)
        self.zoom.setToolTip("Zoom")
        self.zoom.valueChanged.connect(lambda v: self._set("zoom", round(v / 100, 3)))
        rows["zoom"].addWidget(self.zoom)
        self.ox = _slider(-100, 100, "Cadrage gauche / droite")
        self.oy = _slider(-100, 100, "Cadrage haut / bas")
        self.ox.setValue(int(video.offset_x * 100))
        self.oy.setValue(int(video.offset_y * 100))
        self.ox.valueChanged.connect(lambda v: self._set("offset_x", v / 100))
        self.oy.valueChanged.connect(lambda v: self._set("offset_y", v / 100))
        rows["zoom"].addWidget(self.ox)
        rows["zoom"].addWidget(self.oy)
        rows["zoom"].addStretch(1)
        # mouvement subtil
        self.pan = ToggleSwitch("Mouvement subtil")
        self.pan.setChecked(video.pan > 0)
        self.pan_amount = _slider(10, 100, "Amplitude du mouvement")
        self.pan_amount.setValue(int((video.pan or 0.5) * 100))
        self.pan.toggled.connect(self._pan)
        self.pan_amount.valueChanged.connect(self._pan)
        rows["pan"].addWidget(self.pan)
        rows["pan"].addWidget(self.pan_amount)
        rows["pan"].addStretch(1)
        # filtre et intensité
        self.looks = QButtonGroup(self)
        self.looks.setExclusive(True)
        lw = QFrame()
        lw.setProperty("role", "dim")                     # transparent : pas de bande derrière les filtres
        lh = QHBoxLayout(lw)
        lh.setContentsMargins(0, 0, 0, 0)
        lh.setSpacing(2)
        for key, name in (("", "Aucun"),) + tuple((k, v[0]) for k, v in LOOKS.items()):
            b = QToolButton()                             # taille fixe : la case choisie ne change pas de taille
            b.setText(LOOK_EMOJI.get(key, name[:1]))
            b.setToolTip(name)
            b.setProperty("role", "flag")
            b.setFixedSize(36, 34)
            b.setStyleSheet("font-size: 17px;")
            b.setCursor(Qt.PointingHandCursor)
            b.setCheckable(True)
            b.setChecked(key == (video.look or ""))
            b.setProperty("look", key)
            self.looks.addButton(b)
            lh.addWidget(b)
        lh.addStretch(1)
        self.looks.buttonToggled.connect(lambda b, on: on and self._set("look", b.property("look")))
        self.body.addWidget(lw)
        self.amount = _slider(0, 100, "Intensité du filtre")
        self.amount.setValue(int(video.look_amount * 100))
        self.amount.valueChanged.connect(lambda v: self._set("look_amount", v / 100))
        rows["look"].addWidget(self.amount)
        rows["look"].addStretch(1)
        self.body.removeItem(rows["look"])                  # le nom et l'intensité, puis la liste des filtres
        self.body.insertLayout(self.body.indexOf(lw), rows["look"])
        # légère rotation
        self.tilt = ScrubNumber(decimals=1, step=0.5, suffix=" °")
        self.tilt.setRange(-10, 10)
        self.tilt.setValue(video.tilt)
        self.tilt.setFixedWidth(84)
        self.tilt.valueChanged.connect(lambda v: self._set("tilt", float(v)))
        rows["tilt"].addWidget(self.tilt)
        rows["tilt"].addStretch(1)
        # vitesse
        self.speed = ScrubNumber(decimals=2, step=0.05, suffix=" ×")
        self.speed.setRange(0.5, 2.0)
        self.speed.setValue(video.speed)
        self.speed.setFixedWidth(84)
        self.speed.valueChanged.connect(lambda v: self._set("speed", float(v)))
        rows["speed"].addWidget(self.speed)
        rows["speed"].addStretch(1)
        self.note = QLabel("Filtres : couleurs approchées dans l'aperçu, exactes dans la vidéo exportée.")
        self.note.setProperty("role", "faint")
        self.note.setWordWrap(True)
        self.body.addWidget(self.note)
        self.body.addLayout(_reset_row(self._reset))
        self._loading = False

    def _pan(self, *_a) -> None:
        self._set("pan", self.pan_amount.value() / 100 if self.pan.isChecked() else 0.0)

    def _set(self, attr: str, value) -> None:
        if self._loading:
            return
        setattr(self.v, attr, value)
        self.changed.emit()

    def _reset(self) -> None:
        """Tout remettre comme la vidéo d'origine."""
        v = self.v
        v.mirror, v.zoom, v.offset_x, v.offset_y = False, 1.0, 0.0, 0.0
        v.pan, v.look, v.look_amount, v.tilt, v.speed = 0.0, "", 0.7, 0.0, 1.0
        self._loading = True
        self.mirror.setChecked(False)
        self.zoom.setValue(100)
        self.ox.setValue(0)
        self.oy.setValue(0)
        self.pan.setChecked(False)
        for b in self.looks.buttons():
            b.setChecked(b.property("look") == "")
        self.amount.setValue(70)
        self.tilt.setValue(0)
        self.speed.setValue(1.0)
        self._loading = False
        self.changed.emit()


def _reset_row(fn) -> QHBoxLayout:
    h = QHBoxLayout()
    h.addStretch(1)
    h.addWidget(button("Tout réinitialiser", "ghost", on_click=fn))
    return h
