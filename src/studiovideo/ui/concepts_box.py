"""Concepts et leurs chaînes, dans l'onglet Export (sous le reste) : une liste qu'on fait défiler.

Chaque concept : nom, thématique, couleur, langue, puis trois petits logos — YouTube, TikTok, Meta (Facebook ou
Instagram). Un logo en couleur : la chaîne du concept sur ce réseau ; grisé : pas encore de chaîne, un clic ouvre un
rectangle volant pour l'ajouter (nom, adresse publique ; connexion par API dans « Avancé »). « Nouveau concept »
ajoute une ligne dont le nom s'écrit tout de suite.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLineEdit, QMessageBox, QScrollArea, QToolButton,
                               QVBoxLayout, QWidget)

from ..core import AppContext
from ..domain.models import Platform
from ..services.concepts import ConceptError
from . import theme
from .account_setup import URL_HINT
from .color_picker import ColorButton
from .dialogs import CONCEPT_COLORS, language_combo
from .widgets import Popover, button, label, tool

NETS = (("youtube", "YouTube", ("youtube",)), ("tiktok", "TikTok", ("tiktok",)),
        ("meta", "Meta", ("facebook", "instagram")))
COLS = (("Nom", 160), ("Thématique", 0), ("Couleur", 54), ("Langue", 110))


def concept_account(app: AppContext, concept_id: int, key: str):
    """La chaîne du concept sur ce réseau (Meta : Facebook, sinon Instagram), ou None."""
    pfs = dict((k, p) for k, _n, p in NETS)[key]
    accs = [a for a, _d in app.concepts.concept_accounts(concept_id) if a.platform.value in pfs]
    accs.sort(key=lambda a: pfs.index(a.platform.value))
    return accs[0] if accs else None


class ChannelPopover(Popover):
    """La chaîne d'un concept sur un réseau : nom, adresse publique ; retirer ; connexion par API (avancé)."""
    changed = Signal()

    def __init__(self, app: AppContext, concept_id: int, key: str, parent=None):
        super().__init__(parent)
        self.app, self.cid, self.key = app, concept_id, key
        self.acc = concept_account(app, concept_id, key)
        name = dict((k, n) for k, n, _p in NETS)[key]
        self.setFixedWidth(380)
        self.body.addWidget(label(f"{name} · {app.concepts.get_concept(concept_id).name}", "h2"))
        self.platform = QComboBox()
        for p in dict((k, ps) for k, _n, ps in NETS)[key]:
            self.platform.addItem(theme.platform_icon(p, 16), Platform(p).label, p)
        if self.acc:
            self.platform.setCurrentIndex(max(0, self.platform.findData(self.acc.platform.value)))
        self.platform.setVisible(self.platform.count() > 1)
        self.platform.currentIndexChanged.connect(self._hint)
        self.body.addWidget(self.platform)
        self.name = QLineEdit(self.acc.name if self.acc else "")
        self.name.setPlaceholderText("Nom de la chaîne")
        self.body.addWidget(self.name)
        self.url = QLineEdit(self.acc.public_url if self.acc else "")
        self.url.setToolTip("Adresse de la page publique : la page d'envoi s'ouvre sur cette chaîne (YouTube), le lien "
                            "des vidéos publiées et leurs chiffres y sont retrouvés")
        self.body.addWidget(self.url)
        self._hint()
        foot = QHBoxLayout()
        if self.acc:
            foot.addWidget(button("Retirer", "ghost", icon="trash-2", on_click=self._remove,
                                  tooltip="Retirer cette chaîne du concept"))
        adv = button("Avancé : API", "ghost", icon="plug-zap", on_click=self._advanced,
                     tooltip="Connexion par API (application développeur du réseau) : facultative")
        foot.addWidget(adv)
        foot.addStretch(1)
        foot.addWidget(button("Enregistrer", "primary", on_click=self._save))
        self.body.addLayout(foot)
        self.msg = label("", "muted", wrap=True, hint=False)
        self.msg.hide()
        self.body.addWidget(self.msg)
        QTimer.singleShot(0, self.name.setFocus)

    def _hint(self) -> None:
        self.url.setPlaceholderText("Adresse publique, ex. " + URL_HINT.get(self.platform.currentData(), "https://…"))

    def _say(self, text: str) -> None:
        self.msg.setText(text)
        self.msg.show()

    def save(self) -> int | None:
        name, url, pf = self.name.text().strip(), self.url.text().strip(), self.platform.currentData()
        if not name:
            self._say("Donne un nom à la chaîne.")
            return None
        try:
            if self.acc and self.acc.platform.value == pf:
                self.app.concepts.update_account(self.acc.id, name=name, public_url=url)
                aid = self.acc.id
            else:
                if self.acc:                              # Meta : Facebook ↔ Instagram, nouvelle chaîne
                    self.app.concepts.unlink(self.cid, self.acc.id)
                lang = self.app.concepts.get_concept(self.cid).language
                aid = self.app.concepts.create_account(pf, name, url, lang).id
                self.app.concepts.link(self.cid, aid)
        except ConceptError as exc:
            self._say(str(exc))
            return None
        return aid

    def _save(self) -> None:
        if self.save() is not None:
            self.changed.emit()
            self.close()

    def _remove(self) -> None:
        self.app.concepts.unlink(self.cid, self.acc.id)
        self.changed.emit()
        self.close()

    def _advanced(self) -> None:
        aid = self.save()
        if aid is None:
            return
        self.changed.emit()
        self.close()
        from .account_setup import AccountSetupDialog
        AccountSetupDialog(self.app, self.platform.currentData(), aid, self.parent(), advanced=True).exec()
        self.changed.emit()


