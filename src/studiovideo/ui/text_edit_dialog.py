"""Montage par le texte (§6) : décocher des phrases pour les retirer, avec aperçu avant validation."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
                               QVBoxLayout)

from ..ai.highlights import ranges_from_sentences
from ..media.ffmpeg import fmt_timecode
from ..transcribe.model import Transcript
from .widgets import button, label


class TextEditDialog(QDialog):
    """Retourne `ranges` (plages source à conserver) et `replace` (remplacer la timeline ou ajouter)."""

    def __init__(self, tr: Transcript, file_name: str, parent=None):
        super().__init__(parent)
        self.tr = tr
        self.ranges: list[tuple[float, float]] = []
        self.replace = True
        self.setWindowTitle(f"Monter depuis le texte · {file_name}")
        self.resize(760, 640)
        lay = QVBoxLayout(self)
        lay.addWidget(label("Décochez les phrases à retirer. Les phrases conservées qui se suivent restent d'un seul "
                            "tenant ; le fichier source n'est pas modifié.", "muted", wrap=True))
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filtrer…")
        self.search.textChanged.connect(self._filter)
        top.addWidget(self.search, 1)
        top.addWidget(button("Tout cocher", "ghost", on_click=lambda: self._all(True)))
        top.addWidget(button("Tout décocher", "ghost", on_click=lambda: self._all(False)))
        top.addWidget(button("Retirer les silences / hésitations", "ghost",
                             tooltip="Décoche les phrases très courtes (« euh », « bon »…)", on_click=self._fillers))
        lay.addLayout(top)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        for i, s in enumerate(tr.segments):
            it = QListWidgetItem(f"{fmt_timecode(s.start)}  {(s.speaker + ' : ') if s.speaker else ''}{s.text}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            it.setData(Qt.UserRole, i)
            self.list.addItem(it)
        self.list.itemChanged.connect(lambda _it: self._summary())
        lay.addWidget(self.list, 1)
        self.sum = label("", "h2")
        lay.addWidget(self.sum)
        bb = QDialogButtonBox()
        self.b_replace = bb.addButton("Remplacer la timeline", QDialogButtonBox.AcceptRole)
        self.b_replace.setProperty("kind", "primary")
        self.b_add = bb.addButton("Ajouter à la fin de la timeline", QDialogButtonBox.AcceptRole)
        bb.addButton("Annuler", QDialogButtonBox.RejectRole)
        self.b_replace.clicked.connect(lambda: self._done(True))
        self.b_add.clicked.connect(lambda: self._done(False))
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._summary()

    def kept(self) -> list[int]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def _summary(self) -> None:
        keep = self.kept()
        rng = ranges_from_sentences(self.tr, keep) if keep else []
        total = sum(b - a for a, b in rng)
        full = self.tr.duration
        removed = len(self.tr.segments) - len(keep)
        self.sum.setText(f"Durée conservée : {fmt_timecode(total)} sur {fmt_timecode(full)} · {len(rng)} extrait(s) · "
                         f"{removed} phrase(s) retirée(s)")
        for b in (self.b_replace, self.b_add):
            b.setEnabled(bool(keep))

    def _all(self, on: bool) -> None:
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            if not self.list.item(i).isHidden():
                self.list.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.list.blockSignals(False)
        self._summary()

    def _fillers(self) -> None:
        fillers = {"euh", "heu", "bon", "ben", "bah", "hum", "hmm", "voilà", "voila", "ok", "d'accord", "uh", "um"}
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            s = self.tr.segments[self.list.item(i).data(Qt.UserRole)]
            words = [w.strip(".,!?…").lower() for w in s.text.split()]
            if len(words) <= 2 and all(w in fillers for w in words if w):
                self.list.item(i).setCheckState(Qt.Unchecked)
        self.list.blockSignals(False)
        self._summary()

    def _filter(self, q: str) -> None:
        hits = set(self.tr.search(q)) if q.strip() else None
        for i in range(self.list.count()):
            self.list.item(i).setHidden(hits is not None and self.list.item(i).data(Qt.UserRole) not in hits)

    def _done(self, replace: bool) -> None:
        self.replace = replace
        self.ranges = ranges_from_sentences(self.tr, self.kept())
        self.accept()
