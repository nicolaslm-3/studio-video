"""Aide des raccourcis clavier (F1). La liste reflète les raccourcis réellement déclarés dans l'interface."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout

from .widgets import label

GROUPS = [
    ("Partout", [("Ctrl+1 … Ctrl+6", "Studio, Médias, Calendrier, Analytics, Archives, Paramètres"),
                 ("Ctrl+N", "Nouvelle vidéo"), ("Ctrl+I", "Importer des médias"),
                 ("Ctrl+J", "Afficher ou masquer les tâches"), ("F1", "Cette aide")]),
    ("Lecteur (média source)", [("Espace", "Lecture / pause"), ("← / →", "Image précédente / suivante"),
                                ("I / O", "Marquer l'entrée / la sortie"), ("P", "Lire la sélection"),
                                ("C", "Créer un clip de la sélection")]),
    ("Studio (montage)", [("Espace", "Lecture / pause de l'aperçu"), ("← / →", "Image précédente / suivante"),
                          ("S", "Couper l'extrait à la tête de lecture"), ("Suppr", "Supprimer l'élément sélectionné"),
                          ("A", "Ajouter le média ou la sélection à la timeline"),
                          ("Ctrl+Z", "Annuler"), ("Ctrl+Y / Ctrl+Maj+Z", "Rétablir"), ("Ctrl+E", "Exporter"), ("Ctrl+Maj+S", "Enregistrer l'image affichée"),
                          ("Glisser un bord d'extrait", "Rogner l'extrait"),
                          ("Glisser un texte", "Le déplacer (ses bords : le rallonger)"),
                          ("Ctrl + molette", "Zoom de la timeline")]),
]


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Raccourcis clavier")
        self.resize(560, 520)
        lay = QVBoxLayout(self)
        rows = sum(len(items) + 1 for _g, items in GROUPS)
        self.table = QTableWidget(rows, 2)
        self.table.horizontalHeader().setVisible(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        r = 0
        for group, items in GROUPS:
            it = QTableWidgetItem(group)
            f = it.font()
            f.setBold(True)
            it.setFont(f)
            self.table.setItem(r, 0, it)
            self.table.setSpan(r, 0, 1, 2)
            r += 1
            for key, what in items:
                self.table.setItem(r, 0, QTableWidgetItem(key))
                self.table.setItem(r, 1, QTableWidgetItem(what))
                r += 1
        lay.addWidget(self.table, 1)
        lay.addWidget(label("Les raccourcis du lecteur et du Studio agissent quand la zone correspondante est active "
                            "(cliquez dedans).", "faint", wrap=True))
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
