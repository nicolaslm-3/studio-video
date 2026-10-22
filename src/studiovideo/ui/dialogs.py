"""Boîtes de dialogue : concept, compte social, nouveau projet."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QGridLayout, QHBoxLayout, QLineEdit, QMessageBox, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from ..core import AppContext
from ..domain.models import Platform, ProjectMode
from ..services.concepts import ConceptError
from .widgets import Card, button, label

LANGUAGES = [("fr", "Français"), ("en", "Anglais"), ("es", "Espagnol"), ("pt", "Portugais"),
             ("it", "Italien"), ("ja", "Japonais"), ("ru", "Russe"), ("ko", "Coréen"), ("zh", "Chinois"),
             ("ar", "Arabe"), ("tr", "Turc"), ("id", "Indonésien"), ("hi", "Hindi")]

CONCEPT_COLORS = ["#8A8F98", "#7C9CC4", "#8FB09A", "#C4A27C", "#B98AAE", "#C47C7C", "#7FB3B8", "#A99BC9"]


def language_combo(selected: str = "fr") -> QComboBox:
    cb = QComboBox()
    for code, name in LANGUAGES:
        cb.addItem(name, code)
    i = cb.findData(selected)
    cb.setCurrentIndex(max(0, i))
    return cb


class ColorButton(QPushButton):
    def __init__(self, value: str):
        super().__init__()
        self.setFixedSize(34, 26)
        self.set_value(value)
        self.clicked.connect(self._pick)

    def set_value(self, v: str) -> None:
        self.value = v
        self.setStyleSheet(f"background:{v}; border-radius:6px; border:1px solid rgba(0,0,0,.2);")

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self.value), self, "Couleur du concept")
        if c.isValid():
            self.set_value(c.name())


class AccountDialog(QDialog):
    def __init__(self, app: AppContext, parent=None):
        super().__init__(parent)
        self.app = app
        self.account_id: int | None = None
        self.setWindowTitle("Nouveau compte social")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.platform = QComboBox()
        for p in Platform:
            self.platform.addItem(p.label, p.value)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Nom affiché du compte")
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://…  (facultatif)")
        self.lang = language_combo()
        form.addRow("Plateforme", self.platform)
        form.addRow("Nom", self.name)
        form.addRow("URL publique", self.url)
        form.addRow("Langue principale", self.lang)
        lay.addLayout(form)
        lay.addWidget(label("La connexion officielle (OAuth) au compte sera proposée avec le module de "
                            "publication (V3). Aucun mot de passe n'est demandé ni enregistré.", "faint", wrap=True))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Créer")
        bb.button(QDialogButtonBox.Cancel).setText("Annuler")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _ok(self) -> None:
        try:
            acc = self.app.concepts.create_account(self.platform.currentData(), self.name.text(),
                                                   self.url.text().strip(), self.lang.currentData())
        except ConceptError as exc:
            QMessageBox.warning(self, "Compte", str(exc))
            return
        self.account_id = acc.id
        self.accept()


class ConceptDialog(QDialog):
    """Création / édition d'un concept et de ses comptes associés."""

    def __init__(self, app: AppContext, concept_id: int | None = None, parent=None):
        super().__init__(parent)
        self.app = app
        self.concept_id = concept_id
        c = app.concepts.get_concept(concept_id) if concept_id else None
        self.setWindowTitle("Modifier le concept" if c else "Nouveau concept")
        self.setMinimumSize(520, 560)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        form = QFormLayout()
        self.name = QLineEdit(c.name if c else "")
        self.name.setPlaceholderText("Ex. : Poussey Pass")
        self.theme_ = QLineEdit(c.theme if c else "")
        self.theme_.setPlaceholderText("Ex. : humour")
        n = len(app.concepts.list_concepts(True))
        self.color = ColorButton(c.color if c else CONCEPT_COLORS[n % len(CONCEPT_COLORS)])
        self.lang = language_combo(c.language if c else "fr")
        form.addRow("Nom", self.name)
        form.addRow("Thématique", self.theme_)
        form.addRow("Couleur", self.color)
        form.addRow("Langue", self.lang)
        lay.addLayout(form)

        lay.addWidget(label("Comptes associés", "h2"))
        lay.addWidget(label("Cochez les comptes de ce concept. « Par défaut » = présélectionné à la "
                            "publication. Vous pouvez en ajouter ou retirer à tout moment.", "muted", wrap=True))
        self.accounts_box = QWidget()
        self.grid = QGridLayout(self.accounts_box)
        self.grid.setColumnStretch(0, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.accounts_box)
        lay.addWidget(scroll, 1)
        add = button("+ Nouveau compte", "ghost", on_click=self._new_account)
        lay.addWidget(add, 0, Qt.AlignLeft)
        self._rows: dict[int, tuple[QCheckBox, QCheckBox]] = {}
        self._fill_accounts()

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("Enregistrer")
        bb.button(QDialogButtonBox.Cancel).setText("Annuler")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _fill_accounts(self, check_ids: set[int] | None = None) -> None:
        prev = {aid: (a.isChecked(), d.isChecked()) for aid, (a, d) in self._rows.items()}
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self._rows.clear()
        linked = dict((a.id, d) for a, d in self.app.concepts.concept_accounts(self.concept_id)) \
            if self.concept_id else {}
        accounts = self.app.concepts.list_accounts()
        if not accounts:
            self.grid.addWidget(label("Aucun compte. Créez-en un avec « + Nouveau compte ».", "faint"), 0, 0)
        for i, a in enumerate(accounts):
            on, dflt = prev.get(a.id, (a.id in linked or a.id in (check_ids or set()), linked.get(a.id, True)))
            cb = QCheckBox(a.display)
            cb.setChecked(on)
            others = [c.name for c in self.app.concepts.concepts_of_account(a.id) if c.id != self.concept_id]
            if others:
                cb.setToolTip("Ce compte est aussi associé à : " + ", ".join(others))
                cb.setText(f"{a.display}   · associé à {', '.join(others)}")
            d = QCheckBox("Par défaut")
            d.setChecked(dflt)
            d.setEnabled(on)
            cb.toggled.connect(d.setEnabled)
            self.grid.addWidget(cb, i, 0)
            self.grid.addWidget(d, i, 1)
            self._rows[a.id] = (cb, d)

    def _new_account(self) -> None:
        dlg = AccountDialog(self.app, self)
        if dlg.exec() and dlg.account_id:
            self._fill_accounts({dlg.account_id})

    def _save(self) -> None:
        cs = self.app.concepts
        try:
            if self.concept_id:
                cs.update_concept(self.concept_id, name=self.name.text().strip(), theme=self.theme_.text(),
                                  color=self.color.value, language=self.lang.currentData())
            else:
                self.concept_id = cs.create_concept(self.name.text(), self.theme_.text(), self.color.value,
                                                    self.lang.currentData()).id
        except (ConceptError, Exception) as exc:
            QMessageBox.warning(self, "Concept", str(exc))
            return
        linked = {a.id for a, _ in cs.concept_accounts(self.concept_id)}
        for aid, (on, dflt) in self._rows.items():
            if on.isChecked():
                cs.link(self.concept_id, aid, dflt.isChecked())
            elif aid in linked:
                cs.unlink(self.concept_id, aid)
        self.accept()


