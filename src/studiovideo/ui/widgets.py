"""Composants d'interface réutilisables."""
from __future__ import annotations

from PySide6.QtCore import QObject, QPropertyAnimation, QRectF, QSize, Qt, Property, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
                               QWidget)


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


class Popover(QFrame):
    """Petit rectangle volant, ouvert au-dessus d'un bouton (en dessous s'il n'y a pas la place), toujours dans
    l'écran. Son contenu va dans `body`."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QFrame()
        box.setProperty("role", "popover")
        outer.addWidget(box)
        self.body = QVBoxLayout(box)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(10)

    def show_at(self, w: QWidget) -> None:
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QGuiApplication
        self.adjustSize()
        top = w.mapToGlobal(w.rect().topLeft())
        scr = (w.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        y = top.y() - self.height() - 4
        if y < scr.top():                                  # pas la place au-dessus : en dessous
            y = top.y() + w.height() + 4
        x = max(scr.left(), min(top.x(), scr.right() - self.width()))
        self.move(QPoint(x, max(scr.top(), min(y, scr.bottom() - self.height()))))
        self.show()


class _Confirm(Popover):
    """Question oui / non dans un rectangle volant, au centre de la fenêtre."""

    def __init__(self, parent, title: str, text: str, yes: str, no: str):
        super().__init__(parent)
        self.answer = False
        self.setMaximumWidth(460)
        t = label(title, "h2")
        self.body.addWidget(t)
        msg = QLabel(text)
        msg.setWordWrap(True)
        msg.setMinimumWidth(320)
        self.body.addWidget(msg)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button(no, "ghost", on_click=self.close))
        self.yes = button(yes, "primary", on_click=self._yes)
        row.addWidget(self.yes)
        self.body.addLayout(row)

    def _yes(self) -> None:
        self.answer = True
        self.close()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        if getattr(self, "loop", None):
            self.loop.quit()


def ask(parent, title: str, text: str, *_buttons, yes: str = "Oui", no: str = "Non") -> bool:
    """Confirmation (« Supprimer… ? ») dans un rectangle volant au lieu d'une fenêtre ; un clic à côté vaut
    « Non ». Sans écran (tests), la boîte de dialogue standard de Qt, qu'on peut simuler."""
    from PySide6.QtCore import QEventLoop, QPoint
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QMessageBox
    if QGuiApplication.platformName() in ("offscreen", "minimal"):
        return QMessageBox.question(parent, title, text) == QMessageBox.Yes
    win = parent.window() if parent is not None else None
    pop = _Confirm(win, title, text, yes, no)
    pop.adjustSize()
    if win is not None:
        c = win.mapToGlobal(win.rect().center())
        pop.move(c - QPoint(pop.width() // 2, pop.height() // 2))
    pop.loop = QEventLoop()
    pop.show()
    pop.yes.setFocus()
    pop.loop.exec()
    return pop.answer


class _HistoryEntry(QFrame):
    clicked = Signal()

    def __init__(self, title: str, sub: str, active: bool, tip: str = ""):
        super().__init__()
        self.setProperty("role", "projitem")             # même forme que la barre des projets
        self.setProperty("selected", "true" if active else "false")
        self.setCursor(Qt.PointingHandCursor)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 6, 10, 6)
        v.setSpacing(1)
        self.title = ElidedLabel(("✓ " if active else "") + title)
        self.title.setStyleSheet("font-weight: 700;")
        v.addWidget(self.title)
        self.sub = ElidedLabel(sub, "faint")
        v.addWidget(self.sub)
        if tip:
            for w in (self, self.title, self.sub):
                w.setToolTip(tip)

    def mouseReleaseEvent(self, e) -> None:          # noqa: N802
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class HistoryPopover(Popover):
    """Historique (transcriptions, traductions, voix) : chaque version, la plus récente en haut — le moteur en
    écriture principale, en petit et en gris le début du texte ; un clic l'applique. `entries` :
    (identifiant, titre, sous-titre, appliquée, infobulle)."""
    picked = Signal(int)

    def __init__(self, title: str, entries: list, parent=None, empty: str = "Rien pour l'instant."):
        super().__init__(parent)
        self.body.addWidget(label(title, "h2"))
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.entries: list[_HistoryEntry] = []
        for x in entries:
            if isinstance(x, str):                        # titre d'un groupe (une langue)
                lay.addWidget(label(x, "muted", hint=False))
                continue
            hid, t, sub, active, tip = x
            e = _HistoryEntry(t, sub, active, tip)
            e.clicked.connect(lambda hid=hid: (self.picked.emit(hid), self.hide()))
            e.hid = hid
            lay.addWidget(e)
            self.entries.append(e)
        if not entries:
            lay.addWidget(label(empty, "faint", hint=False))
        lay.addStretch(1)
        from PySide6.QtWidgets import QScrollArea
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setWidget(host)
        sc.setFixedWidth(320)
        sc.setMinimumHeight(min(360, max(60, 54 * len(entries) + 8)))
        sc.setMaximumHeight(360)
        self.body.addWidget(sc)


def local_time(iso: str | None, fmt: str = "%d/%m/%Y %H:%M") -> str:
    """Date enregistrée en temps universel (« 2026-10-28T09:05:00Z ») → à l'heure du PC."""
    from datetime import datetime, timezone
    if not iso:
        return ""
    s = iso.strip().rstrip("Z").replace(" ", "T")[:19]
    for f in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s, f).replace(tzinfo=timezone.utc).astimezone().strftime(fmt)
        except ValueError:
            continue
    return iso[:16].replace("T", " ")


