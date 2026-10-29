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
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListView, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit,
                               QProgressBar, QScrollArea, QStyle, QStyledItemDelegate, QToolButton,
                               QVBoxLayout, QWidget, QWidgetAction)

from ..core import AppContext
from ..edit.model import Timeline
from ..publish import reconcile, rules
from . import browser_login, theme
from .montage_player import MontagePlayer
from .concepts_box import ConceptsBox, concept_account
from .publish_workshop import LinkBox, VideoTile, reveal, upload_url
from .simple_views import _View, _active_job, _open_folder, _row
from .widgets import button, label, tool
from .widgets import ask
from .widgets import dot_icon

# réseaux de l'onglet Export : (clé, nom affiché) ; Meta réunit Facebook et Instagram (une page d'envoi)
NETWORKS = [("youtube", "YouTube"), ("tiktok", "TikTok"), ("meta", "Meta")]
EMOJI_FILE = Path(__file__).parent / "assets" / "emojis.json"


def compose_for(platform: str, title: str, description: str) -> dict:
    """Texte d'un réseau à partir du texte commun : {"title": … (YouTube seulement), "text": …}."""
    return rules.compose(platform, title, description, "")


def safe_name(name: str) -> str:
    """Nom de fichier valide sous Windows (sans extension)."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", name).strip(" .")[:120] or "video"


def date_prefix(when=None) -> str:
    """Début de chaque fichier exporté : la date et l'heure de l'export (« 2026-10-28 10h32 »)."""
    from datetime import datetime
    return (when or datetime.now()).strftime("%Y-%m-%d %Hh%M")


def base_name(name: str) -> str:
    """Le nom seul : sans date, heure ni concept en tête (« 2026-10-28 10h32 - Concept - Nom » → « Nom »)."""
    base = re.sub(r"^\d{4}-\d{2}-\d{2}(?:[ _-]+\d{1,2}h\d{2})?(?:\s*-\s*)?", "", safe_name(name))
    return base.strip() or "video"


def file_name(name: str, when=None, concept: str = "") -> str:
    """Nom du fichier exporté : « 2026-10-28 10h32 - Concept - Nom.mp4 » (sans concept : « … 10h32 - Nom.mp4 »).
    Une date, une heure ou un concept déjà en tête ne sont pas répétés."""
    base = base_name(name)
    concept = safe_name(concept) if concept else ""
    if concept and base.startswith(concept + " - "):
        base = base[len(concept) + 3:].strip() or "video"
    return " - ".join(x for x in (date_prefix(when), concept, base) if x) + ".mp4"


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
E, CELL = 16, 22                                    # emojis petits : deux fois moins de place


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
            pm = QPixmap(int(E * dpr), int(E * dpr))
            pm.fill(Qt.transparent)
            q = QPainter(pm)
            f = QFont(opt.font)
            f.setPixelSize(int(E * 0.72 * dpr))
            q.setFont(f)
            q.drawText(QRectF(0, 0, E * dpr, E * dpr), Qt.AlignCenter, e)
            q.end()
            pm.setDevicePixelRatio(dpr)
            _emoji_pix[(e, dpr)] = pm
        r = opt.rect
        p.drawPixmap(r.x() + (r.width() - E) // 2, r.y() + (r.height() - E) // 2, pm)

    def sizeHint(self, _opt, _index) -> QSize:
        return QSize(CELL, CELL)


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
        self.list.setGridSize(QSize(CELL, CELL))
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


class NetworkCard(QFrame):
    """Un réseau dans le rectangle de publication : logo et chaîne du concept, état ; dessous, le lien de la vidéo
    une fois publiée. Visible seulement une fois la page d'envoi ouverte (les logos du menu suffisent sinon)."""

    def __init__(self, view: "ExportView", key: str, name: str):
        super().__init__()
        self.view, self.key, self.name = view, key, name
        self.acc_id: int | None = None
        self.setObjectName("netRow")
        self.setStyleSheet("QFrame#netRow { background: transparent; border: none; }")    # sur le rectangle
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 2)
        v.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.icon = QLabel()
        self.icon.setPixmap(theme.platform_icon(key, 18).pixmap(QSize(18, 18)))
        self.icon.setToolTip(name)
        head.addWidget(self.icon)
        self.channel = label("", None, hint=False)
        self.channel.setStyleSheet("font-weight: 700;")
        head.addWidget(self.channel, 1)
        self.state = label("", "muted", hint=False)
        head.addWidget(self.state)
        v.addLayout(head)
        self.link = LinkBox(view.app, view.open_url)
        self.link.changed.connect(self.view.refresh_cards)
        v.addWidget(self.link)

    def account_id(self) -> int | None:
        return self.acc_id

    def set_account(self, acc) -> None:
        """La chaîne du concept sur ce réseau (None : le concept n'en a pas, la ligne est masquée)."""
        self.acc_id = acc.id if acc else None
        if acc:
            net = f"  ·  {rules.RULES[acc.platform.value].label}" if self.key == "meta" else ""
            self.channel.setText(acc.name + net)
            self.channel.setToolTip(acc.public_url or acc.name)
        self.setVisible(False)                            # les logos, à droite du menu, suffisent

    def show_state(self, exported: bool, pub_id: int | None) -> None:
        """État de la publication (à publier, publiée) et son lien."""
        r = self.view.app.publications.get(pub_id) if pub_id else None
        done = bool(r and r["status"] == "published")
        self.state.setText("✓ Publiée" if done else "À publier" if r else "")
        self.link.set_publication(pub_id)
        self.setVisible(bool(self.acc_id) and r is not None)      # seulement une fois la page d'envoi ouverte


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
        page = QVBoxLayout(content)
        page.setContentsMargins(26, 12, 26, 10)
        page.setSpacing(18)
        root = QHBoxLayout()
        root.setSpacing(34)                               # de l'air entre les deux colonnes
        page.addLayout(root)
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
        tl_ = QHBoxLayout()
        tl_.setContentsMargins(0, 0, 0, 0)
        tl_.setSpacing(16)
        fl = QVBoxLayout()
        fl.setSpacing(6)
        self.file_name = QLineEdit()
        self.file_name.setPlaceholderText("Nom du fichier exporté")
        self.file_name.setToolTip("Nom du fichier exporté")
        self.file_name.textChanged.connect(self._changed)
        self.date_lbl = label(date_prefix(), "muted", hint=False)        # le fichier commence par la date du jour
        self.date_lbl.setToolTip("Le nom du fichier commence par la date et l'heure de l'export")
        fl.addLayout(_row(self.date_lbl, self.file_name, label(".mp4", "muted", hint=False), spacing=4))
        self.folder = str(export_dir)
        self.folder_btn = tool("folder", "Dossier où enregistrer la vidéo", self._pick_folder)    # chemin au survol
        fl.itemAt(fl.count() - 1).layout().insertWidget(0, self.folder_btn)
        self.text = QPlainTextEdit()                      # titre (1re ligne), description, hashtags
        self.text.setPlaceholderText("Texte")
        self.text.setToolTip("Texte : la première ligne sert de titre (YouTube), le reste de description")
        self.text.setFixedHeight(150)
        self.text.textChanged.connect(self._changed)
        EmojiButton(self.text)
        fl.addWidget(self.text)
        self.count = QLabel(self.text)                    # dans le rectangle, en bas à droite
        self.count.setProperty("role", "faint")
        self.count.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.text.installEventFilter(self)
        fl.addStretch(1)
        tl_.addLayout(fl, 1)
        self.player = MontagePlayer()                     # petit aperçu, à droite des champs
        self.player.note.setMaximumHeight(0)
        self.player.set_compact()
        self.player.track_resolver = lambda aid: self.host._tracks_for(aid, self.player.tl)   # voix doublée
        self.player.setFixedSize(180, 300)
        tl_.addWidget(self.player, 0, Qt.AlignTop)
        box = QVBoxLayout(top)
        box.setContentsMargins(14, 14, 16, 12)
        box.setSpacing(8)
        box.addLayout(tl_)
        # en bas du rectangle : Exporter, le dossier (chemin au survol), ce qui s'est passé, le calendrier
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setFixedHeight(6)
        self.progress.hide()
        box.addWidget(self.progress)
        self.go = button("Exporter", "soft", icon="download", on_click=self._go)
        self.open_dir = tool("folder-open", "Ouvrir le dossier",
                             lambda: _open_folder(Path(self._last_path()).parent))
        self.status = label("", "muted", hint=False)
        self.status.setWordWrap(True)
        self.plan_btn = button("Mettre dans le calendrier", "ghost", icon="calendar-days", on_click=self.plan,
                               tooltip="Programmer cette vidéo dans le calendrier")
        box.addLayout(_row(self.go, self.open_dir, self.status, "stretch", self.plan_btn, spacing=8))
        left.addWidget(top)
        left.addStretch(1)
        lw = QWidget()
        lw.setLayout(left)
        root.addWidget(lw, 5)
        # -- colonne de droite : l'Atelier de publication
        right = QVBoxLayout()
        right.setSpacing(12)
        vr = QHBoxLayout()
        vr.setSpacing(6)
        self.tile = VideoTile("")
        vr.addWidget(self.tile)
        self.reveal_btn = tool("folder-open", "Afficher dans l'Explorateur", lambda: reveal(self._last_path()))
        vr.addWidget(self.reveal_btn, 0, Qt.AlignVCenter)
        vr.addStretch(1)
        right.addLayout(vr)
        pub = QFrame()                                    # concept, réseaux, pages d'envoi : un seul rectangle
        pub.setProperty("role", "card")
        pl = QVBoxLayout(pub)
        pl.setContentsMargins(14, 10, 14, 12)
        pl.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(4)
        self.concept = QComboBox()
        self.concept.setToolTip("Concept")
        self.concept.setPlaceholderText("Concept")
        self.concept.currentIndexChanged.connect(self._concept_changed)
        head.addWidget(self.concept)
        head.addStretch(1)
        self.net_icons: dict[str, QLabel] = {}
        for key, name in NETWORKS:
            ic = QLabel()
            ic.setFixedSize(24, 24)
            ic.setAlignment(Qt.AlignCenter)
            head.addWidget(ic)
            self.net_icons[key] = ic
        pl.addLayout(head)
        self.nets: dict[str, NetworkCard] = {}
        for key, name in NETWORKS:
            c = NetworkCard(self, key, name)
            self.nets[key] = c
            pl.addWidget(c)
        self.open_all = button("Ouvrir les pages d'envoi", None, icon="upload", on_click=self.open_pages,
                               tooltip="Ouvre la page d'envoi de chaque chaîne du concept, chacune dans un nouvel "
                               "onglet de ton navigateur, et copie le texte : glisse la vidéo, colle le texte, "
                               "clique toi-même sur « Publier »")
        pl.addWidget(self.open_all, 0, Qt.AlignLeft)
        self.pub_note = label("", "faint", wrap=True, hint=False)
        self.pub_note.hide()
        pl.addWidget(self.pub_note)
        right.addWidget(pub)
        # -- sous les réseaux : les concepts et leurs chaînes
        right.addSpacing(10)
        self.concepts = ConceptsBox(app)
        self.concepts.changed.connect(self._concepts_edited)
        right.addWidget(self.concepts)
        right.addSpacing(10)
        right.addStretch(1)
        rw = QWidget()
        rw.setLayout(right)
        root.addWidget(rw, 4)
        page.addStretch(1)

    # -- état enregistré dans le projet ---------------------------------------------------
    def _state(self) -> dict:
        st = {"title": "", "description": "", "file_name": "", "folder": ""}
        if self.pid:
            st.update(self.app.projects.load_data(self.pid).get("export_ui") or {})
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
        st = {"title": self._title(), "description": self._desc(),
              "file_name": self.file_name.text().strip(), "folder": self.folder}
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
                problems += rules.check(pf, self._title(), self._desc(), "")
        n = len(self.text.toPlainText())
        self.count.setText(f"{n} caractère{'s' if n > 1 else ''}" + (f"  ·  ⚠ {problems[0]}" if problems else ""))
        self.count.setToolTip(problems[0] if problems else "")
        self._place_count()

    def _title(self) -> str:
        """Le titre : la première ligne du texte."""
        return (self.text.toPlainText().strip().splitlines() or [""])[0].strip()

    def _desc(self) -> str:
        """La description : le texte après la première ligne."""
        return "\n".join(self.text.toPlainText().strip().splitlines()[1:]).strip()

    def _place_count(self) -> None:
        self.count.adjustSize()
        self.count.move(self.text.width() - self.count.width() - 10, self.text.height() - self.count.height() - 6)
        self.count.raise_()

    def eventFilter(self, obj, e) -> bool:
        if obj is self.text and e.type() == QEvent.Resize:
            self._place_count()
        return super().eventFilter(obj, e)

    def _pick_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Dossier de la vidéo exportée", self.folder)
        if d:
            self.folder = d
            self._show_folder()
            self._changed()

    def _show_folder(self) -> None:
        self.folder_btn.setToolTip(f"Dossier où enregistrer la vidéo :\n{self.folder}")

    def _fill_concepts(self, chosen: int | None) -> None:
        """Les concepts (« Brouillons », celui des vidéos pas encore rangées, est caché) ; celui de la vidéo choisi."""
        self.concept.blockSignals(True)
        self.concept.clear()
        for c in self.app.concepts.user_concepts():
            self.concept.addItem(dot_icon(c.color), c.name, c.id)
        self.concept.setCurrentIndex(self.concept.findData(chosen))
        self.concept.blockSignals(False)
        self._show_channels()
        self._show_prefix()

    def _concept_name(self) -> str:
        return self.concept.currentText() if self.concept.currentData() else ""

    def _show_prefix(self) -> None:
        """Devant le nom du fichier : la date, l'heure et le concept de la vidéo."""
        c = self._concept_name()
        self.date_lbl.setText(f"{date_prefix()} - {c} -" if c else f"{date_prefix()} -")

    def _show_channels(self) -> None:
        """Chaque réseau reçoit la chaîne du concept choisi."""
        cid = self.concept.currentData()
        for k, c in self.nets.items():
            c.set_account(concept_account(self.app, cid, k) if cid else None)

    def _concept_changed(self) -> None:
        """Le concept choisi ici est celui de la vidéo (le même que dans la barre du haut)."""
        cid = self.concept.currentData()
        if cid and self.pid:
            self.app.projects.set_concept(self.pid, cid)
            self.host.concept_changed()
        self._show_channels()
        self._show_prefix()
        self._changed()
        self.refresh_cards()

    def _concepts_edited(self) -> None:
        """Un concept ou une chaîne modifiés (en bas) : menu des concepts et cartes à jour."""
        self._fill_concepts(self.concept.currentData())
        self.refresh_cards()
        self._update_go()

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
            text = "\n".join(x for x in (title, st["description"]) if x)
            if self.text.toPlainText().strip() != text.strip():
                self.text.setPlainText(text)
            p = self.app.projects.get(self.pid)
            self._fill_concepts(p["concept_id"] if p else None)
            self.concepts.refresh()
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
        tl = self.version_timeline()
        self.go.setEnabled(not busy and bool(tl.segments))
        self.go.setToolTip("Enregistre la vidéo dans le dossier choisi" if tl.segments else "Rien à exporter")
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
            self.status.setText("Export de la vidéo en cours…")
        elif last:
            self.status.setText(last.get("summary", ""))
        else:
            self.status.setText("")
        path = self._last_path()
        self.open_dir.setVisible(bool(path) and Path(path).exists())
        self.open_dir.setToolTip(f"Ouvrir le dossier :\n{Path(path).parent}" if path else "")
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
        for k, name in NETWORKS:                          # logos : en couleur si le concept a une chaîne
            acc = self.nets[k].account_id()
            ic = theme.platform_icon(k, 18) if acc else theme.icon(theme.PLATFORMS[k][0], theme.color("faint"), 18)
            self.net_icons[k].setPixmap(ic.pixmap(QSize(18, 18)))
            self.net_icons[k].setToolTip(f"{name} : {self.app.concepts.get_account(acc).name}" if acc else
                                         f"{name} : pas de chaîne dans ce concept (ajoute-la dans Concepts)")
        has = any(c.account_id() for c in self.nets.values())
        self.open_all.setEnabled(exported and has)
        self.open_all.setToolTip("" if exported and has else "Exporte d'abord la vidéo (à gauche)" if has else
                                 "Ce concept n'a pas encore de chaîne : ajoute-la dans Concepts, en dessous")
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
            mid = ps.create_master(p["concept_id"], self._title(), self._desc(), "",
                                   language=data.get("version_lang") or "fr", variant_id=last.get("variant_id"))
            last["master_id"] = mid
        ps.set_destinations(mid, [acc_id], confirmed_external={acc_id})
        pub = next(pb for pb in ps.list_for_master(mid) if pb.account.id == acc_id)
        t = compose_for(pub.account.platform.value, self._title(), self._desc())
        ps.override(pub.id, title=t["title"], description=t["text"])
        pubs[key] = pub.id
        data["export_last"] = last
        self.app.projects.save_data(self.pid, data)
        return pub.id

    def open_upload(self, key: str) -> str:
        """Page d'envoi d'un réseau, dans un nouvel onglet ; renvoie un message d'erreur, ou ""."""
        pid = self._publication_for(key)
        if pid is None:
            return ""
        r = self.app.publications.get(pid)
        if r["status"] not in ("published", "cancelled", "manual_action"):
            self.app.publications.mark_manual(pid, "Envoyée depuis l'Atelier de publication : lien à confirmer.")
        url = upload_url(key)
        if key == "youtube":                              # YouTube Studio directement sur la chaîne du concept
            ch = self.youtube_channel(r["account_id"])
            if ch:
                url = reconcile.youtube_upload_url(ch)
        return browser_login.open_page(self.app, r["account_id"], url, self.open_url)

    def open_pages(self) -> list[str]:
        """« Ouvrir les pages d'envoi » : un onglet par chaîne du concept (YouTube, TikTok, Meta) en un clic ; le
        texte (titre puis description) est copié pour être collé dans chaque page."""
        keys = [k for k, c in self.nets.items() if c.account_id()
                and not (c.state.text().startswith("✓"))]
        notes = [n for n in (self.open_upload(k) for k in keys) if n]
        QApplication.clipboard().setText(self.text.toPlainText().strip())
        self.refresh_cards()
        names = ", ".join(self.nets[k].name for k in keys)
        self.pub_note.setText(" ".join(notes) or (f"Pages d'envoi ouvertes ({names}) ; texte copié." if keys else
                                                  "Toutes les vidéos de ce concept sont déjà publiées."))
        self.pub_note.show()
        return keys

    def youtube_channel(self, account_id: int) -> str:
        """Identifiant de la chaîne YouTube du compte (lu une fois sur sa page publique, puis gardé)."""
        acc = self.app.concepts.get_account(account_id)
        saved = self.app.concepts.account_settings(account_id).get("channel") or {}
        if saved.get("url") == acc.public_url:
            return saved.get("id") or ""
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ch = reconcile.youtube_channel_id(acc.public_url)
        finally:
            QApplication.restoreOverrideCursor()
        self.app.concepts.update_account_settings(account_id, channel={"url": acc.public_url, "id": ch})
        return ch

    # -- action -------------------------------------------------------------------------
    def _destination(self, st: dict) -> Path | None:
        """Fichier de sortie : nom et dossier choisis ; un fichier du même nom n'est remplacé qu'avec accord."""
        folder = Path(st["folder"] or self.export_dir)
        dest = folder / file_name(st['file_name'] or self._default_name(), concept=self._concept_name())
        if dest.exists() and not ask(
                self, "Exporter", f"« {dest.name} » existe déjà dans ce dossier.\n\nLe remplacer ?"):
            return None
        return dest

    def _go(self) -> None:
        tl = self.version_timeline()
        if not self.pid or not tl.segments:
            return
        self.host.flush()
        st = self._state()
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
        data["export_pending"] = {"job": jid, "title": st["title"], "description": st["description"],
                                  "language": lang, "name": base_name(st["file_name"] or self._default_name())}
        self.app.projects.save_data(self.pid, data)
        self.status.setText("Export de la vidéo en cours…")
        self.open_dir.hide()
        self._update_go()

    def finish(self, pid: int, job) -> None:
        """Export terminé : la vidéo prend le nom du fichier (sans date, heure ni concept)."""
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
        data["export_last"] = {"summary": f"✓ « {Path(path).name} » enregistrée dans « {Path(path).parent} ».",
                               "path": path, "variant_id": variant}
        self.app.projects.save_data(pid, data)
        name = pend.get("name") or base_name(Path(path).stem)
        p = self.app.projects.get(pid)
        if p and name and p["name"] != name:
            try:
                self.app.projects.rename(pid, name)
                self.host.refresh()
            except ValueError:
                pass

    def plan(self):
        """« Mettre dans le calendrier » : le rectangle de nouvelle publication, avec cette vidéo, son concept et
        son texte."""
        from .pages.calendar import PlanPopover
        self.pop = PlanPopover(self.app, concept_id=self.concept.currentData(), project_id=self.pid,
                               text=self.text.toPlainText().strip(), parent=self)
        self.pop.show_at(self.plan_btn)
        return self.pop

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
