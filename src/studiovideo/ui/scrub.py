"""Valeurs réglables à la souris, comme dans Premiere Pro : partout où une valeur est un nombre (temps, taille,
volume, durée…), on clique sur la valeur et on balaie vers la gauche (moins) ou la droite (plus) ; Maj : plus
vite, Ctrl : plus fin ; molette ; petits + et − superposés à droite ; double-clic : saisie au clavier.

`ScrubTime` affiche un timecode (« 1:23.4 ») ; `ScrubNumber` un nombre et remplace les champs numériques de Qt
(mêmes méthodes : `setRange`, `setSingleStep`, `setDecimals`, `setSuffix`, signal `valueChanged`)."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QToolButton, QVBoxLayout

from .widgets import tc_html

HEIGHT = 30


def _tc(t: float) -> str:
    """Temps court façon « 4:43.3 » (heures si besoin)."""
    t = max(0.0, float(t or 0))
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h}:{m:02d}:{s:04.1f}" if h else f"{m}:{s:04.1f}"


class ScrubTime(QFrame):
    """Temps réglable à la souris ; `step` : pas des boutons + et − (secondes)."""
    edited = Signal(float)                   # valeur validée (relâchement, molette, boutons, saisie)
    valueChanged = Signal(object)            # chaque changement, même pendant le glissement
    UNIT = "s"

    def __init__(self, parent=None, step: float = 0.5):
        super().__init__(parent)
        self.setProperty("role", "scrub")
        self.setFixedHeight(HEIGHT)
        self.setMaximumWidth(170)                # petit : la valeur et ses + / −
        self._v, self._lo, self._hi = 0.0, 0.0, 1e6
        self._step = step
        self._drag: tuple[float, float] | None = None
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 1, 2, 1)
        h.setSpacing(2)
        self.val = QLabel()
        self.val.setTextFormat(Qt.RichText)
        self.val.setAlignment(Qt.AlignCenter)
        self.val.setCursor(Qt.SizeHorCursor)
        self.val.setStyleSheet("font-weight: 600; font-size: 13px; background: transparent;")
        self.edit = QLineEdit()
        self.edit.setAlignment(Qt.AlignCenter)
        self.edit.setStyleSheet("border: none; padding: 0; background: transparent;")
        self.edit.hide()
        self.edit.editingFinished.connect(self._typed)
        h.addWidget(self.val, 1)
        h.addWidget(self.edit, 1)
        pm = QVBoxLayout()                   # + au-dessus de −, à droite : peu de place
        pm.setContentsMargins(0, 0, 0, 0)
        pm.setSpacing(0)
        self.plus, self.minus = QToolButton(), QToolButton()
        for b, txt, sign in ((self.plus, "+", 1), (self.minus, "−", -1)):
            b.setText(txt)
            b.setAutoRaise(True)
            b.setFixedSize(18, (HEIGHT - 2) // 2)
            b.setProperty("role", "scrubbtn")
            b.clicked.connect(lambda _=False, s=sign: self._commit(self._v + s * self._step))
            pm.addWidget(b)
        h.addLayout(pm)
        self.val.installEventFilter(self)
        self._tips()
        self._show()

    # -- valeur ---------------------------------------------------------------------------
    def value(self) -> float:
        return self._v

    def setValue(self, v: float) -> None:            # noqa: N802 — même usage que les champs de Qt
        old = self._v
        self._v = self._clamp(v)
        self._show()
        if self._v != old:
            self.valueChanged.emit(self._out(self._v))

    def _clamp(self, v: float) -> float:
        return max(self._lo, min(self._hi, float(v)))

    def _out(self, v: float):
        return v

    def setRange(self, lo: float, hi: float) -> None:    # noqa: N802
        self._lo, self._hi = lo, max(lo, hi)
        self._v = self._clamp(self._v)
        self._show()

    def setSingleStep(self, step: float) -> None:        # noqa: N802
        self._step = step
        self._tips()

    def singleStep(self) -> float:                       # noqa: N802
        return self._step

    def minimum(self) -> float:
        return self._lo

    def maximum(self) -> float:
        return self._hi

    def setMinimum(self, lo: float) -> None:             # noqa: N802
        self.setRange(lo, max(lo, self._hi))

    def setMaximum(self, hi: float) -> None:             # noqa: N802
        self.setRange(min(self._lo, hi), hi)

    def setKeyboardTracking(self, _on: bool) -> None:    # noqa: N802 — compatibilité
        pass

    # -- affichage ------------------------------------------------------------------------
    def _unit(self) -> str:
        return self.UNIT

    def _fmt(self, v: float) -> str:
        return f"{v:+g} {self._unit()}".replace(".", ",").strip()

    def _tips(self) -> None:
        self.plus.setToolTip(self._fmt(self._step))
        self.minus.setToolTip(self._fmt(-self._step))
        self.val.setToolTip("Cliquer et balayer à gauche ou à droite pour régler (Maj : plus vite, Ctrl : plus "
                            "fin), molette, double-clic : saisir")

    def _show(self) -> None:
        self.val.setText(tc_html(self._v))

    def _commit(self, v: float) -> None:
        self.setValue(v)
        self.edited.emit(self._v)

    def _text(self) -> str:
        return _tc(self._v)

    def _parse(self, txt: str) -> float:
        v = 0.0
        for p in txt.split(":"):
            v = v * 60 + float(p)
        return v

    def _rates(self) -> tuple[float, float, float]:
        """Pas du glissement par cran de 2 pixels : normal, Maj, Ctrl."""
        return 0.1, 0.5, 0.02

    def _typed(self) -> None:
        txt = self.edit.text().strip().replace(",", ".")
        self.edit.hide()
        self.val.show()
        try:
            v = self._parse(txt)
        except ValueError:
            return
        self._commit(v)

    def eventFilter(self, obj, e) -> bool:              # noqa: N802
        t = e.type()
        if t == QEvent.MouseButtonPress and e.button() == Qt.LeftButton and self.isEnabled():
            self._drag = (e.globalPosition().x(), self._v)
            return True
        if t == QEvent.MouseMove and self._drag is not None:
            dx = e.globalPosition().x() - self._drag[0]
            mods = e.modifiers()
            r = self._rates()
            rate = r[1] if mods & Qt.ShiftModifier else r[2] if mods & Qt.ControlModifier else r[0]
            self.setValue(self._drag[1] + round(dx / 2) * rate)       # 2 pixels = un cran
            return True
        if t == QEvent.MouseButtonRelease and self._drag is not None:
            start = self._drag[1]
            self._drag = None
            if abs(self._v - start) > 1e-9:
                self.edited.emit(self._v)
            return True
        if t == QEvent.MouseButtonDblClick and self.isEnabled():
            self._drag = None
            self.val.hide()
            self.edit.setText(self._text())
            self.edit.show()
            self.edit.setFocus()
            self.edit.selectAll()
            return True
        return super().eventFilter(obj, e)

    def wheelEvent(self, e) -> None:                    # noqa: N802
        if not self.isEnabled():
            return
        steps = e.angleDelta().y() / 120
        if steps:
            k = 5 if e.modifiers() & Qt.ShiftModifier else 1
            self._commit(self._v + steps * k * self._wheel())

    def _wheel(self) -> float:
        return 0.1


class ScrubNumber(ScrubTime):
    """Nombre réglable à la souris ; remplace les champs numériques de Qt (`decimals` = 0 : nombre entier)."""

    def __init__(self, parent=None, decimals: int = 0, step: float | None = None, suffix: str = " px"):
        self._dec, self._suffix, self._special = decimals, suffix, ""
        super().__init__(parent, step if step is not None else (1 if decimals == 0 else 10 ** -decimals))
        self._lo, self._hi = -1e9, 1e9

    def setDecimals(self, d: int) -> None:               # noqa: N802
        self._dec = d
        self._show()

    def decimals(self) -> int:
        return self._dec

    def setSuffix(self, s: str) -> None:                 # noqa: N802
        self._suffix = s
        self._tips()
        self._show()

    def setSpecialValueText(self, s: str) -> None:       # noqa: N802 — texte affiché au minimum (« Auto »…)
        self._special = s
        self._show()

    def setGroupSeparatorShown(self, _on: bool) -> None:  # noqa: N802
        pass

    def _clamp(self, v: float) -> float:
        return round(super()._clamp(v), self._dec)

    def _out(self, v: float):
        return int(round(v)) if self._dec == 0 else v

    def value(self):
        return self._out(self._v)

    def _unit(self) -> str:
        return self._suffix.strip()

    def _fmt(self, v: float) -> str:
        n = f"{v:+.{self._dec}f}".replace(".", ",")
        return f"{n}{self._suffix}"

    def _show(self) -> None:
        if self._special and self._v <= self._lo:
            self.val.setText(self._special)
            return
        self.val.setText(self._text().replace(".", ",") + self._suffix)

    def _text(self) -> str:
        return f"{self._v:.{self._dec}f}"

    def _parse(self, txt: str) -> float:
        return float(txt.replace(self._suffix.strip(), "").split()[0])

    def _rates(self) -> tuple[float, float, float]:
        fine = self._step if self._dec == 0 else max(10 ** -self._dec, self._step / 5)
        return self._step, self._step * 5, fine

    def _wheel(self) -> float:
        return self._step


def double_spin() -> ScrubNumber:
    """À la place d'un champ décimal de Qt (mêmes réglages par défaut : 2 décimales, pas de 1, de 0 à 99,99)."""
    w = ScrubNumber(decimals=2, step=1.0, suffix="")
    w.setRange(0, 99.99)
    return w


def int_spin() -> ScrubNumber:
    """À la place d'un champ entier de Qt (de 0 à 99 par défaut)."""
    w = ScrubNumber(decimals=0, step=1, suffix="")
    w.setRange(0, 99)
    return w
