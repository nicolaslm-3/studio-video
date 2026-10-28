"""Onglet Export, en deux colonnes.

À gauche, la vidéo du projet : son nom de fichier (précédé de la date du jour), son titre et sa description communs à
tous les réseaux, un petit aperçu ; puis ce qu'on en fait : « Brouillon sur les réseaux » ou « Sur mon PC seulement ».
La vidéo exportée est celle du projet ouvert (même montage, même format dans tous les onglets) ; une langue préparée
dans Traduction est une version du projet, rangée sous lui. TikTok, Facebook et Instagram n'ont pas de champ titre :
le titre ouvre la description.

À droite, l'Atelier de publication (`publish_workshop.py`) : la vidéo exportée, à glisser dans la page d'envoi ou à
afficher dans l'Explorateur, puis une carte par réseau (YouTube, TikTok, Meta — Facebook et Instagram ensemble) :
le compte, « Ouvrir la page d'envoi » dans le navigateur dédié du compte (texte copié, clic final laissé à
l'utilisateur), puis le lien de la vidéo publiée, retrouvé tout seul ou collé. Brouillon : les comptes connectés par
API reçoivent la vidéo sans qu'elle soit publiée (YouTube en privé, boîte de réception TikTok, Page Facebook) ;
les autres passent par la page d'envoi."""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListView, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit,
                               QProgressBar, QRadioButton, QScrollArea, QStyle, QStyledItemDelegate, QToolButton,
                               QVBoxLayout, QWidget, QWidgetAction)

from ..core import AppContext
from ..domain.models import PublicationStatus
from ..edit.model import Timeline
from ..publish import rules
from . import browser_login, theme
from .montage_player import MontagePlayer
from .publish_workshop import LinkBox, VideoTile, reveal, upload_url
from .simple_views import _View, _active_job, _open_folder, _row
from .widgets import button, label, tool

# réseaux de l'onglet Export : (clé, nom affiché) ; Meta réunit Facebook et Instagram (une page d'envoi)
NETWORKS = [("youtube", "YouTube"), ("tiktok", "TikTok"), ("meta", "Meta")]
NET_PLATFORMS = {"youtube": ("youtube",), "tiktok": ("tiktok",), "meta": ("facebook", "instagram")}
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


