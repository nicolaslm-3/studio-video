"""Composants d'interface réutilisables."""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget)


def label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    return lb


def button(text: str, kind: str | None = None, tooltip: str = "", on_click=None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if tooltip:
        b.setToolTip(tooltip)
    if on_click:
        b.clicked.connect(on_click)
    b.setCursor(Qt.PointingHandCursor)
    return b


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
        col.addWidget(label(title, "h1"))
        if subtitle:
            col.addWidget(label(subtitle, "muted"))
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


class JobBridge(QObject):
    """Relaye les notifications des threads de travail vers le thread graphique."""
    changed = Signal(int)

    def __call__(self, job_id: int) -> None:
        self.changed.emit(job_id)


STATUS_LABELS = {"pending": "En attente", "running": "En cours", "succeeded": "Terminée",
                 "failed": "Échec", "cancelled": "Annulée"}

PLATFORM_COLORS = {"youtube": "#C46A62", "tiktok": "#7FB3B8", "instagram": "#B98AAE",
                   "facebook": "#7C93C4", "x": "#A3A6AB"}