class NetButton(QToolButton):
    """Logo d'un réseau : en couleur si le concept y a une chaîne, grisé sinon (nom du réseau au survol)."""

    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.setFixedSize(30, 28)
        self.setIconSize(QSize(18, 18))
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("QToolButton { border: none; background: transparent; border-radius: 6px; }"
                           "QToolButton:hover { background: rgba(128,128,128,.18); }")

    def show_account(self, name: str, acc) -> None:
        self.linked = acc is not None
        self.setIcon(theme.platform_icon(self.key, 18) if acc else
                     theme.icon(theme.PLATFORMS[self.key][0], theme.color("faint"), 18))
        self.setToolTip(f"{name} : {acc.name}" + (f"\n{acc.public_url}" if acc.public_url else "") if acc else
                        f"{name} : pas encore de chaîne — clic pour l'ajouter")


class ConceptRow(QFrame):
    changed = Signal()
    removed = Signal(object)

    def __init__(self, app: AppContext, concept=None, parent=None):
        super().__init__(parent)
        self.app, self.concept = app, concept
        self.setObjectName("conceptRow")
        self.setStyleSheet("QFrame#conceptRow { background: transparent; border: none; }")    # la couleur du fond
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 4, 0, 6)
        v.setSpacing(4)
        h = QHBoxLayout()
        h.setSpacing(8)
        self.name = QLineEdit(concept.name if concept else "")
        self.name.setPlaceholderText("Nom du concept")
        self.name.setFixedWidth(COLS[0][1])
        self.name.editingFinished.connect(self._name_done)
        h.addWidget(self.name)
        self.theme = QLineEdit(concept.theme if concept else "")
        self.theme.setPlaceholderText("Thématique")
        self.theme.editingFinished.connect(lambda: self._update(theme=self.theme.text().strip()))
        h.addWidget(self.theme, 1)
        n = len(app.concepts.list_concepts(include_archived=True))
        self.color = ColorButton(concept.color if concept else CONCEPT_COLORS[n % len(CONCEPT_COLORS)], (24, 24))
        self.color.picked.connect(lambda c: self._update(color=c))
        cw = QWidget()
        cw.setAttribute(Qt.WA_TranslucentBackground)
        cw.setStyleSheet("background: transparent;")
        cl = QHBoxLayout(cw)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.color, 0, Qt.AlignCenter)
        cw.setFixedWidth(COLS[2][1])
        h.addWidget(cw)
        self.lang = language_combo(concept.language if concept else "fr")
        self.lang.setFixedWidth(COLS[3][1])
        self.lang.currentIndexChanged.connect(lambda _i: self._update(language=self.lang.currentData()))
        h.addWidget(self.lang)
        self.del_btn = tool("trash-2", "Supprimer ce concept", self._delete, size=28, icon_px=15)
        h.addWidget(self.del_btn)
        v.addLayout(h)
        nets = QHBoxLayout()
        nets.setSpacing(2)
        self.nets: dict[str, NetButton] = {}
        for key, name, _pfs in NETS:
            b = NetButton(key)
            b.clicked.connect(lambda _=False, k=key: self.open_channel(k))
            nets.addWidget(b)
            self.nets[key] = b
        nets.addStretch(1)
        v.addLayout(nets)
        self.pop: ChannelPopover | None = None
        self.show_accounts()

    def show_accounts(self) -> None:
        for key, name, _pfs in NETS:
            b = self.nets[key]
            b.setEnabled(self.concept is not None)
            b.show_account(name, concept_account(self.app, self.concept.id, key) if self.concept else None)

    def open_channel(self, key: str) -> ChannelPopover | None:
        if self.concept is None:
            return None
        self.pop = ChannelPopover(self.app, self.concept.id, key, self.window())
        self.pop.changed.connect(lambda: (self.show_accounts(), self.changed.emit()))
        self.pop.show_at(self.nets[key])
        return self.pop

    def _name_done(self) -> None:
        name = self.name.text().strip()
        if self.concept is None:
            if not name:
                return
            try:
                self.concept = self.app.concepts.create_concept(name, self.theme.text().strip(), self.color.value,
                                                                self.lang.currentData())
            except ConceptError as exc:
                QMessageBox.information(self, "Concept", str(exc))
                return
            self.show_accounts()
            self.changed.emit()
        elif name and name != self.concept.name:
            self._update(name=name)
        elif not name:
            self.name.setText(self.concept.name)

    def _update(self, **fields) -> None:
        if self.concept is None:
            return
        try:
            self.concept = self.app.concepts.update_concept(self.concept.id, **fields)
        except Exception as exc:                          # nom déjà pris
            QMessageBox.information(self, "Concept", str(exc).replace("UNIQUE constraint failed: concept.workspace_id, "
                                                                      "concept.name", "Ce nom est déjà pris."))
            self.name.setText(self.concept.name)
            return
        self.changed.emit()

    def _delete(self) -> None:
        if self.concept is None:
            self.removed.emit(self)
            return
        if QMessageBox.question(self, "Supprimer", f"Supprimer le concept « {self.concept.name} » ?\n\nSes chaînes "
                                "restent enregistrées.") != QMessageBox.Yes:
            return
        try:
            self.app.concepts.delete_concept(self.concept.id)
        except ConceptError:
            self.app.concepts.update_concept(self.concept.id, archived=1)      # il a des vidéos : mis de côté
        self.removed.emit(self)
        self.changed.emit()


