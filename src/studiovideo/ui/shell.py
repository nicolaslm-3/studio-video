"""Coque de l'application : dock centré en bas (parcours), tiroir de navigation, bulles et fenêtres flottantes."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QIcon, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QPushButton, QToolButton, QVBoxLayout, QWidget)

from . import theme
from .widgets import label


def shadow(w: QWidget, blur: int = 28, dy: int = 6, alpha: int = 40) -> None:
    eff = QGraphicsDropShadowEffect(w)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    c = QColor(theme.color("shadow"))
    c.setAlpha(alpha if theme.mode_name == "light" else 120)
    eff.setColor(c)
    w.setGraphicsEffect(eff)


class Bubble(QLabel):
    """Bulle aux couleurs du logiciel : nom au survol (dock), infobulles, messages brefs."""

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setProperty("role", "bubble")

    def paintEvent(self, e) -> None:
        """Fond peint ici : une fenêtre flottante transparente n'affiche pas le fond de la feuille de style
        (le texte se retrouvait sans fond, illisible). Fond clair du thème, liseré de la couleur de l'onglet."""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        border = QColor(theme.color("accent"))
        border.setAlpha(150)
        p.setPen(QPen(border, 1.4))
        p.setBrush(QColor(theme.color("raised")))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 9, 9)
        p.end()
        pal = self.palette()
        pal.setColor(self.foregroundRole(), QColor(theme.color("text")))
        self.setPalette(pal)
        super().paintEvent(e)

    def show_for(self, w: QWidget, text: str, above: bool = True) -> None:
        self.setText(text)
        self.adjustSize()
        g = w.mapToGlobal(QPoint(w.width() // 2, 0 if above else w.height()))
        x = g.x() - self.width() // 2
        y = g.y() - self.height() - 8 if above else g.y() + 8
        self.move(x, y)
        self.show()
        self.raise_()


_bubble: Bubble | None = None


def bubble() -> Bubble:
    global _bubble
    if _bubble is None:
        _bubble = Bubble()
    return _bubble


class TipFilter(QObject):
    """Infobulles de tout le logiciel dans la bulle du thème, à la place de l'infobulle de Windows (un rectangle
    sombre aux coins carrés)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tip = Bubble()
        self.tip.setWordWrap(True)
        self.tip.setMaximumWidth(380)
        self.owner: QWidget | None = None
        self.timer = QTimer(self, singleShot=True, interval=12000)
        self.timer.timeout.connect(self.hide)

    def hide(self) -> None:
        self.tip.hide()
        self.owner = None

    def eventFilter(self, obj, e) -> bool:
        t = e.type()
        if t == QEvent.ToolTip and isinstance(obj, QWidget) and obj.toolTip() and not obj.isWindow():
            self.show_tip(obj, obj.toolTip(), e.globalPos())
            return True
        if obj is self.owner and t in (QEvent.Leave, QEvent.Hide, QEvent.MouseButtonPress, QEvent.Wheel):
            self.hide()
        return False

    def show_tip(self, w: QWidget, text: str, pos: QPoint | None = None) -> None:
        self.owner = w
        self.tip.setText(text)
        short = "\n" not in text and self.tip.fontMetrics().horizontalAdvance(text) + 30 <= self.tip.maximumWidth()
        self.tip.setWordWrap(not short)          # texte court : sur une ligne ; long : à la ligne
        self.tip.adjustSize()
        g = pos or QCursor.pos()
        x, y = g.x() - self.tip.width() // 2, g.y() + 22
        scr = QApplication.screenAt(g) or QApplication.primaryScreen()
        if scr:
            r = scr.availableGeometry()
            x = max(r.left() + 4, min(x, r.right() - self.tip.width() - 4))
            if y + self.tip.height() > r.bottom() - 4:
                y = g.y() - self.tip.height() - 12
        self.tip.move(x, y)
        self.tip.show()
        self.tip.raise_()
        self.timer.start()


class DockButton(QToolButton):
    """Bouton d'icône du dock : couleur propre, nom au survol, point sous l'élément actif."""

    def __init__(self, icon_name: str, text: str, color: str | None = None, checkable: bool = True, parent=None,
                 size: int = 48, dimmed: bool = False):
        super().__init__(parent)
        self.icon_name, self.text_, self.color, self.dimmed = icon_name, text, color, dimmed
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(size, size)
        self.setIconSize(QSize(23, 23) if size >= 44 else QSize(19, 19))
        self.setAccessibleName(text)
        self.badge = ""
        self.restyle()

    def restyle(self) -> None:
        col = theme.color("faint") if self.dimmed else self.color or theme.color("muted")
        self.setIcon(theme.icon(self.icon_name, col, self.iconSize().width()))
        tint = QColor(col)
        tint.setAlpha(34 if theme.mode_name == "light" else 60)
        hover = QColor(col)
        hover.setAlpha(20 if theme.mode_name == "light" else 40)
        self.setStyleSheet(
            f"QToolButton {{ border-radius: 14px; background: transparent; }}"
            f"QToolButton:hover {{ background: rgba({hover.red()},{hover.green()},{hover.blue()},{hover.alpha()}); }}"
            f"QToolButton:checked {{ background: rgba({tint.red()},{tint.green()},{tint.blue()},{tint.alpha()}); }}")

    def enterEvent(self, e):
        bubble().show_for(self, self.text_ + (f" · {self.badge}" if self.badge else ""))
        super().enterEvent(e)

    def leaveEvent(self, e):
        bubble().hide()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        bubble().hide()
        super().mousePressEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        if self.isChecked():
            p.setBrush(QColor(theme.color("faint") if self.dimmed else self.color or theme.color("text")))
            p.drawEllipse(self.width() // 2 - 2, self.height() - 6, 4, 4)
        if self.badge:
            p.setBrush(QColor(theme.color("danger") if self.badge.startswith("!") else self.color
                              or theme.color("accent")))
            p.drawEllipse(self.width() - 13, 6, 8, 8)
        p.end()


class JobsButton(DockButton):
    """Tâches (barre du haut) : pastille quand une tâche tourne ou a échoué ; la liste s'ouvre au clic."""

    def __init__(self, parent=None):
        super().__init__("loader-circle", "Tâches", None, checkable=False, parent=parent, size=34)
        self.running = 0
        self.angle = 0.0
        self.spin = QTimer(self, interval=40)            # une tâche en cours : l'icône tourne
        self.spin.timeout.connect(self._turn)

    def _turn(self) -> None:
        self.angle = (self.angle + 12) % 360
        self.update()

    def restyle(self) -> None:
        super().restyle()
        self.spin_icon = self.icon()
        if getattr(self, "running", 0):
            self.setIcon(QIcon())                         # en cours : l'icône est dessinée tournée (paintEvent)

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self.running:
            return
        s = self.iconSize()
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.translate(self.width() / 2, self.height() / 2)
        p.rotate(self.angle)
        p.drawPixmap(-s.width() // 2, -s.height() // 2, self.spin_icon.pixmap(s))
        p.end()

    def enterEvent(self, e):                     # nom affiché sous le bouton (il est en haut de la fenêtre)
        bubble().show_for(self, self.text_ + (f" · {self.badge.lstrip('!')}" if self.badge else ""), above=False)
        QToolButton.enterEvent(self, e)

    def set_jobs(self, running: int, failed: int) -> None:
        self.badge = f"!{failed} échec" if failed else (f"{running} en cours" if running else "")
        self.color = theme.color("accent") if running else None
        self.running = running
        if running and not self.spin.isActive():
            self.spin.start()
        elif not running:
            self.spin.stop()
            self.angle = 0.0
        self.restyle()
        self.update()


class Dock(QFrame):
    """Barre centrée en bas : menu · calendrier · parcours · Export."""
    parcours_clicked = Signal(str)
    menu_clicked = Signal()
    calendar_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "dock")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)
        self.menu_btn = DockButton("menu", "Menu", None, checkable=False)
        self.menu_btn.clicked.connect(self.menu_clicked.emit)
        lay.addWidget(self.menu_btn)
        self.calendar_btn = DockButton("calendar-days", "Calendrier", None, checkable=False)
        self.calendar_btn.clicked.connect(self.calendar_clicked.emit)
        lay.addWidget(self.calendar_btn)
        lay.addWidget(self._divider())
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, DockButton] = {}
        for mode, (name, ic, col, _desc) in theme.PARCOURS.items():
            b = DockButton(ic, name, col, dimmed=mode in theme.DIMMED)
            b.clicked.connect(lambda _=False, m=mode: self.parcours_clicked.emit(m))
            self.group.addButton(b)
            self.buttons[mode] = b
            lay.addWidget(b)
        name, ic, col, _desc = theme.EXPORT_TAB             # dernier onglet : l'export
        b = DockButton(ic, name, col)
        b.clicked.connect(lambda: self.parcours_clicked.emit("export"))
        self.group.addButton(b)
        self.buttons["export"] = b
        lay.addWidget(b)
        shadow(self, 30, 8, 45)

    @staticmethod
    def _divider() -> QFrame:
        d = QFrame()
        d.setProperty("role", "divider")
        d.setFixedSize(1, 28)
        return d

    def set_active(self, mode: str | None) -> None:
        self.group.setExclusive(False)
        for m, b in self.buttons.items():
            b.setChecked(m == mode)
        self.group.setExclusive(True)

    def restyle(self) -> None:
        for b in [self.menu_btn, self.calendar_btn, *self.buttons.values()]:
            b.restyle()
        shadow(self, 30, 8, 45)