class ToggleSwitch(QAbstractButton):
    """Interrupteur façon iPhone : la pastille glisse de gauche (arrêt, gris) à droite (marche, en couleur)."""

    def __init__(self, tooltip: str = "", parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setFixedSize(38, 22)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(120)
        self.toggled.connect(self._slide)

    def sizeHint(self) -> QSize:                     # noqa: N802
        return QSize(38, 22)

    def _get_knob(self) -> float:
        return self._pos

    def _set_knob(self, v: float) -> None:
        self._pos = v
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def _slide(self, on: bool) -> None:
        self._anim.stop()
        if self.isVisible():
            self._anim.setStartValue(self._pos)
            self._anim.setEndValue(1.0 if on else 0.0)
            self._anim.start()
        else:
            self._set_knob(1.0 if on else 0.0)

    def setChecked(self, on: bool) -> None:          # noqa: N802 — sans animation quand c'est le programme
        super().setChecked(on)
        self._anim.stop()
        self._set_knob(1.0 if on else 0.0)

    def paintEvent(self, _e) -> None:                # noqa: N802
        from . import theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1, 1, self.width() - 2, self.height() - 2)
        on = QColor(theme.color("accent"))
        off = QColor(theme.color("border"))
        c = QColor(round(off.red() + (on.red() - off.red()) * self._pos),
                   round(off.green() + (on.green() - off.green()) * self._pos),
                   round(off.blue() + (on.blue() - off.blue()) * self._pos))
        if not self.isEnabled():
            c.setAlpha(110)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 4
        x = r.x() + 2 + (r.width() - d - 4) * self._pos
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, r.y() + 2, d, d))
        p.end()


class ElidedLabel(QLabel):
    """Une ligne de texte qui finit par « … » quand la place manque, sans jamais élargir son cadre (texte entier
    au survol)."""

    def __init__(self, text: str = "", role: str | None = None, parent=None):
        super().__init__(parent)
        if role:
            self.setProperty("role", role)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(8)
        self.setText(text)

    def setText(self, text: str) -> None:            # noqa: N802
        self._full = text or ""
        self.setToolTip(self._full)
        self._elide()

    def text(self) -> str:
        return self._full

    def resizeEvent(self, e) -> None:                # noqa: N802
        super().resizeEvent(e)
        self._elide()

    def changeEvent(self, e) -> None:                # noqa: N802 — police ou style changé : recalcul
        super().changeEvent(e)
        self._elide()

    def _elide(self) -> None:
        QLabel.setText(self, self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(8, self.width())))


TENTH_PX = 9          # dixième de seconde : bien plus petit que les chiffres (Qt ignore les tailles en %)


def tc_html(t: float, total: float | None = None) -> str:
    """Timecode « 1:23.4 » en texte riche : le dixième de seconde moitié moins haut et grisé ; `total` : durée
    ajoutée après « / » (sans dixième)."""
    from ..media.ffmpeg import fmt_timecode
    from . import theme
    s = fmt_timecode(t, True)
    head, _, tenth = s.rpartition(".")
    out = (f"<span style='color:{theme.color('text')}'>{head}</span><span style='color:{theme.color('faint')}; "
           f"font-size:{TENTH_PX}px'>.{tenth}</span>") if head else s
    if total is not None:
        out += f"<span style='color:{theme.color('muted')}'> / {fmt_timecode(total)}</span>"
    return out


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

    def __init__(self, tooltip: str = "", on_click=None, size: int = 40):
        super().__init__()
        from PySide6.QtCore import QSize
        self.setFixedSize(size, size)
        self.icon_px = round(size * 0.42)
        self.setIconSize(QSize(self.icon_px, self.icon_px))
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("kind", "primary")
        self.setStyleSheet(f"QPushButton {{ border-radius: {size // 2}px; padding: 0; }}")
        if tooltip:
            self.setToolTip(tooltip)
        if on_click:
            self.clicked.connect(on_click)
        self._playing = False
        self._apply()

    def _apply(self) -> None:
        from . import theme
        self.setIcon(theme.icon("pause" if self._playing else "play", theme.color("accent_text"), self.icon_px))

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


def dot_icon(hex_color: str, size: int = 10):
    """Point de couleur plein (menus des concepts, comme une liste à puces)."""
    from PySide6.QtGui import QIcon
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(hex_color))
    p.setPen(Qt.NoPen)
    p.drawEllipse(size // 2, size // 2, size, size)
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


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
