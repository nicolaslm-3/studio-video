"""Composants d'interface réutilisables."""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget)


HINT_MIN_CHARS = 60


def label(text: str = "", role: str | None = None, wrap: bool = False, hint: bool | None = None) -> QLabel:
    """Texte. Les longues phrases d'explication grises (muted/faint) sont masquées : interface minimale.

    Leur texte reste disponible en infobulle de la zone (voir `hint_icon`) ; hint=False force l'affichage."""
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    if hint is None:
        hint = role in ("muted", "faint") and len(text) >= HINT_MIN_CHARS
    if hint:
        lb.setProperty("hint", True)
        lb.setToolTip(text)
        lb.setVisible(False)
    return lb


def hint_icon(text: str, size: int = 15) -> QLabel:
    """Petit « i » discret : l'explication s'affiche au survol."""
    from . import theme
    lb = QLabel()
    lb.setPixmap(theme.icon("info", theme.color("faint"), size).pixmap(size, size))
    lb.setToolTip(text)
    lb.setFixedSize(size + 4, size + 4)
    return lb


def button(text: str, kind: str | None = None, tooltip: str = "", on_click=None, icon: str | None = None,
           icon_color: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if icon:
        from PySide6.QtCore import QSize

        from . import theme
        col = icon_color or (theme.color("accent_text") if kind == "primary" else theme.color("accent") if kind == "soft"
                             else theme.color("muted"))
        b.setIcon(theme.icon(icon, col, 16))
        b.setIconSize(QSize(16, 16))
    if kind:
        b.setProperty("kind", kind)
    if tooltip:
        b.setToolTip(tooltip)
    if on_click:
        b.clicked.connect(on_click)
    b.setCursor(Qt.PointingHandCursor)
    return b


def tool(icon: str, tooltip: str = "", on_click=None, size: int = 34, icon_px: int = 18,
         color: str | None = None, checkable: bool = False):
    """Bouton-icône discret (l'action est nommée dans l'infobulle)."""
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QToolButton

    from . import theme
    b = QToolButton()
    b.setIcon(theme.icon(icon, color or theme.color("muted"), icon_px))
    b.setIconSize(QSize(icon_px, icon_px))
    b.setFixedSize(size, size)
    b.setCursor(Qt.PointingHandCursor)
    b.setCheckable(checkable)
    b.icon_name = icon
    if tooltip:
        b.setToolTip(tooltip)
        b.setAccessibleName(tooltip.split(" (")[0])
    if on_click:
        b.clicked.connect(on_click)
    return b


class PlayButton(QPushButton):
    """Lecture / pause rond, couleur d'accent ; setText("❚❚") bascule l'icône (compatibilité)."""

    def __init__(self, tooltip: str = "", on_click=None):
        super().__init__()
        from PySide6.QtCore import QSize
        self.setFixedSize(40, 40)
        self.setIconSize(QSize(18, 18))
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("kind", "primary")
        self.setStyleSheet("QPushButton { border-radius: 20px; padding: 0; }")
        if tooltip:
            self.setToolTip(tooltip)
        if on_click:
            self.clicked.connect(on_click)
        self._playing = False
        self._apply()

    def _apply(self) -> None:
        from . import theme
        self.setIcon(theme.icon("pause" if self._playing else "play", theme.color("accent_text"), 18))

    def setText(self, text: str) -> None:            # noqa: N802 — API Qt
        self._playing = "❚" in text
        self._apply()

    def text(self) -> str:
        return "❚❚" if self._playing else "▶"


class Card(QFrame):
    clicked = Signal()

    def __init__(self, parent=None, clickable: bool = False, padding: int = 16, spacing: int = 8):
        super().__init__(parent)
        self.setProperty("role", "card")
        self.setProperty("clickable", "true" if clickable else "false")
        if clickable:
            self.setCursor(Qt.PointingHandCursor)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(padding, padding, padding, padding)
        self.lay.setSpacing(spacing)
        self._clickable = clickable

    def mouseReleaseEvent(self, e):
        if self._clickable and e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class EmptyState(QWidget):
    """État vide utile : explique quoi faire et propose l'action."""

    def __init__(self, title: str, text: str, action_text: str | None = None, on_action=None, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(8)
        t = label(title, "h2")
        t.setAlignment(Qt.AlignCenter)
        d = label(text, "muted", wrap=True)
        d.setAlignment(Qt.AlignCenter)
        d.setMaximumWidth(460)
        lay.addWidget(t, 0, Qt.AlignCenter)
        lay.addWidget(d, 0, Qt.AlignCenter)
        t.setToolTip(text)
        if action_text:
            b = button(action_text, "primary", on_click=on_action)
            lay.addSpacing(6)
            lay.addWidget(b, 0, Qt.AlignCenter)


class Unavailable(EmptyState):
    """Fonction non livrée : signalée explicitement, jamais simulée (§25)."""

    def __init__(self, feature: str, version: str, detail: str = "", parent=None):
        super().__init__(f"{feature} — non disponible", (
            f"Cette fonction est prévue pour la {version}. Elle n'est pas encore opérationnelle dans cette "
            f"version du logiciel." + (f"\n\n{detail}" if detail else "")), parent=parent)


class PageHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        col = QVBoxLayout()
        col.setSpacing(2)
        t = label(title, "h1")
        if subtitle:
            t.setToolTip(subtitle)            # plus de sous-titre affiché : interface minimale
        col.addWidget(t)
        self.lay.addLayout(col)
        self.lay.addStretch(1)

    def add_action(self, w: QWidget) -> None:
        self.lay.addWidget(w)


def color_dot(hex_color: str, size: int = 10) -> QLabel:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(hex_color))
    p.setPen(Qt.NoPen)
    p.drawEllipse(0, 0, size, size)
    p.end()
    lb = QLabel()
    lb.setPixmap(pm)
    lb.setFixedSize(size, size)
    return lb


def hspacer() -> QWidget:
    w = QWidget()
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return w


class DimOnHover(QWidget):
    """Zone atténuée qui s'éclaire quand la souris passe dessus."""

    def __init__(self, dim: float = 0.4, parent=None):
        super().__init__(parent)
        self.dim = dim
        self.eff = __import__('PySide6.QtWidgets', fromlist=['x']).QGraphicsOpacityEffect(self)
        self.eff.setOpacity(dim)
        self.setGraphicsEffect(self.eff)
        if dim >= 1.0:
            self.eff.setEnabled(False)

    def enterEvent(self, e):
        self.eff.setOpacity(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.eff.setOpacity(self.dim)
        super().leaveEvent(e)


class JobBridge(QObject):
    """Relaye les notifications des threads de travail vers le thread graphique."""
    changed = Signal(int)

    def __call__(self, job_id: int) -> None:
        self.changed.emit(job_id)


STATUS_LABELS = {"pending": "En attente", "running": "En cours", "succeeded": "Terminée",
                 "failed": "Échec", "cancelled": "Annulée"}

PLATFORM_COLORS = {"youtube": "#C46A62", "tiktok": "#7FB3B8", "instagram": "#B98AAE",
                   "facebook": "#7C93C4", "x": "#A3A6AB"}
