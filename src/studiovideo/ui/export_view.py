"""Onglet Export : la vidéo du projet, son nom de fichier, son titre et sa description, adaptés à chaque réseau,
puis publier, programmer, ou seulement l'enregistrer sur le PC.

Chaque bouton « Exporter » du logiciel mène ici. La vidéo exportée est celle du projet ouvert (même montage, même
format dans tous les onglets) ; une langue préparée dans Traduction est une version du projet, rangée sous lui. Le texte
commun (titre + description) est repris sur chaque réseau ; un réseau peut avoir son propre texte. TikTok,
Facebook et Instagram n'ont pas de champ titre : le titre ouvre la description.

Publier, programmer et envoyer en brouillon passent par les publications du Calendrier (`PublicationService`) : la
vidéo est d'abord exportée (tâche critique), puis les publications sont créées pour les comptes choisis. Un compte
sans connexion par API passe par l'Atelier de publication (`publish_workshop.py`, ouvert d'office après « Publier
maintenant ») : texte copié, page d'envoi dans le navigateur dédié du compte, clic final laissé à l'utilisateur. Brouillon :
la vidéo est envoyée sans être publiée (YouTube en privé, boîte de réception TikTok, brouillon de la Page Facebook ;
Instagram n'en propose pas). « Sur mon PC seulement » exporte la vidéo dans le dossier choisi, sans rien publier."""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QDate, QDateTime, QEvent, QLocale, QPoint, QRectF, QSize, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QTextCharFormat
from PySide6.QtWidgets import (QButtonGroup, QCalendarWidget, QCheckBox, QComboBox, QFileDialog, QFrame, QStyle,
                               QHBoxLayout, QLineEdit, QListView, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPlainTextEdit, QProgressBar, QRadioButton, QScrollArea, QStyledItemDelegate,
                               QToolButton, QVBoxLayout, QWidget, QWidgetAction)

from ..core import AppContext
from ..domain.models import PublicationStatus
from ..edit.model import Timeline
from ..publish import rules
from . import theme
from .montage_player import MontagePlayer
from .simple_views import _View, _active_job, _open_folder, _row
from .widgets import button, label, tool

# réseaux de l'onglet Export : (clé plateforme, nom affiché)
NETWORKS = [("youtube", "YouTube Shorts"), ("tiktok", "TikTok"), ("facebook", "Facebook"), ("instagram", "Instagram")]
EMOJI_FILE = Path(__file__).parent / "assets" / "emojis.json"


def compose_for(platform: str, title: str, description: str) -> dict:
    """Texte d'un réseau à partir du texte commun : {"title": … (YouTube seulement), "text": …}."""
    return rules.compose(platform, title, description, "")


