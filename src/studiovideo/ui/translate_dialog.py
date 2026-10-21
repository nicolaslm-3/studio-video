"""Traduire une transcription dans une ou plusieurs langues (§10)."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QLineEdit,
                               QMessageBox, QVBoxLayout)

from ..core import AppContext
from ..services import secrets
from .dialogs import LANGUAGES
from .widgets import label


class TranslateDialog(QDialog):
    def __init__(self, app: AppContext, asset_id: int, glossary_hint: list[str], parent=None):
        super().__init__(parent)
        self.app, self.asset_id = app, asset_id
        self.job_ids: list[int] = []
        tr = app.transcripts.get(asset_id)
        self.src = tr.language if tr else None
        self.setWindowTitle("Traduire la transcription")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.addWidget(label(f"Langue d'origine : {dict(LANGUAGES).get(self.src or '', self.src or 'inconnue')}. "
                            "Chaque traduction garde les horodatages des phrases et reste modifiable.", "muted",
                            wrap=True))
        grid = QGridLayout()
        self.langs: dict[str, QCheckBox] = {}
        done = set(app.transcripts.languages(asset_id))
        for i, (code, name) in enumerate(LANGUAGES):
            cb = QCheckBox(name + ("  ✓" if code in done else ""))
            cb.setEnabled(code != self.src)
            cb.setToolTip("Déjà traduit : sera remplacé" if code in done else "")
            self.langs[code] = cb
            grid.addWidget(cb, i // 3, i % 3)
        lay.addLayout(grid)
        form = QFormLayout()
        self.engine = QComboBox()
        llm = app.settings.get("llm", None) or {}
        if llm.get("kind") in ("ollama", "openai") and llm.get("model"):
            self.engine.addItem(f"Modèle de langage ({llm['model']})" +
                                (" · hors ligne" if llm["kind"] == "ollama" else ""), "llm")
        self.engine.addItem("DeepL" + ("" if secrets.get_secret("deepl_api_key") else " (clé à saisir dans Paramètres)"),
                            "deepl")
        self.engine.addItem("Google Cloud Translation" +
                            ("" if secrets.get_secret("google_api_key") else " (clé à saisir dans Paramètres)"), "google")
        last = app.settings.get("translate_engine", None)
        if last:
            self.engine.setCurrentIndex(max(0, self.engine.findData(last)))
        form.addRow("Moteur", self.engine)
        self.glossary = QLineEdit(", ".join(dict.fromkeys(t for t in glossary_hint if t)))
        self.glossary.setPlaceholderText("Noms et termes à ne pas traduire, séparés par des virgules")
        form.addRow("À conserver", self.glossary)
        lay.addLayout(form)
        lay.addWidget(label("Avec DeepL, Google ou une API distante, le texte de la transcription est envoyé au "
                            "service choisi. Avec Ollama, tout reste sur votre PC.", "faint", wrap=True))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Traduire")
        bb.button(QDialogButtonBox.Cancel).setText("Annuler")
        bb.accepted.connect(self._go)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _go(self) -> None:
        langs = [c for c, cb in self.langs.items() if cb.isChecked() and cb.isEnabled()]
        if not langs:
            QMessageBox.information(self, "Traduire", "Cochez au moins une langue.")
            return
        eng = self.engine.currentData()
        self.app.settings.set("translate_engine", eng)
        glossary = [t.strip() for t in self.glossary.text().split(",") if t.strip()]
        a = self.app.library.get(self.asset_id)
        for code in langs:
            self.job_ids.append(self.app.jobs.submit(
                "translate", f"Traduction {code.upper()} · {a['file_name']}",
                {"asset_id": self.asset_id, "language": code, "engine": {"engine": eng}, "glossary": glossary}))
        self.accept()