# Disponibilité des parcours dans cette version (§24, §25 : pas de faux boutons)
MODE_INFO = {
    ProjectMode.FREE_EDIT: ("Assembler, découper, recadrer et exporter vos fichiers.", None),
    ProjectMode.SMART_CLIPPING: ("Transcrire une vidéo longue et en extraire les meilleurs passages.", None),
    ProjectMode.TRANSLATION: ("Transcrire, traduire, sous-titrer et doubler une vidéo, étape par étape.", None),
    ProjectMode.ORIGINAL: ("Créer une vidéo à partir d'un texte, d'un PDF ou d'une idée.", "V4"),
    ProjectMode.CAMPAIGN: ("Suivre le brief d'un client et contrôler la conformité de chaque clip.", None),
    ProjectMode.BATCH: ("Produire plusieurs vidéos à partir d'un modèle (bouton « Production en série »).", None),
}


class NewProjectDialog(QDialog):
    def __init__(self, app: AppContext, concept_id: int | None = None, parent=None):
        super().__init__(parent)
        self.app = app
        self.project_id: int | None = None
        self.mode = ProjectMode.FREE_EDIT
        self.setWindowTitle("Nouveau projet")
        self.setMinimumWidth(640)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        form = QFormLayout()
        self.concept = QComboBox()
        for c in app.concepts.list_concepts():
            self.concept.addItem(c.name, c.id)
        if concept_id:
            self.concept.setCurrentIndex(max(0, self.concept.findData(concept_id)))
        self.name = QLineEdit()
        self.name.setPlaceholderText("Nom du projet")
        form.addRow("Concept", self.concept)
        form.addRow("Nom", self.name)
        lay.addLayout(form)
        lay.addWidget(label("Parcours", "h2"))
        grid = QGridLayout()
        grid.setSpacing(10)
        self._cards: dict[ProjectMode, Card] = {}
        for i, (mode, (desc, ver)) in enumerate(MODE_INFO.items()):
            card = Card(clickable=ver is None, padding=12, spacing=4)
            head = QHBoxLayout()
            head.addWidget(label(mode.label, "h2"))
            head.addStretch(1)
            if ver:
                head.addWidget(label(f"Prévu {ver}", "badge"))
            card.lay.addLayout(head)
            card.lay.addWidget(label(desc, "muted" if not ver else "faint", wrap=True))
            card.setEnabled(ver is None)
            if ver is None:
                card.clicked.connect(lambda m=mode: self._select(m))
            grid.addWidget(card, i // 2, i % 2)
            self._cards[mode] = card
        lay.addLayout(grid)
        self._select(ProjectMode.FREE_EDIT)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Créer le projet")
        bb.button(QDialogButtonBox.Ok).setProperty("kind", "primary")
        bb.button(QDialogButtonBox.Cancel).setText("Annuler")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _select(self, mode: ProjectMode) -> None:
        self.mode = mode
        for m, c in self._cards.items():
            c.setStyleSheet("QFrame[role='card']{border:1px solid palette(highlight);}" if m == mode else "")

    def _ok(self) -> None:
        if self.concept.count() == 0:
            QMessageBox.information(self, "Nouveau projet", "Créez d'abord un concept éditorial.")
            return
        try:
            self.project_id = self.app.projects.create(self.concept.currentData(), self.name.text(), self.mode)
        except ValueError as exc:
            QMessageBox.warning(self, "Nouveau projet", str(exc))
            return
        self.accept()