def safe_name(name: str) -> str:
    """Nom de fichier valide sous Windows (sans extension)."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", name).strip(" .")[:120] or "video"


def date_prefix(day=None) -> str:
    """Début de chaque fichier exporté : la date du jour, année-mois-jour (« 2026-04-02 »)."""
    from datetime import date
    return (day or date.today()).isoformat()


def file_name(name: str, day=None) -> str:
    """Nom du fichier exporté : « 2026-04-02 Mon nom.mp4 » (une date déjà en tête n'est pas répétée)."""
    base = re.sub(r"^\d{4}-\d{2}-\d{2}[ _-]*", "", safe_name(name)) or "video"
    return f"{date_prefix(day)} {base}.mp4"


# -- emojis ----------------------------------------------------------------------------------------------
def _norm(s: str) -> str:
    """Pour la recherche : minuscules, sans accents (« coeur » trouve « cœur »)."""
    s = s.lower().replace("œ", "oe").replace("æ", "ae").replace(" ", " ")
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


@lru_cache(maxsize=1)
def load_emojis() -> tuple[list[str], list[tuple[str, int, str, str]]]:
    """(groupes, [(emoji, groupe, nom français, mots-clés normalisés fr + en)]) — données Unicode / CLDR."""
    try:
        d = json.loads(EMOJI_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], []
    return d["groups"], [(e, g, n.replace(" ", " "), _norm(n + " " + k)) for e, g, n, k in d["emojis"]]


_emoji_pix: dict[tuple[str, float], QPixmap] = {}


class _EmojiDelegate(QStyledItemDelegate):
    """Chaque emoji est dessiné une fois puis gardé en image : le défilement reste fluide."""

    def paint(self, p, opt, index) -> None:
        if opt.state & QStyle.State_MouseOver:
            p.save()
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.color("hover")))
            p.drawRoundedRect(QRectF(opt.rect).adjusted(2, 2, -2, -2), 8, 8)
            p.restore()
        e = index.data(Qt.UserRole)
        dpr = p.device().devicePixelRatioF() if p.device() else 1.0
        pm = _emoji_pix.get((e, dpr))
        if pm is None:
            pm = QPixmap(int(30 * dpr), int(30 * dpr))
            pm.fill(Qt.transparent)
            q = QPainter(pm)
            f = QFont(opt.font)
            f.setPixelSize(int(22 * dpr))
            q.setFont(f)
            q.drawText(QRectF(0, 0, 30 * dpr, 30 * dpr), Qt.AlignCenter, e)
            q.end()
            pm.setDevicePixelRatio(dpr)
            _emoji_pix[(e, dpr)] = pm
        r = opt.rect
        p.drawPixmap(r.x() + (r.width() - 30) // 2, r.y() + (r.height() - 30) // 2, pm)

    def sizeHint(self, _opt, _index) -> QSize:
        return QSize(38, 38)


class EmojiPicker(QFrame):
    """Tous les emojis, par catégorie, avec une recherche en français ou en anglais."""
    GROUP_ICONS = ("😀", "🧑", "🐶", "🍔", "✈️", "⚽", "💡", "❤️", "🏁")

    def __init__(self, on_pick, parent=None):
        super().__init__(parent)
        self.on_pick = on_pick
        self.setFixedSize(392, 380)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher un emoji (français ou anglais)")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(theme.icon("search", theme.color("muted"), 16), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self._filter)
        v.addWidget(self.search)
        groups, items = load_emojis()
        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        self.first_of: dict[int, QListWidgetItem] = {}
        for gi, name in enumerate(groups):
            b = QToolButton()
            b.setText(self.GROUP_ICONS[gi] if gi < len(self.GROUP_ICONS) else "•")
            b.setToolTip(name)
            b.setStyleSheet("font-size: 16px; padding: 2px 4px;")
            b.clicked.connect(lambda _=False, g=gi: self._jump(g))
            tabs.addWidget(b)
        v.addLayout(tabs)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setMovement(QListWidget.Static)
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setUniformItemSizes(True)
        self.list.setGridSize(QSize(38, 38))
        self.list.setWordWrap(False)
        self.list.setLayoutMode(QListView.Batched)
        self.list.setBatchSize(200)
        self.list.setItemDelegate(_EmojiDelegate(self.list))
        self.list.setMouseTracking(True)
        self.items: list[tuple[QListWidgetItem, str]] = []
        for e, g, name, keys in items:
            it = QListWidgetItem()
            it.setToolTip(name)
            it.setTextAlignment(Qt.AlignCenter)
            it.setData(Qt.UserRole, e)
            self.list.addItem(it)
            self.items.append((it, keys))
            self.first_of.setdefault(g, it)
        self.list.itemClicked.connect(lambda it: self.on_pick(it.data(Qt.UserRole)))
        v.addWidget(self.list, 1)
        self.empty = label("Aucun emoji trouvé", "muted", hint=False)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.hide()
        v.addWidget(self.empty)

    def _filter(self, text: str) -> None:
        words = _norm(text).split()
        n = 0
        self.list.setUpdatesEnabled(False)
        for it, keys in self.items:
            show = all(w in keys for w in words)
            it.setHidden(not show)
            n += show
        self.list.setUpdatesEnabled(True)
        self.empty.setVisible(n == 0)
        self.list.scrollToTop()

    def _jump(self, g: int) -> None:
        if self.search.text():
            self.search.clear()
        it = self.first_of.get(g)
        if it is not None:
            self.list.scrollToItem(it, QListWidget.PositionAtTop)

    def reset(self) -> None:
        self.search.clear()
        self.search.setFocus()


_picker_menu: QMenu | None = None


def _emoji_menu() -> tuple[QMenu, EmojiPicker]:
    """Menu d'emojis partagé (construit une fois) : il insère dans le champ du bouton qui l'a ouvert."""
    global _picker_menu
    if _picker_menu is None:
        m = QMenu()
        picker = EmojiPicker(lambda e: (m.target_button.insert(e), m.close()) if m.target_button else None)
        act = QWidgetAction(m)
        act.setDefaultWidget(picker)
        m.addAction(act)
        m.picker = picker
        m.target_button = None
        _picker_menu = m
    return _picker_menu, _picker_menu.picker


class EmojiButton(QToolButton):
    """Icône emoji grise, en haut à droite à l'intérieur du champ : ouvre tous les emojis (avec recherche) et
    insère celui choisi à l'endroit du curseur."""

    def __init__(self, target):
        super().__init__(target)
        self.target = target
        self.setIcon(theme.icon("smile", theme.color("faint"), 16))
        self.setIconSize(QSize(16, 16))
        self.setFixedSize(24, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Insérer un emoji")
        self.setStyleSheet("QToolButton { border: none; padding: 0; border-radius: 6px; background: transparent; }")
        self.clicked.connect(self.open)
        if isinstance(target, QLineEdit):
            target.setTextMargins(0, 0, 22, 0)
        else:
            target.setViewportMargins(0, 0, 22, 0)
        target.installEventFilter(self)
        self._place()

    def _place(self) -> None:
        t = self.target
        y = (t.height() - self.height()) // 2 if isinstance(t, QLineEdit) else 4
        self.move(t.width() - self.width() - 4, max(0, y))

    def eventFilter(self, obj, e) -> bool:
        if obj is self.target and e.type() == QEvent.Resize:
            self._place()
        return super().eventFilter(obj, e)

    def open(self) -> None:
        m, picker = _emoji_menu()
        m.target_button = self
        picker.reset()
        m.popup(self.mapToGlobal(QPoint(0, self.height())))

    def insert(self, emoji: str) -> None:
        t = self.target
        if isinstance(t, QLineEdit):
            t.insert(emoji)
        else:
            t.insertPlainText(emoji)
        t.setFocus()


# -- comptes ---------------------------------------------------------------------------------------------
def account_icon(acc, size: int = 22) -> QIcon:
    """Photo de profil du compte (ronde) ; sans photo, l'initiale sur la couleur du réseau."""
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addEllipse(QRectF(0, 0, size * 2, size * 2))
    src = QPixmap(acc.avatar_path) if getattr(acc, "avatar_path", "") and Path(acc.avatar_path).exists() else None
    if src is not None and not src.isNull():
        p.setClipPath(path)
        sc = src.scaled(size * 2, size * 2, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        p.drawPixmap(-(sc.width() - size * 2) // 2, -(sc.height() - size * 2) // 2, sc)
    else:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.PLATFORMS.get(acc.platform.value, ("", "#8A8F98"))[1] or "#8A8F98"))
        p.drawPath(path)
        f = QFont()
        f.setBold(True)
        f.setPixelSize(int(size * 0.95))
        p.setFont(f)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(QRectF(0, 0, size * 2, size * 2), Qt.AlignCenter, (acc.name.lstrip("@") or "?")[0].upper())
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


ADD_ACCOUNT = -1                                   # dernière ligne du menu des comptes : « Ajouter un compte »
CONNECT_ACCOUNT = -2                               # « Connecter ce compte… » (réglages et connexion)


class RoundCalendar(QCalendarWidget):
    """Calendrier dessiné jour par jour : le jour choisi dans un rond plein, les jours passés ou hors du mois
    grisés."""

    def paintCell(self, painter, rect, date) -> None:     # noqa: N802
        from PySide6.QtGui import QPainter
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(rect, QColor(theme.color("raised")))
        off = (date < self.minimumDate() or date > self.maximumDate() or date.month() != self.monthShown()
               or date.year() != self.yearShown())
        if date == self.selectedDate():
            d = min(rect.width(), rect.height()) - 6
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.color("accent")))
            painter.drawEllipse(QRectF(rect.center().x() - d / 2 + 1, rect.center().y() - d / 2 + 1, d, d))
            painter.setPen(QColor(theme.color("accent_text")))
            f = painter.font()
            f.setBold(True)
            painter.setFont(f)
        else:
            painter.setPen(QColor(theme.color("faint" if off else "text")))
        painter.drawText(rect, Qt.AlignCenter, str(date.day()))
        painter.restore()