class ConceptsBox(QFrame):
    """Le rectangle des concepts : en-têtes de colonnes, liste qu'on fait défiler, « Nouveau concept »."""
    changed = Signal()

    def __init__(self, app: AppContext, parent=None):
        super().__init__(parent)
        self.app = app
        self.setProperty("role", "card")
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 14)
        v.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(label("Concepts", "h2"))
        head.addStretch(1)
        self.new_btn = button("Nouveau concept", "soft", icon="plus", on_click=self.new_concept)
        head.addWidget(self.new_btn)
        v.addLayout(head)
        cols = QHBoxLayout()
        cols.setContentsMargins(1, 0, 40, 0)
        cols.setSpacing(8)
        for name, w in COLS:
            lb = label(name, "faint", hint=False)
            if w:
                lb.setFixedWidth(w)
            cols.addWidget(lb, 0 if w else 1)
        v.addLayout(cols)
        self.rows_host = QWidget()
        self.rows = QVBoxLayout(self.rows_host)
        self.rows.setContentsMargins(0, 0, 4, 0)
        self.rows.setSpacing(6)
        self.rows.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.rows_host)
        self.scroll.setFixedHeight(330)
        v.addWidget(self.scroll)
        self.refresh()

    def items(self) -> list[ConceptRow]:
        return [self.rows.itemAt(i).widget() for i in range(self.rows.count() - 1)]

    def _add(self, concept=None) -> ConceptRow:
        r = ConceptRow(self.app, concept)
        r.changed.connect(self.changed.emit)
        r.removed.connect(self._remove)
        self.rows.insertWidget(self.rows.count() - 1, r)
        return r

    def _remove(self, row: ConceptRow) -> None:
        row.hide()
        row.setParent(None)
        row.deleteLater()

    def refresh(self) -> None:
        """Liste reconstruite seulement si les concepts ont changé (une ligne en cours d'écriture reste) ; logos des
        chaînes toujours relus."""
        concepts = self.app.concepts.list_concepts()
        have = [r.concept.id for r in self.items() if r.concept]
        if have == [c.id for c in concepts]:
            for r in self.items():
                if r.concept:
                    r.show_accounts()
            return
        for r in self.items():
            self._remove(r)
        for c in concepts:
            self._add(c)

    def new_concept(self) -> ConceptRow:
        """Nouvelle ligne en bas de la liste, le nom prêt à être écrit."""
        r = self._add()
        QTimer.singleShot(0, lambda: (self.scroll.ensureWidgetVisible(r), r.name.setFocus()))
        return r