class NetworkCard(QFrame):
    """Un réseau : son icône et son nom, le compte, puis l'Atelier de publication — la page d'envoi dans le
    navigateur dédié du compte, et le lien de la vidéo une fois publiée."""

    def __init__(self, view: "ExportView", key: str, name: str):
        super().__init__()
        self.view, self.key, self.name = view, key, name
        self.setProperty("role", "card")
        self.login = None
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 14, 12)
        v.setSpacing(7)
        head = QHBoxLayout()
        head.setSpacing(8)
        ic = QLabel()
        ic.setPixmap(theme.platform_icon(key, 20).pixmap(QSize(20, 20)))
        head.addWidget(ic)
        title = label(name, None, hint=False)
        title.setStyleSheet("font-weight: 700;")
        head.addWidget(title)
        head.addStretch(1)
        self.state = label("", "muted", hint=False)
        head.addWidget(self.state)
        v.addLayout(head)
        self.account = QComboBox()
        self.account.setIconSize(QSize(22, 22))
        self.account.currentIndexChanged.connect(self._account_changed)
        v.addWidget(self.account)
        add_for = NET_PLATFORMS[key][0]
        self.no_account = button("Ajouter un compte", "ghost", icon="user-plus",
                                 tooltip=f"Ajouter un compte {name}",
                                 on_click=lambda: self.view.add_account(add_for))
        self.no_account.setStyleSheet("font-size: 12px; padding: 3px 8px;")
        v.addWidget(self.no_account, 0, Qt.AlignLeft)
        self._last_account: int | None = None
        row = QHBoxLayout()
        row.setSpacing(6)
        self.open_btn = button(f"Ouvrir la page d'envoi {name}", "soft", icon="upload",
                               tooltip="Ouvre la page d'envoi dans le navigateur dédié du compte (déjà connecté) et "
                               "copie le texte du réseau : glisse la vidéo, colle le texte, clique toi-même sur "
                               "« Publier »", on_click=lambda: self.view.open_upload(self.key))
        row.addWidget(self.open_btn)
        self.login_btn = button("Se connecter à ce compte", "ghost", icon="log-in", on_click=self._login)
        row.addWidget(self.login_btn)
        row.addStretch(1)
        v.addLayout(row)
        self.note = label("", "faint", wrap=True, hint=False)
        v.addWidget(self.note)
        self.link = LinkBox(view.app, view.open_url)
        self.link.changed.connect(self.view.refresh_cards)
        v.addWidget(self.link)

    def account_id(self) -> int | None:
        a = self.account.currentData()
        return a if a not in (None, ADD_ACCOUNT, CONNECT_ACCOUNT) else None

    def _account_changed(self) -> None:
        pick = self.account.currentData()
        if pick in (ADD_ACCOUNT, CONNECT_ACCOUNT):         # actions du menu : le compte d'avant reste choisi
            self.account.blockSignals(True)
            self.account.setCurrentIndex(max(0, self.account.findData(self._last_account)))
            self.account.blockSignals(False)
            if pick == ADD_ACCOUNT:
                self.view.add_account(NET_PLATFORMS[self.key][0])
            elif self._last_account:
                acc = self.view.app.concepts.get_account(self._last_account)
                self.view.add_account(acc.platform.value, self._last_account)
            return
        self._last_account = self.account.currentData()
        self.view._changed()
        self.view.refresh_cards()

    def fill_accounts(self, accounts, chosen: int | None) -> None:
        self.account.blockSignals(True)
        self.account.clear()
        for a in accounts:
            net = f" · {rules.RULES[a.platform.value].label}" if len(NET_PLATFORMS[self.key]) > 1 else ""
            st = " · API" if a.connection_status == "connected" else ""
            self.account.addItem(account_icon(a), a.name + net + st, a.id)
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

    def show_state(self, exported: bool, pub_id: int | None) -> None:
        """Page d'envoi possible une fois la vidéo exportée et un compte choisi ; état de la publication."""
        acc = self.account_id()
        r = self.view.app.publications.get(pub_id) if pub_id else None
        done = bool(r and r["status"] == "published")
        self.state.setText("✓ Publiée" if done else "À publier" if r else "")
        self.open_btn.setEnabled(bool(acc) and exported and not done)
        session = browser_login.session_state(self.view.app, acc) if acc else None
        api = bool(acc) and self.view.app.concepts.get_account(acc).connection_status == "connected"
        self.login_btn.setVisible(bool(acc) and not session and not api)
        if not acc:
            self.note.setText("")
        elif not exported:
            self.note.setText("Exporte d'abord la vidéo (à gauche), puis ouvre la page d'envoi.")
        elif session:
            self.note.setText(f"Navigateur dédié au compte ({browser_login.browser.browser_name(browser_login.browser.find_browser())}), "
                              "déjà connecté. Le clic final « Publier » reste le tien.")
        else:
            self.note.setText("Connecte ce compte une fois dans son navigateur dédié : il y restera connecté.")
        self.note.setVisible(bool(self.note.text()))
        self.link.set_publication(pub_id)

    def _login(self) -> None:
        acc = self.account_id()
        if not acc:
            return
        self.login = browser_login.BrowserLogin(self.view.app, acc, self)
        err = self.login.start()
        if err:
            self.note.setText(err)
            return
        self.login_btn.setEnabled(False)
        self.note.setText("Connecte-toi dans la fenêtre ouverte, puis ferme-la.")
        self.login.finished.connect(self._logged)

    def _logged(self, found) -> None:
        self.login_btn.setEnabled(True)
        self.view.refresh_cards()
        if not found:
            self.note.setText("Aucune session trouvée : la connexion n'a pas abouti." if found is False else
                              "Impossible de vérifier la connexion (navigateur encore ouvert ?).")