class WhenPicker(QWidget):
    """Date et heure de publication : un calendrier épuré (dans un menu), l'heure et les minutes (par 5)."""
    dateTimeChanged = Signal(QDateTime)

    def __init__(self, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.date_btn = QToolButton()
        self.date_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.date_btn.setPopupMode(QToolButton.InstantPopup)
        self.date_btn.setIcon(theme.icon("calendar-days", theme.color("muted"), 16))
        self.date_btn.setProperty("role", "field")
        self.date_btn.setCursor(Qt.PointingHandCursor)
        self.date_btn.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        self.cal = RoundCalendar()
        self.cal.setProperty("role", "cal")
        self.cal.setLocale(QLocale(QLocale.French))
        self.cal.setGridVisible(False)
        self.cal.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.cal.setHorizontalHeaderFormat(QCalendarWidget.SingleLetterDayNames)
        self.cal.setFirstDayOfWeek(Qt.Monday)
        self.cal.setMinimumDate(QDate.currentDate())
        self.cal.setFixedSize(300, 250)
        self.cal.clicked.connect(self._date_picked)
        m = QMenu(self.date_btn)
        m.aboutToShow.connect(self._style_cal)             # couleurs du thème affiché
        act = QWidgetAction(m)
        act.setDefaultWidget(self.cal)
        m.addAction(act)
        self.date_btn.setMenu(m)
        self._style_cal()
        h.addWidget(self.date_btn)
        self.hour, self.minute = QComboBox(), QComboBox()
        for k in range(24):
            self.hour.addItem(f"{k:02d} h", k)
        for k in range(0, 60, 5):
            self.minute.addItem(f"{k:02d}", k)
        self.hour.setToolTip("Heure")
        self.minute.setToolTip("Minutes")
        for c in (self.hour, self.minute):
            c.setMaxVisibleItems(12)
            c.currentIndexChanged.connect(lambda _i: self.dateTimeChanged.emit(self.dateTime()))
            h.addWidget(c)
        h.addStretch(1)
        self.setDateTime(QDateTime.currentDateTime().addSecs(3600))

    def _style_cal(self) -> None:
        plain = QTextCharFormat()
        plain.setForeground(QColor(theme.color("text")))
        for d in (Qt.Saturday, Qt.Sunday):                 # week-end sans rouge : calendrier sobre
            self.cal.setWeekdayTextFormat(d, plain)
        head = QTextCharFormat()
        head.setForeground(QColor(theme.color("faint")))
        self.cal.setHeaderTextFormat(head)

    def _date_picked(self, d: QDate) -> None:
        self.date_btn.menu().close()
        self._show_date(d)
        self.dateTimeChanged.emit(self.dateTime())

    def _show_date(self, d: QDate) -> None:
        self.cal.setSelectedDate(d)
        self.date_btn.setText(QLocale(QLocale.French).toString(d, "ddd d MMM yyyy"))

    def dateTime(self) -> QDateTime:                     # noqa: N802 — comme QDateTimeEdit
        return QDateTime(self.cal.selectedDate(), QTime(self.hour.currentData() or 0, self.minute.currentData() or 0))

    def setDateTime(self, dt: QDateTime) -> None:        # noqa: N802
        m = dt.time().hour() * 60 + dt.time().minute()
        k = -(-m // 5)                                    # arrondi aux 5 minutes suivantes
        d = dt.date().addDays(1) if k >= 288 else dt.date()
        k %= 288
        self.blockSignals(True)
        self._show_date(d)
        self.hour.setCurrentIndex(k // 12)
        self.minute.setCurrentIndex(k % 12)
        self.blockSignals(False)


class PhonePreview(QWidget):
    """Aperçu d'un téléphone : image de la vidéo et interface du réseau (nom du compte, texte, boutons),
    pour voir ce qui sera lisible et ce que l'interface cache."""

    def __init__(self, platform: str, w: int = 180, h: int = 320, parent=None):
        super().__init__(parent)
        self.platform = platform
        self.pix: QPixmap | None = None
        self.account = "ton_compte"
        self.title = ""
        self.text = ""
        self.setFixedSize(w, h)

    def set_content(self, pix: QPixmap | None, account: str, title: str, text: str) -> None:
        self.pix, self.account, self.title, self.text = pix, account or "ton_compte", title, text
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(3, 3, self.width() - 6, self.height() - 6)
        k = r.height() / 374                         # proportions d'un écran de 374 px de haut
        path = QPainterPath()
        path.addRoundedRect(r, 22, 22)
        p.setClipPath(path)
        p.fillRect(r, QColor("#000000"))
        if self.pix is not None and not self.pix.isNull():   # la vidéo remplit l'écran (comme sur le réseau)
            sc = self.pix.scaled(r.size().toSize(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap(int(r.x() + (r.width() - sc.width()) / 2), int(r.y() + (r.height() - sc.height()) / 2), sc)
        else:
            p.setPen(QColor("#8A8A8A"))
            p.drawText(r, Qt.AlignCenter, "Aperçu de la vidéo…")
        for i in range(60):                          # dégradé bas : lisibilité du texte, comme dans les applications
            c = QColor(0, 0, 0, int(i * 2.6))
            p.fillRect(QRectF(r.x(), r.bottom() - 120 * k + i * 2 * k, r.width(), 2 * k + 1), c)
        p.setPen(QColor("#FFFFFF"))
        f = QFont(self.font())
        icons = {"youtube": ["👍", "👎", "💬", "↗"], "tiktok": ["❤", "💬", "🔖", "↗"],
                 "instagram": ["♡", "💬", "➤", "⋯"], "facebook": ["👍", "💬", "↗", "⋯"]}[self.platform]
        f.setPixelSize(max(12, int(17 * k)))
        p.setFont(f)
        y = r.bottom() - 200 * k
        for ic in icons:                             # boutons à droite
            p.drawText(QRectF(r.right() - 38 * k, y, 32 * k, 26 * k), Qt.AlignCenter, ic)
            y += 40 * k
        x, w = r.x() + 10, r.width() - 56 * k
        f.setPixelSize(max(9, int(11 * k)))
        f.setBold(True)
        p.setFont(f)
        acc = "@" + self.account.lstrip("@")
        follow = {"youtube": "  S'abonner", "instagram": "  Suivre", "facebook": "  Suivre", "tiktok": ""}
        y = r.bottom() - 74 * k
        p.drawText(QRectF(x, y - 18 * k, w, 16 * k), Qt.AlignLeft | Qt.AlignVCenter, acc + follow[self.platform])
        text, bold = ((self.title or "Titre du Short"), True) if self.platform == "youtube" else \
            ((self.text or "Description"), False)
        f.setBold(bold)
        p.setFont(f)
        fm = p.fontMetrics()
        out, cur = [], ""
        for word in " ".join(text.split()).split(" "):
            test = (cur + " " + word).strip()
            if fm.horizontalAdvance(test) > w and cur:
                out.append(cur)
                cur = word
            else:
                cur = test
        out.append(cur)
        if len(out) > 2:
            out = out[:2]
            out[-1] = fm.elidedText(out[-1] + " … plus", Qt.ElideMiddle, int(w))
        for i, ln in enumerate(out):
            p.drawText(QRectF(x, y + i * 15 * k, w, 15 * k), Qt.AlignLeft | Qt.AlignVCenter, ln)
        f.setBold(True)
        p.setFont(f)
        top = {"youtube": "Shorts", "tiktok": "Abonnements   Pour toi", "instagram": "Reels",
               "facebook": "Reels"}[self.platform]
        p.drawText(QRectF(r.x(), r.y() + 10 * k, r.width(), 18 * k), Qt.AlignCenter, top)
        p.setClipping(False)
        p.setPen(QPen(QColor(theme.color("border")), 3))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 22, 22)
        p.end()


class NetworkCard(QFrame):
    """Un réseau, en colonne : aperçu propre au réseau en haut, puis compte, titre et description (repris du
    texte commun, ou personnalisés)."""

    def __init__(self, view: "ExportView", platform: str, name: str):
        super().__init__()
        self.view, self.platform, self.name = view, platform, name
        self.setProperty("role", "card")
        self.custom = False
        self._loading = False
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(7)
        self.on = QCheckBox(name)
        self.on.setIcon(theme.platform_icon(platform, 16))
        self.on.setToolTip(f"Publier sur {name}")
        self.on.setStyleSheet("font-weight: 700;")
        self.on.toggled.connect(self.view._changed)
        v.addWidget(self.on)
        self.preview = PhonePreview(platform)
        v.addWidget(self.preview, 0, Qt.AlignHCenter)
        self.account = QComboBox()
        self.account.setIconSize(QSize(22, 22))
        self.account.currentIndexChanged.connect(self._account_changed)
        v.addWidget(self.account)
        self.no_account = button("Ajouter un compte", "ghost", icon="user-plus",
                                 tooltip=f"Ajouter un compte {rules.RULES[platform].label}",
                                 on_click=lambda: self.view.add_account(self.platform))
        self.no_account.setStyleSheet("font-size: 12px; padding: 3px 8px;")
        v.addWidget(self.no_account, 0, Qt.AlignLeft)
        self._last_account: int | None = None
        r = rules.RULES[platform]
        self.title = QLineEdit()
        self.title.setPlaceholderText("Titre (100 caractères au plus)")
        self.title.textEdited.connect(self._edited)
        self.title_row = QWidget()
        tr_ = QHBoxLayout(self.title_row)
        tr_.setContentsMargins(0, 0, 0, 0)
        tr_.setSpacing(2)
        tr_.addWidget(self.title, 1)
        EmojiButton(self.title)
        if r.title_max is not None:
            v.addWidget(label("Titre", "muted", hint=False))
            v.addWidget(self.title_row)
        else:
            self.title_row.hide()
        v.addWidget(label("Description", "muted", hint=False))
        self.text = QPlainTextEdit()
        self.text.setFixedHeight(104)
        self.text.textChanged.connect(self._edited)
        EmojiButton(self.text)
        v.addWidget(self.text)
        self.count = label("", "faint", wrap=True, hint=False)
        v.addWidget(self.count)
        self.reset = button("Reprendre le texte commun", "ghost", on_click=self._reset)
        v.addWidget(self.reset, 0, Qt.AlignLeft)
        v.addStretch(1)

    def _edited(self, *_a) -> None:
        if self._loading:
            return
        self.custom = True
        self.view._changed()

    def _reset(self) -> None:
        self.custom = False
        self.view._changed()
        self.view.refresh_texts()

    def _account_changed(self) -> None:
        pick = self.account.currentData()
        if pick in (ADD_ACCOUNT, CONNECT_ACCOUNT):         # actions du menu : le compte d'avant reste choisi
            self.account.blockSignals(True)
            self.account.setCurrentIndex(max(0, self.account.findData(self._last_account)))
            self.account.blockSignals(False)
            if pick == ADD_ACCOUNT:
                self.view.add_account(self.platform)
            elif self._last_account:
                self.view.add_account(self.platform, self._last_account)
            return
        self._last_account = self.account.currentData()
        self.view._changed()

    def fill_accounts(self, accounts, chosen: int | None) -> None:
        self._loading = True
        self.account.blockSignals(True)
        self.account.clear()
        for a in accounts:
            st = "" if a.connection_status == "connected" else "  · Atelier de publication"
            self.account.addItem(account_icon(a), a.name + st, a.id)
        if accounts:
            self.account.insertSeparator(self.account.count())
            self.account.addItem(theme.icon("plug-zap", theme.color("muted"), 18), "Connecter ce compte…",
                                 CONNECT_ACCOUNT)
            self.account.addItem(theme.icon("user-plus", theme.color("muted"), 18), "Ajouter un compte", ADD_ACCOUNT)
        if chosen is not None:
            self.account.setCurrentIndex(max(0, self.account.findData(chosen)))
        self._last_account = self.account.currentData()
        self.account.blockSignals(False)
        has = bool(accounts)
        self.account.setVisible(has)
        self.no_account.setVisible(not has)
        self._loading = False

    def set_texts(self, title: str, text: str) -> None:
        self._loading = True
        if self.title.text() != title:
            self.title.setText(title)
        if self.text.toPlainText() != text:
            self.text.setPlainText(text)
        self._loading = False
        self.update_count()

    def update_count(self) -> None:
        r = rules.RULES[self.platform]
        n = rules.text_length(self.platform, self.text.toPlainText())
        problems = rules.check(self.platform, self.title.text(), self.text.toPlainText(), "")
        self.count.setText(f"{n} / {r.text_max} caractères" + (f"  ·  ⚠ {problems[0]}" if problems else ""))
        self.reset.setVisible(self.custom)

    def value(self) -> dict:
        acc = self.account.currentData()
        return {"on": self.on.isChecked(), "account": acc if acc not in (ADD_ACCOUNT, CONNECT_ACCOUNT) else None,
                "custom": self.custom,
                "title": self.title.text().strip() if rules.RULES[self.platform].title_max else "",
                "text": self.text.toPlainText().strip()}


ACTIONS = ("publish", "schedule", "draft", "local")
LOCAL = ACTIONS.index("local")
DRAFT_HELP = ("La vidéo est envoyée sans être publiée : en privé sur YouTube (à publier depuis YouTube Studio), dans "
              "la boîte de réception TikTok, en brouillon sur la Page Facebook. Instagram ne propose pas de brouillon "
              "par son API : la vidéo y sera à publier à la main.")


class ExportView(_View):
    """Onglet Export : en haut, un cadre avec la vidéo (aperçu, nom du fichier, dossier, titre, description) et ce
    qu'on en fait ; en bas, une colonne par réseau."""

    def __init__(self, app: AppContext, host, export_dir: Path, parent=None):
        super().__init__(app, host, parent)
        self.export_dir = export_dir
        self._loading = False
        self._frame_key = ""
        self._frame_job: int | None = None
        self._pix: QPixmap | None = None
        self._frame_timer = QTimer(self, singleShot=True, interval=500)
        self._frame_timer.timeout.connect(self._ask_frame)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(26, 12, 26, 10)
        root.setSpacing(14)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setWidget(content)
        outer.addWidget(sc)
        # -- un seul cadre : la vidéo (aperçu, nom du fichier, dossier, titre, description) | ce qu'on en fait
        top = QFrame()
        top.setProperty("role", "card")
        tl_ = QHBoxLayout(top)
        tl_.setContentsMargins(14, 14, 18, 14)
        tl_.setSpacing(16)
        self.player = MontagePlayer()
        self.player.note.setMaximumHeight(0)
        self.player.set_compact()
        self.player.track_resolver = lambda aid: self.host._tracks_for(aid, self.player.tl)   # voix doublée
        self.player.setFixedSize(210, 330)
        tl_.addWidget(self.player, 0, Qt.AlignTop)
        fl = QVBoxLayout()
        fl.setSpacing(6)
        fl.addWidget(label("Nom du fichier", "muted", hint=False))
        self.file_name = QLineEdit()
        self.file_name.setPlaceholderText("Nom de la vidéo exportée")
        self.file_name.textChanged.connect(self._changed)
        self.date_lbl = label(date_prefix(), "muted", hint=False)        # le fichier commence par la date du jour
        self.date_lbl.setToolTip("Le nom du fichier commence par la date du jour (année-mois-jour)")
        fl.addLayout(_row(self.date_lbl, self.file_name, label(".mp4", "muted", hint=False), spacing=4))
        self.folder = str(export_dir)
        self.folder_lbl = label("", "faint", hint=False)
        self.folder_lbl.setMinimumWidth(80)
        self.folder_btn = tool("folder", "Choisir le dossier où enregistrer la vidéo", self._pick_folder)
        fl.addLayout(_row(self.folder_btn, self.folder_lbl, "stretch", spacing=4))
        fl.addWidget(label("Titre sur les réseaux", "muted", hint=False))
        self.title = QLineEdit()
        self.title.setPlaceholderText("Titre de la vidéo")
        self.title.textChanged.connect(self._common_changed)
        EmojiButton(self.title)
        fl.addWidget(self.title)
        fl.addWidget(label("Description", "muted", hint=False))
        self.desc = QPlainTextEdit()
        self.desc.setPlaceholderText("Description, hashtags…")
        self.desc.setFixedHeight(66)
        self.desc.textChanged.connect(self._common_changed)
        EmojiButton(self.desc)
        fl.addWidget(self.desc)
        fl.addStretch(1)
        tl_.addLayout(fl, 3)
        sep = QFrame()
        sep.setProperty("role", "vsep")
        sep.setFixedWidth(1)
        tl_.addSpacing(4)
        tl_.addWidget(sep)
        tl_.addSpacing(4)
        # -- ce qu'on en fait : publier, programmer, brouillon, ou seulement sur le PC
        al = QVBoxLayout()
        al.setSpacing(10)
        al.addWidget(label("Que faire de la vidéo ?", "h2"))
        self.actions = QButtonGroup(self)
        self.act_publish = QRadioButton("Publier maintenant")
        self.act_schedule = QRadioButton("Programmer le")
        self.act_draft = QRadioButton("Brouillon sur les réseaux")
        self.act_draft.setToolTip(DRAFT_HELP)
        self.act_local = QRadioButton("Sur mon PC seulement")
        self.act_local.setToolTip("Enregistre la vidéo dans le dossier choisi, sans rien publier")
        for i, b in enumerate((self.act_publish, self.act_schedule, self.act_draft, self.act_local)):
            self.actions.addButton(b, i)
        self.actions.idToggled.connect(lambda _i, on: on and self._changed())
        self.when = WhenPicker()
        self.when.dateTimeChanged.connect(self._changed)
        al.addWidget(self.act_publish)
        al.addWidget(self.act_schedule)
        al.addWidget(self.when)
        al.addWidget(self.act_draft)
        al.addWidget(self.act_local)
        al.addStretch(1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        al.addWidget(self.progress)
        self.go = button("Exporter", "big", on_click=self._go)
        al.addWidget(self.go)
        self.status = label("", "muted", wrap=True, hint=False)
        al.addWidget(self.status)
        self.open_dir = button("Ouvrir le dossier", "ghost", icon="folder-open",
                               on_click=lambda: _open_folder(Path(self._last_path()).parent))
        self.open_dir.hide()
        self.workshop_btn = button("Atelier de publication", "soft", icon="upload",
                                   tooltip="Publier à la main les réseaux sans connexion par API : texte copié, page "
                                   "d'envoi ouverte dans le navigateur du compte, vidéo à glisser",
                                   on_click=self.open_workshop)
        self.workshop_btn.hide()
        al.addLayout(_row(self.open_dir, self.workshop_btn, "stretch", spacing=6))
        aw = QFrame()
        aw.setProperty("role", "dim")
        aw.setLayout(al)
        aw.setMinimumWidth(290)
        tl_.addWidget(aw, 2)
        root.addWidget(top)
        # -- une colonne par réseau
        nets = QHBoxLayout()
        nets.setSpacing(12)
        self.nets: dict[str, NetworkCard] = {}
        for key, name in NETWORKS:
            c = NetworkCard(self, key, name)
            self.nets[key] = c
            nets.addWidget(c, 1)
        root.addLayout(nets)
        root.addStretch(1)

    # -- état enregistré dans le projet ---------------------------------------------------
    def _state(self) -> dict:
        st = {"title": "", "description": "", "action": "local", "when": "", "nets": {}, "file_name": "",
              "folder": ""}
        if self.pid:
            st.update(self.app.projects.load_data(self.pid).get("export_ui") or {})
        if st["action"] not in ACTIONS:
            st["action"] = "local"
        return st

    def _default_title(self) -> str:
        """Titre proposé : le titre de l'extrait choisi en Clipping, sinon le nom du projet."""
        data = self.app.projects.load_data(self.pid)
        cid = data.get("clip_of")
        r = self.app.candidates.get(cid) if cid else None
        if r is not None and (r["subject"] or "").strip():
            return r["subject"].strip()[:100]
        p = self.app.projects.get(self.pid)
        return p["name"] if p else ""

    def _default_name(self) -> str:
        p = self.app.projects.get(self.pid)
        return safe_name(p["name"] if p else "video")

    def _changed(self, *_a) -> None:
        if self._loading or not self.pid:
            return
        a = self.actions.checkedId()
        st = {"title": self.title.text(), "description": self.desc.toPlainText(),
              "action": ACTIONS[a] if 0 <= a < len(ACTIONS) else "local",
              "when": self.when.dateTime().toString("yyyy-MM-ddTHH:mm"),
              "file_name": self.file_name.text().strip(), "folder": self.folder,
              "nets": {k: t.value() for k, t in self.nets.items()}}
        data = self.app.projects.load_data(self.pid)
        data["export_ui"] = st
        self.app.projects.save_data(self.pid, data)
        for t in self.nets.values():
            t.update_count()
        self._update_go()
        self._update_previews()

    def _common_changed(self) -> None:
        if self._loading:
            return
        self.refresh_texts()
        self._changed()

    def refresh_texts(self) -> None:
        """Réseaux non personnalisés : texte commun mis en forme pour le réseau."""
        for k, t in self.nets.items():
            if not t.custom:
                c = compose_for(k, self.title.text(), self.desc.toPlainText())
                t.set_texts(c["title"], c["text"])
        self._update_previews()

    def _pick_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Dossier de la vidéo exportée", self.folder)
        if d:
            self.folder = d
            self._show_folder()
            self._changed()

    def _show_folder(self) -> None:
        self.folder_lbl.setToolTip(self.folder)
        fm = self.folder_lbl.fontMetrics()
        self.folder_lbl.setText(fm.elidedText(self.folder, Qt.ElideMiddle, 320))

    def add_account(self, platform: str, account_id: int | None = None) -> None:
        """« Ajouter un compte » (ou « Connecter ce compte… ») : le compte, les réglages de l'application du réseau
        et la connexion, ici même (`account_setup.py`)."""
        from .account_setup import AccountSetupDialog
        dlg = AccountSetupDialog(self.app, platform, account_id, self)
        if not dlg.exec():
            return
        data = self.app.projects.load_data(self.pid)
        st = data.setdefault("export_ui", {})
        st.setdefault("nets", {}).setdefault(platform, {}).update({"account": dlg.account_id, "on": True})
        self.app.projects.save_data(self.pid, data)
        self.refresh()
        if dlg.connect_job:
            self.status.setText("Autorisez l'accès dans la page de connexion ouverte dans votre navigateur…")

    # -- affichage ----------------------------------------------------------------------
    def refresh(self) -> None:
        if not self.pid:
            return
        self._loading = True
        try:
            st = self._state()
            self.folder = st["folder"] or str(self.export_dir)
            self._show_folder()
            name = st["file_name"] or self._default_name()
            if self.file_name.text() != name:
                self.file_name.setText(name)
            title = st["title"] if st["title"] or st["description"] else self._default_title()
            if self.title.text() != title:
                self.title.setText(title)
            if self.desc.toPlainText() != st["description"]:
                self.desc.setPlainText(st["description"])
            p = self.app.projects.get(self.pid)
            accounts = self.app.concepts.list_accounts()
            linked = {a.id for a, _d in self.app.concepts.concept_accounts(p["concept_id"])} if p else set()
            for k, t in self.nets.items():
                acc = [a for a in accounts if a.platform.value == k]
                acc.sort(key=lambda a: (a.id not in linked, a.name.lower()))
                saved = st["nets"].get(k) or {}
                t.fill_accounts(acc, saved.get("account"))
                # un réseau qui n'avait pas de compte est coché dès qu'il en a un
                t.on.setChecked(bool(acc) and (saved.get("on", True) if saved.get("account") else True))
                t.on.setEnabled(bool(acc))
                t.custom = bool(saved.get("custom"))
                if t.custom:
                    t.set_texts(saved.get("title", ""), saved.get("text", ""))
            {"publish": self.act_publish, "schedule": self.act_schedule, "draft": self.act_draft}.get(
                st["action"], self.act_local).setChecked(True)
            if st["when"]:
                self.when.setDateTime(QDateTime.fromString(st["when"], "yyyy-MM-ddTHH:mm"))
        finally:
            self._loading = False
        self.refresh_texts()
        self._preview()
        self._update_status()
        self._update_go()

    def version_timeline(self) -> Timeline:
        """Le montage du projet ouvert (une version traduite a le sien, avec ses sous-titres et sa voix)."""
        return self.host.tl

    def _preview(self) -> None:
        tl = self.version_timeline()
        self.player.set_timeline(tl, self.host.assets)
        key = json.dumps(tl.to_dict(), sort_keys=True)
        if key != self._frame_key and tl.segments:
            self._frame_key = key
            self._frame_timer.start()

    def _ask_frame(self) -> None:
        """Image de la vidéo (rendu réel : cadrage, textes, sous-titres) pour les aperçus des réseaux."""
        tl = self.version_timeline()
        if not tl.segments or not self.isVisible():
            return
        if self._frame_job and _active_job(self.app, "preview_frame", "project_id", self.pid):
            self.app.jobs.cancel(self._frame_job)
        self._frame_job = self.app.jobs.submit("preview_frame", "Aperçu des réseaux",
                                               {"timeline": tl.to_dict(), "t": min(1.5, tl.duration / 2),
                                                "project_id": self.pid})

    def hideEvent(self, e) -> None:
        self._frame_timer.stop()                   # onglet quitté (ou logiciel fermé) : pas de rendu en attente
        self.player.pause()
        super().hideEvent(e)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if self._frame_key and self._pix is None:
            self._frame_timer.start()

    def _update_previews(self) -> None:
        for t in self.nets.values():
            t.preview.set_content(self._pix, t.account.currentText().split("  ·")[0] if t.account.count() else "",
                                  t.title.text(), t.text.toPlainText())

    def _update_go(self) -> None:
        if not self.pid:
            return
        running = _active_job(self.app, "export_timeline", "project_id", self.pid)
        pending = self.app.projects.load_data(self.pid).get("export_pending")
        busy = bool(running and pending)
        nets = [t.name for t in self.nets.values() if t.on.isChecked() and t.value()["account"]]
        a = self.actions.checkedId()
        if a == LOCAL:
            self.go.setText("Exporter sur mon PC")
        else:
            verb = {0: "Exporter et publier", 1: "Exporter et programmer", 2: "Exporter en brouillon"}.get(a, "Exporter")
            self.go.setText(verb + (f"  ·  {', '.join(nets)}" if nets else ""))
        tl = self.version_timeline()
        self.go.setEnabled(not busy and bool(tl.segments) and (a == LOCAL or bool(nets)))
        self.go.setToolTip("" if a == LOCAL or nets else "Coche au moins un réseau, avec un compte")
        self.when.setEnabled(a == 1)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setValue(int((running["progress"] or 0) * 100))

    def _last_path(self) -> str:
        return ((self.app.projects.load_data(self.pid).get("export_last") or {}).get("path") or "") if self.pid else ""

    def _update_status(self) -> None:
        data = self.app.projects.load_data(self.pid)
        last = data.get("export_last")
        if data.get("export_pending") and _active_job(self.app, "export_timeline", "project_id", self.pid):
            self.status.setText("Export de la vidéo en cours… La suite (publication, programmation ou brouillon) se "
                                "fera ensuite d'elle-même.")
        elif last:
            self.status.setText(last.get("summary", ""))
        else:
            self.status.setText("")
        path = self._last_path()
        self.open_dir.setVisible(bool(path) and Path(path).exists())
        self.workshop_btn.setVisible(bool(self._manual_pubs()))

    def _manual_pubs(self) -> list[int]:
        """Publications du dernier export encore à faire dans l'Atelier."""
        if not self.pid:
            return []
        ids = (self.app.projects.load_data(self.pid).get("export_last") or {}).get("manual") or []
        return [i for i in ids if (r := self.app.publications.get(i)) is not None
                and r["status"] not in ("published", "cancelled")]

    def open_workshop(self) -> None:
        ids = self._manual_pubs()
        if ids:
            from .publish_workshop import PublishWorkshop
            PublishWorkshop(self.app, ids, self).exec()
            self._update_status()

    # -- action -------------------------------------------------------------------------
    def _destination(self, st: dict) -> Path | None:
        """Fichier de sortie : nom et dossier choisis ; un fichier du même nom n'est remplacé qu'avec accord."""
        folder = Path(st["folder"] or self.export_dir)
        dest = folder / file_name(st['file_name'] or self._default_name())
        if dest.exists() and QMessageBox.question(
                self, "Exporter", f"« {dest.name} » existe déjà dans ce dossier.\n\nLe remplacer ?") != QMessageBox.Yes:
            return None
        return dest

    def _go(self) -> None:
        tl = self.version_timeline()
        if not self.pid or not tl.segments:
            return
        self.host.flush()
        st = self._state()
        a = st["action"]
        if a == "schedule" and st["when"] <= datetime.now().strftime("%Y-%m-%dT%H:%M"):
            QMessageBox.information(self, "Programmer", "Choisis une date et une heure à venir.")
            return
        nets = {} if a == "local" else {k: v for k, v in st["nets"].items() if v.get("on") and v.get("account")}
        for k, v in nets.items():
            problems = rules.check(k, v.get("title", ""), v.get("text", ""), "")
            if problems:
                QMessageBox.warning(self, "Export", f"{self.nets[k].name} : " + "\n".join(problems))
                return
        problems = tl.problems(self.app.montage.asset_row)
        if problems:
            QMessageBox.warning(self, "Export impossible", "\n".join(f"• {p}" for p in problems))
            return
        dest = self._destination(st)
        if dest is None:
            return
        data = self.app.projects.load_data(self.pid)
        lang = data.get("version_lang") or ""
        jid = self.app.jobs.submit("export_timeline", f"Export · {dest.name}",
                                   {"timeline": tl.to_dict(), "dest": str(dest), "project_id": self.pid},
                                   critical=True)
        data = self.app.projects.load_data(self.pid)
        data["export_pending"] = {"job": jid, "action": a, "when": st["when"], "title": st["title"],
                                  "description": st["description"], "language": lang,
                                  "nets": {k: {"account": v["account"], "title": v.get("title", ""),
                                               "text": v.get("text", "")} for k, v in nets.items()}}
        self.app.projects.save_data(self.pid, data)
        self.status.setText("Export de la vidéo en cours…")
        self.open_dir.hide()
        self._update_go()

    def finish(self, pid: int, job) -> None:
        """Export terminé : publications créées puis publiées, programmées ou envoyées en brouillon ; sur le PC
        seulement : rien d'autre."""
        data = self.app.projects.load_data(pid)
        pend = data.get("export_pending")
        if not pend or pend.get("job") != job.id:
            return
        data.pop("export_pending")
        if job.status != "succeeded":
            data["export_last"] = {"summary": "Échec de l'export : " + ((job.error or "").strip().splitlines()
                                                                       or ["annulé"])[-1][:300]}
            self.app.projects.save_data(pid, data)
            return
        path = (job.result or {}).get("path", "")
        name = Path(path).name
        action = pend["action"]
        if action not in ("publish", "schedule", "draft"):
            data["export_last"] = {"summary": f"✓ « {name} » enregistrée dans « {Path(path).parent} ».",
                                   "path": path}
            self.app.projects.save_data(pid, data)
            return
        variant = (job.result or {}).get("variant_id")
        p = self.app.projects.get(pid)
        ps = self.app.publications
        when = pend["when"] if action == "schedule" else None
        mid = ps.create_master(p["concept_id"], pend["title"], pend["description"], "",
                               language=pend.get("language") or "fr", variant_id=variant, planned_at=when)
        accounts = {v["account"]: k for k, v in pend["nets"].items()}
        notes, manual = [], []
        if accounts:
            ps.set_destinations(mid, list(accounts), confirmed_external=set(accounts))
            for pub in ps.list_for_master(mid):
                k = accounts.get(pub.account.id)
                v = pend["nets"].get(k) or {}
                ps.override(pub.id, title=v.get("title", ""), description=v.get("text", ""))
                label_ = rules.RULES[pub.account.platform.value].label
                if action == "schedule":
                    ps.schedule(pub.id, when)
                elif action == "draft" and pub.account.platform.value == "instagram":
                    ps.mark_manual(pub.id, "Instagram ne permet pas d'envoyer un brouillon : publication à faire à "
                                           "la main.")
                    notes.append(f"{label_} : pas de brouillon possible, à publier avec l'Atelier de publication")
                    manual.append(pub.id)
                elif pub.account.connection_status == "connected":
                    ps.set_status(pub.id, PublicationStatus.UPLOADING)
                    self.app.jobs.submit("publish_upload", f"{label_} · {pend['title'] or 'sans titre'}"
                                         + (" · brouillon" if action == "draft" else ""),
                                         {"publication_id": pub.id, "now": True, "draft": action == "draft"},
                                         critical=True)
                else:
                    ps.mark_manual(pub.id, "Sans connexion par API : à publier avec l'Atelier de publication.")
                    notes.append(f"{label_} : à publier avec l'Atelier de publication")
                    manual.append(pub.id)
        if action == "publish" and manual and len(manual) == len(accounts):
            summary = f"✓ « {name} » exportée."
        elif action == "publish":
            summary = f"✓ « {name} » exportée, mise en ligne lancée (suivi dans les tâches)."
        elif action == "draft":
            summary = f"✓ « {name} » exportée, envoi en brouillon lancé (suivi dans les tâches)."
        else:
            d = datetime.strptime(when, "%Y-%m-%dT%H:%M")
            summary = f"✓ « {name} » exportée et programmée le {d:%d/%m/%Y à %H:%M} (Calendrier)."
        if notes:
            summary += "\n" + "\n".join(notes)
        data["export_last"] = {"summary": summary, "path": path, "master_id": mid, "manual": manual}
        self.app.projects.save_data(pid, data)
        if manual and action == "publish" and pid == self.pid and self.isVisible():
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self.open_workshop)          # « Publier maintenant » : l'Atelier s'ouvre d'office

    def on_job(self, job) -> None:
        pid = job.params.get("project_id")
        if job.kind == "export_timeline" and job.status in ("succeeded", "failed", "cancelled") and pid \
                and self.app.projects.get(pid):
            self.finish(pid, job)
        if job.kind == "preview_frame" and job.id == self._frame_job and job.status == "succeeded":
            self._pix = QPixmap(job.result["path"])
            self._update_previews()
        if not self.pid or pid != self.pid:
            return
        if job.kind == "export_timeline":
            if job.status in ("pending", "running"):
                self.progress.setValue(int((job.progress or 0) * 100))
                return
            self._update_status()
            self._update_go()