class Popover(QFrame):
    """Fenêtre flottante qui se ferme d'un clic à l'extérieur."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 18)
        self.frame = QFrame()
        self.frame.setProperty("role", "popover")
        shadow(self.frame, 34, 8, 50)
        outer.addWidget(self.frame)
        self.lay = QVBoxLayout(self.frame)
        self.lay.setContentsMargins(14, 14, 14, 14)
        self.lay.setSpacing(6)

    def show_above(self, anchor: QWidget, align: str = "center", below: bool = False) -> None:
        self.adjustSize()
        g = anchor.mapToGlobal(QPoint(0, 0))
        if align == "left":
            x = g.x() - 14
        elif align == "right":
            x = g.x() + anchor.width() - self.width() + 14
        else:
            x = g.x() + anchor.width() // 2 - self.width() // 2
        y = g.y() + anchor.height() - 6 if below else g.y() - self.height() + 6
        scr = QApplication.screenAt(g) or QApplication.primaryScreen()
        if scr:
            r = scr.availableGeometry()
            x = max(r.left() + 4, min(x, r.right() - self.width() - 4))
            y = max(r.top() + 4, y)
        self.move(x, y)
        self.show()


class Drawer(Popover):
    """Navigation : Studio, Médias, Calendrier, Analytics, Concepts, Paramètres."""
    navigate = Signal(int)

    def __init__(self, items: list[tuple[int, str, str]], version: str, parent=None):
        super().__init__(parent)
        self.setFixedWidth(250)
        head = QHBoxLayout()
        head.addWidget(label("Studio Vidéo", "h2"))
        head.addStretch(1)
        head.addWidget(label(f"v{version}", "faint"))
        self.lay.addLayout(head)
        self.lay.addSpacing(6)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for idx, name, ic in items:
            b = QPushButton(name)
            b.setProperty("kind", "nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setIcon(theme.icon(ic, theme.color("muted"), 18))
            b.setIconSize(QSize(18, 18))
            b.icon_name = ic
            self.group.addButton(b, idx)
            self.lay.addWidget(b)
            b.clicked.connect(lambda _=False, i=idx: (self.hide(), self.navigate.emit(i)))

    def button(self, idx: int):
        return self.group.button(idx)

    def restyle(self) -> None:
        for b in self.group.buttons():
            b.setIcon(theme.icon(b.icon_name, theme.color("muted"), 18))
        shadow(self.frame, 34, 8, 50)


class Toast(Bubble):
    """Message bref au-dessus du dock (remplace la barre d'état)."""

    def __init__(self, host: QWidget):
        super().__init__()
        self.host = host
        self.setWordWrap(True)
        self.setMaximumWidth(560)
        from PySide6.QtCore import QTimer
        self.timer = QTimer(self, singleShot=True)
        self.timer.timeout.connect(self.hide)

    def show_message(self, text: str, ms: int = 5000, anchor: QWidget | None = None) -> None:
        self.setText(text)
        self.adjustSize()
        a = anchor or self.host
        g = a.mapToGlobal(QPoint(a.width() // 2, 0))
        self.move(g.x() - self.width() // 2, g.y() - self.height() - 12)
        if self.host.isVisible():
            self.show()
            self.raise_()
        self.timer.start(ms)

    def currentMessage(self) -> str:                  # compatibilité avec QStatusBar
        return self.text() if self.isVisible() or self.timer.isActive() else ""