ACTIONS = ("draft", "local")
DRAFT_HELP = ("La vidéo est exportée puis envoyée sans être publiée sur chaque réseau qui a un compte : en privé sur "
              "YouTube, dans la boîte de réception TikTok, en brouillon sur la Page Facebook (comptes connectés par "
              "API). Les autres comptes, et Instagram, qui n'a pas de brouillon : avec la page d'envoi, à droite.")


class ExportView(_View):
    """Onglet Export, en deux colonnes : à gauche la vidéo (nom du fichier, titre, description, petit aperçu) et ce
    qu'on en fait ; à droite l'Atelier de publication — la vidéo à glisser, puis une carte par réseau."""

    def __init__(self, app: AppContext, host, export_dir: Path, parent=None):
        super().__init__(app, host, parent)
        self.export_dir = export_dir
        self._loading = False
        self.open_url = lambda u: QDesktopServices.openUrl(QUrl(u))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        root = QHBoxLayout(content)
        root.setContentsMargins(26, 12, 26, 10)
        root.setSpacing(18)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setWidget(content)
        outer.addWidget(sc)
        # -- colonne de gauche : la vidéo, puis que faire de la vidéo
        left = QVBoxLayout()
        left.setSpacing(14)
        top = QFrame()
        top.setProperty("role", "card")
        tl_ = QHBoxLayout(top)
        tl_.setContentsMargins(14, 14, 16, 14)
        tl_.setSpacing(16)
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
        self.title.textChanged.connect(self._changed)
        EmojiButton(self.title)
        fl.addWidget(self.title)
        fl.addWidget(label("Description", "muted", hint=False))
        self.desc = QPlainTextEdit()
        self.desc.setPlaceholderText("Description, hashtags…")
        self.desc.setFixedHeight(96)
        self.desc.textChanged.connect(self._changed)
        EmojiButton(self.desc)
        fl.addWidget(self.desc)
        self.count = label("", "faint", wrap=True, hint=False)
        fl.addWidget(self.count)
        fl.addStretch(1)
        tl_.addLayout(fl, 1)
        self.player = MontagePlayer()                     # petit aperçu, à droite des champs
        self.player.note.setMaximumHeight(0)
        self.player.set_compact()
        self.player.track_resolver = lambda aid: self.host._tracks_for(aid, self.player.tl)   # voix doublée
        self.player.setFixedSize(180, 300)
        tl_.addWidget(self.player, 0, Qt.AlignTop)
        left.addWidget(top)
        # que faire de la vidéo : brouillon sur les réseaux, ou seulement sur le PC
        act = QFrame()
        act.setProperty("role", "card")
        al = QVBoxLayout(act)
        al.setContentsMargins(16, 12, 16, 14)
        al.setSpacing(8)
        al.addWidget(label("Que faire de la vidéo ?", "h2"))
        self.actions = QButtonGroup(self)
        self.act_draft = QRadioButton("Brouillon sur les réseaux")
        self.act_draft.setToolTip(DRAFT_HELP)
        self.act_local = QRadioButton("Sur mon PC seulement")
        self.act_local.setToolTip("Enregistre la vidéo dans le dossier choisi, sans rien publier ; la publier ensuite "
                                  "avec la page d'envoi de chaque réseau, à droite")
        for i, b in enumerate((self.act_draft, self.act_local)):
            self.actions.addButton(b, i)
            al.addWidget(b)
        self.actions.idToggled.connect(lambda _i, on: on and self._changed())
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
        al.addLayout(_row(self.open_dir, "stretch"))
        left.addWidget(act)
        left.addStretch(1)
        lw = QWidget()
        lw.setLayout(left)
        root.addWidget(lw, 5)
        # -- colonne de droite : l'Atelier de publication
        right = QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(label("Atelier de publication", "h2"))
        vr = QHBoxLayout()
        vr.setSpacing(8)
        self.tile = VideoTile("")
        vr.addWidget(self.tile)
        self.reveal_btn = button("Afficher dans l'Explorateur", "ghost", icon="folder-open",
                                 on_click=lambda: reveal(self._last_path()))
        vr.addWidget(self.reveal_btn, 0, Qt.AlignVCenter)
        vr.addStretch(1)
        right.addLayout(vr)
        self.nets: dict[str, NetworkCard] = {}
        for key, name in NETWORKS:
            c = NetworkCard(self, key, name)
            self.nets[key] = c
            right.addWidget(c)
        right.addStretch(1)
        rw = QWidget()
        rw.setLayout(right)
        root.addWidget(rw, 4)

    # -- état enregistré dans le projet ---------------------------------------------------
    def _state(self) -> dict:
        st = {"title": "", "description": "", "action": "local", "nets": {}, "file_name": "", "folder": ""}
        if self.pid:
            st.update(self.app.projects.load_data(self.pid).get("export_ui") or {})
        if st["action"] not in ACTIONS:                   # anciens choix (publier, programmer) : sur le PC
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
              "file_name": self.file_name.text().strip(), "folder": self.folder,
              "nets": {k: {"account": c.account_id()} for k, c in self.nets.items()}}
        data = self.app.projects.load_data(self.pid)
        data["export_ui"] = st
        self.app.projects.save_data(self.pid, data)
        self._update_count()
        self._update_go()

    def _update_count(self) -> None:
        """Le texte commun respecte-t-il les règles de chaque réseau choisi ?"""
        problems = []
        for k, c in self.nets.items():
            acc = c.account_id()
            if acc:
                pf = self.app.concepts.get_account(acc).platform.value
                problems += rules.check(pf, self.title.text(), self.desc.toPlainText(), "")
        n = len(self.desc.toPlainText())
        self.count.setText(f"{n} caractères" + (f"  ·  ⚠ {problems[0]}" if problems else ""))

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
        """« Ajouter un compte » (ou « Connecter ce compte… ») : le compte, son navigateur dédié et, replié, la
        connexion par API (`account_setup.py`)."""
        from .account_setup import AccountSetupDialog
        dlg = AccountSetupDialog(self.app, platform, account_id, self)
        if not dlg.exec():
            return
        key = next(k for k, pfs in NET_PLATFORMS.items() if platform in pfs)
        data = self.app.projects.load_data(self.pid)
        st = data.setdefault("export_ui", {})
        st.setdefault("nets", {}).setdefault(key, {}).update({"account": dlg.account_id})
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
            for k, c in self.nets.items():
                acc = [a for a in accounts if a.platform.value in NET_PLATFORMS[k]]
                acc.sort(key=lambda a: (a.id not in linked, a.name.lower()))
                c.fill_accounts(acc, (st["nets"].get(k) or {}).get("account"))
            (self.act_draft if st["action"] == "draft" else self.act_local).setChecked(True)
        finally:
            self._loading = False
        self.player.set_timeline(self.version_timeline(), self.host.assets)
        self._update_count()
        self._update_status()
        self._update_go()

    def version_timeline(self) -> Timeline:
        """Le montage du projet ouvert (une version traduite a le sien, avec ses sous-titres et sa voix)."""
        return self.host.tl

    def hideEvent(self, e) -> None:
        self.player.pause()
        super().hideEvent(e)

    def _update_go(self) -> None:
        if not self.pid:
            return
        running = _active_job(self.app, "export_timeline", "project_id", self.pid)
        pending = self.app.projects.load_data(self.pid).get("export_pending")
        busy = bool(running and pending)
        nets = [c.name for c in self.nets.values() if c.account_id()]
        local = self.actions.checkedId() == ACTIONS.index("local")
        if local:
            self.go.setText("Exporter sur mon PC")
        else:
            self.go.setText("Exporter en brouillon" + (f"  ·  {', '.join(nets)}" if nets else ""))
        tl = self.version_timeline()
        self.go.setEnabled(not busy and bool(tl.segments) and (local or bool(nets)))
        self.go.setToolTip("" if local or nets else "Choisis au moins un compte, à droite")
        self.progress.setVisible(busy)
        if busy:
            self.progress.setValue(int((running["progress"] or 0) * 100))

    def _last(self) -> dict:
        return (self.app.projects.load_data(self.pid).get("export_last") or {}) if self.pid else {}

    def _last_path(self) -> str:
        return self._last().get("path") or ""

    def _update_status(self) -> None:
        data = self.app.projects.load_data(self.pid)
        last = data.get("export_last")
        if data.get("export_pending") and _active_job(self.app, "export_timeline", "project_id", self.pid):
            self.status.setText("Export de la vidéo en cours… La suite (brouillons sur les réseaux) se fera ensuite "
                                "d'elle-même.")
        elif last:
            self.status.setText(last.get("summary", ""))
        else:
            self.status.setText("")
        path = self._last_path()
        self.open_dir.setVisible(bool(path) and Path(path).exists())
        self.refresh_cards()

    def refresh_cards(self) -> None:
        """La vidéo à glisser (le dernier export) et l'état de chaque réseau."""
        if not self.pid:
            return
        path = self._last_path()
        exported = bool(path) and Path(path).exists()
        self.tile.set_video(path if exported else "")
        self.reveal_btn.setEnabled(exported)
        pubs = self._last().get("pubs") or {}
        for k, c in self.nets.items():
            pid = pubs.get(k)
            r = self.app.publications.get(pid) if pid else None
            c.show_state(exported, pid if r is not None and r["account_id"] == c.account_id() else None)

    # -- page d'envoi (Atelier de publication) ---------------------------------------------
    def _publication_for(self, key: str) -> int | None:
        """La publication du dernier export pour ce réseau et ce compte, créée au besoin (elle sert à retrouver
        le lien de la vidéo une fois publiée, et aux statistiques)."""
        c = self.nets[key]
        acc_id = c.account_id()
        data = self.app.projects.load_data(self.pid)
        last = data.get("export_last") or {}
        if not acc_id or not last.get("path"):
            return None
        pubs = last.setdefault("pubs", {})
        r = self.app.publications.get(pubs[key]) if pubs.get(key) else None
        if r is not None and r["account_id"] == acc_id and r["status"] != "cancelled":
            return r["id"]
        ps = self.app.publications
        mid = last.get("master_id")
        if not mid or ps.master(mid) is None:
            p = self.app.projects.get(self.pid)
            mid = ps.create_master(p["concept_id"], self.title.text().strip(), self.desc.toPlainText().strip(), "",
                                   language=data.get("version_lang") or "fr", variant_id=last.get("variant_id"))
            last["master_id"] = mid
        ps.set_destinations(mid, [acc_id], confirmed_external={acc_id})
        pub = next(pb for pb in ps.list_for_master(mid) if pb.account.id == acc_id)
        t = compose_for(pub.account.platform.value, self.title.text(), self.desc.toPlainText())
        ps.override(pub.id, title=t["title"], description=t["text"])
        pubs[key] = pub.id
        data["export_last"] = last
        self.app.projects.save_data(self.pid, data)
        return pub.id

    def open_upload(self, key: str) -> None:
        """Ouvre la page d'envoi du réseau dans le navigateur dédié du compte et copie son texte ; le clic final
        reste à l'utilisateur, le lien est ensuite retrouvé tout seul (ou collé)."""
        c = self.nets[key]
        pid = self._publication_for(key)
        if pid is None:
            return
        r = self.app.publications.get(pid)
        if r["status"] not in ("published", "cancelled", "manual_action"):
            self.app.publications.mark_manual(pid, "Envoyée depuis l'Atelier de publication : lien à confirmer.")
        t = compose_for(r["platform"], self.title.text(), self.desc.toPlainText())
        QApplication.clipboard().setText(t["title"] or t["text"])
        note = browser_login.open_page(self.app, r["account_id"], upload_url(key), self.open_url)
        self.refresh_cards()
        c.note.setText(note or f"Page d'envoi ouverte ; {'titre' if t['title'] else 'texte'} copié dans le "
                       "presse-papiers. Glisse la vidéo (en haut) dans la page.")
        c.note.show()

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
        nets = {}
        if a == "draft":
            for k, c in self.nets.items():
                acc = c.account_id()
                if acc:
                    pf = self.app.concepts.get_account(acc).platform.value
                    t = compose_for(pf, st["title"], st["description"])
                    problems = rules.check(pf, t["title"], t["text"], "")
                    if problems:
                        QMessageBox.warning(self, "Export", f"{c.name} : " + "\n".join(problems))
                        return
                    nets[k] = {"account": acc, "title": t["title"], "text": t["text"]}
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
        data["export_pending"] = {"job": jid, "action": a, "title": st["title"], "description": st["description"],
                                  "language": lang, "nets": nets}
        self.app.projects.save_data(self.pid, data)
        self.status.setText("Export de la vidéo en cours…")
        self.open_dir.hide()
        self._update_go()

    def finish(self, pid: int, job) -> None:
        """Export terminé : sur le PC seulement, rien d'autre ; brouillon : envoi sans publier aux comptes
        connectés par API, les autres avec la page d'envoi (à droite)."""
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
        variant = (job.result or {}).get("variant_id")
        name = Path(path).name
        if pend["action"] != "draft" or not pend["nets"]:
            data["export_last"] = {"summary": f"✓ « {name} » enregistrée dans « {Path(path).parent} ».",
                                   "path": path, "variant_id": variant}
            self.app.projects.save_data(pid, data)
            return
        p = self.app.projects.get(pid)
        ps = self.app.publications
        mid = ps.create_master(p["concept_id"], pend["title"], pend["description"], "",
                               language=pend.get("language") or "fr", variant_id=variant)
        accounts = {v["account"]: k for k, v in pend["nets"].items()}
        notes, pubs = [], {}
        ps.set_destinations(mid, list(accounts), confirmed_external=set(accounts))
        for pub in ps.list_for_master(mid):
            k = accounts.get(pub.account.id)
            v = pend["nets"].get(k) or {}
            pubs[k] = pub.id
            ps.override(pub.id, title=v.get("title", ""), description=v.get("text", ""))
            label_ = rules.RULES[pub.account.platform.value].label
            if pub.account.platform.value == "instagram":
                ps.mark_manual(pub.id, "Instagram ne permet pas d'envoyer un brouillon : publication à faire à la main.")
                notes.append(f"{label_} : pas de brouillon possible, à publier avec la page d'envoi")
            elif pub.account.connection_status == "connected":
                ps.set_status(pub.id, PublicationStatus.UPLOADING)
                self.app.jobs.submit("publish_upload", f"{label_} · {pend['title'] or 'sans titre'} · brouillon",
                                     {"publication_id": pub.id, "now": True, "draft": True}, critical=True)
            else:
                ps.mark_manual(pub.id, "Sans connexion par API : à publier avec la page d'envoi.")
                notes.append(f"{label_} : sans connexion par API, à envoyer avec la page d'envoi")
        summary = f"✓ « {name} » exportée, envoi en brouillon lancé (suivi dans les tâches)." \
            if len(notes) < len(accounts) else f"✓ « {name} » exportée."
        if notes:
            summary += "\n" + "\n".join(notes)
        data["export_last"] = {"summary": summary, "path": path, "variant_id": variant, "master_id": mid,
                               "pubs": pubs}
        self.app.projects.save_data(pid, data)

    def on_job(self, job) -> None:
        pid = job.params.get("project_id")
        if job.kind == "export_timeline" and job.status in ("succeeded", "failed", "cancelled") and pid \
                and self.app.projects.get(pid):
            self.finish(pid, job)
        if job.kind in ("match_published", "publish_upload") and job.status == "succeeded" and self.pid:
            self.refresh_cards()                          # lien retrouvé, brouillon envoyé
        if not self.pid or pid != self.pid:
            return
        if job.kind == "export_timeline":
            if job.status in ("pending", "running"):
                self.progress.setValue(int((job.progress or 0) * 100))
                return
            self._update_status()
            self._update_go()
