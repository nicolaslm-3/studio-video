"""Création originale (§4 mode 3) : de l'idée ou du document au script, puis à la vidéo."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QHeaderView, QMessageBox, QPlainTextEdit, QSpinBox,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..ai import tts as tts_mod
from ..core import AppContext
from ..edit import original
from ..services import secrets
from .dialogs import LANGUAGES
from .dub_dialog import CONSENT
from .widgets import button, label


class OriginalDialog(QDialog):
    """Retourne via `params` les paramètres du travail « create_original » (lancé par le Studio)."""

    def __init__(self, app: AppContext, project_id: int, width: int, height: int, parent=None):
        super().__init__(parent)
        self.app, self.pid, self.W, self.H = app, project_id, width, height
        self.params: dict | None = None
        self.script_job: int | None = None
        self.setWindowTitle("Création originale")
        self.resize(980, 760)
        lay = QVBoxLayout(self)
        lay.addWidget(label("1. La matière", "h2"))
        self.src_tabs = QTabWidget()
        self.idea = QPlainTextEdit()
        self.idea.setPlaceholderText("Votre idée en une ou deux phrases : sujet, angle, ton, public…")
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Collez un texte, ou chargez un fichier (texte, PDF, Word).")
        tw = QWidget()
        tl = QVBoxLayout(tw)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(self.text)
        tl.addWidget(button("Charger un fichier…", on_click=self._load_file))
        self.src_tabs.addTab(self.idea, "Idée")
        self.src_tabs.addTab(tw, "Texte ou document")
        self.src_tabs.setMaximumHeight(170)
        lay.addWidget(self.src_tabs)
        row = QHBoxLayout()
        self.lang = QComboBox()
        for c, n in LANGUAGES:
            self.lang.addItem(n, c)
        self.seconds = QSpinBox()
        self.seconds.setRange(10, 600)
        self.seconds.setValue(45)
        self.seconds.setSuffix(" s")
        row.addWidget(label("Langue", "muted"))
        row.addWidget(self.lang)
        row.addWidget(label("Durée visée", "muted"))
        row.addWidget(self.seconds)
        llm = app.settings.get("llm", None) or {}
        self.has_llm = llm.get("kind") in ("ollama", "openai") and bool(llm.get("model"))
        self.ai_btn = button("Écrire le script avec l'IA", "primary", on_click=self._ai_script)
        self.ai_btn.setEnabled(self.has_llm)
        if not self.has_llm:
            self.ai_btn.setToolTip("Configurez un modèle de langage (Paramètres → Matériel et moteurs)")
        row.addWidget(self.ai_btn)
        row.addWidget(button("Découper le texte en scènes", on_click=self._split_text))
        row.addStretch(1)
        lay.addLayout(row)
        self.status = label("" if self.has_llm else "Sans modèle de langage : collez un texte puis « Découper le "
                            "texte en scènes », ou écrivez les scènes vous-même.", "faint", wrap=True)
        lay.addWidget(self.status)

        lay.addWidget(label("2. Le script (modifiable)", "h2"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Narration (lue / sous-titrée)", "Texte à l'écran", "Visuel", "Durée"])
        self.table.verticalHeader().setDefaultSectionSize(54)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 200)
        self.table.setColumnWidth(2, 210)
        self.table.setColumnWidth(3, 90)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setWordWrap(True)
        lay.addWidget(self.table, 1)
        sb = QHBoxLayout()
        sb.addWidget(button("+ Scène", on_click=lambda: self._add_row(original.Scene())))
        sb.addWidget(button("Monter", "ghost", on_click=lambda: self._move(-1)))
        sb.addWidget(button("Descendre", "ghost", on_click=lambda: self._move(1)))
        sb.addWidget(button("Supprimer", "ghost", on_click=self._del_row))
        sb.addStretch(1)
        self.total = label("", "muted")
        sb.addWidget(self.total)
        lay.addLayout(sb)

        lay.addWidget(label("3. La voix", "h2"))
        vf = QFormLayout()
        vr = QHBoxLayout()
        self.engine = QComboBox()
        self.engine.addItem("Sans voix (texte et sous-titres seulement)", None)
        self.engine.addItem("ElevenLabs" + ("" if secrets.get_secret("elevenlabs_api_key") else
                                            " (clé à saisir dans Paramètres)"), "elevenlabs")
        try:
            tts_mod.PiperTTS(app.paths.models)
            self.engine.addItem("Piper (hors ligne)", "piper")
        except tts_mod.TTSError:
            pass
        self.engine.currentIndexChanged.connect(self._engine_changed)
        vr.addWidget(self.engine, 1)
        self.voice = QComboBox()
        self.voice.setEnabled(False)
        vr.addWidget(self.voice, 1)
        self.load_btn = button("Charger les voix", on_click=self._load_voices)
        self.load_btn.setEnabled(False)
        vr.addWidget(self.load_btn)
        vf.addRow("Voix off", vr)
        self.consent = QCheckBox(CONSENT)
        self.consent.setChecked(bool(app.settings.get("dub_consent", False)))
        vf.addRow("", self.consent)
        lay.addLayout(vf)
        bb = QHBoxLayout()
        bb.addWidget(label(f"Format : {width}×{height}, celui du montage (onglet Cadrage).", "faint"))
        bb.addStretch(1)
        bb.addWidget(button("Annuler", on_click=self.reject))
        self.go = button("Créer la vidéo", "primary", on_click=self._create)
        bb.addWidget(self.go)
        lay.addLayout(bb)
        self._media = [(a["id"], a["file_name"], a["kind"]) for a in app.db.query(
            "SELECT id, file_name, kind FROM source_asset WHERE kind IN ('image','video') AND missing = 0 "
            "AND (project_id = ? OR project_id IS NULL) ORDER BY project_id IS NULL, id DESC LIMIT 200", (project_id,))]
        saved = app.projects.load_data(project_id).get("original_script") or []
        for s in saved:
            self._add_row(original.Scene(**{k: v for k, v in s.items() if k in original.Scene.__dataclass_fields__}))
        self.table.itemChanged.connect(lambda _i: self._update_total())
        self._update_total()

    # -- matière -----------------------------------------------------------------------------
    def _load_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Charger un document", "",
                                              "Documents (*.txt *.md *.pdf *.docx)")
        if not path:
            return
        try:
            txt = original.clean_text(original.read_source(path))
        except original.OriginalError as exc:
            QMessageBox.warning(self, "Document", str(exc))
            return
        if not txt:
            QMessageBox.warning(self, "Document", "Aucun texte trouvé (PDF scanné ? Il faudrait d'abord le "
                                "passer en texte).")
            return
        self.text.setPlainText(txt)
        self.status.setText(f"{len(txt.split())} mots chargés.")

    def _split_text(self) -> None:
        txt = self.text.toPlainText().strip() or self.idea.toPlainText().strip()
        if not txt:
            QMessageBox.information(self, "Script", "Collez d'abord un texte.")
            return
        words = max(12, min(40, round(len(txt.split()) / max(1, self.seconds.value() / 6))))
        self._set_scenes(original.script_from_text(txt, max_words=words))

    def _ai_script(self) -> None:
        idea, src = self.idea.toPlainText().strip(), self.text.toPlainText().strip()
        if not (idea or src):
            QMessageBox.information(self, "Script", "Écrivez une idée ou fournissez un texte.")
            return
        self.script_job = self.app.jobs.submit("write_script", "Écriture du script", {
            "idea": idea, "source": src, "language": self.lang.currentData(), "seconds": self.seconds.value()})
        self.ai_btn.setEnabled(False)
        self.status.setText("Le modèle écrit le script…")

    def on_job(self, job) -> None:
        if job.id != self.script_job or job.status in ("pending", "running"):
            return
        self.script_job = None
        self.ai_btn.setEnabled(self.has_llm)
        if job.status == "succeeded":
            self._set_scenes([original.Scene(**s) for s in job.result["scenes"]])
            self.status.setText("Script proposé : relisez-le, c'est vous l'auteur. Vérifiez chaque fait.")
        else:
            self.status.setText(f"⚠ {(job.error or 'Annulé').splitlines()[-1][:300]}")

    # -- script --------------------------------------------------------------------------------
    def _visual_combo(self, value: str) -> QComboBox:
        cb = QComboBox()
        cb.addItem("Fond de couleur", "")
        for aid, name, kind in self._media:
            cb.addItem(("🖼 " if kind == "image" else "🎬 ") + name, f"asset:{aid}")
        i = cb.findData(value)
        cb.setCurrentIndex(max(0, i))
        return cb

    def _add_row(self, sc: original.Scene, at: int | None = None) -> None:
        r = self.table.rowCount() if at is None else at
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(sc.narration))
        self.table.setItem(r, 1, QTableWidgetItem(sc.text))
        self.table.setCellWidget(r, 2, self._visual_combo(sc.visual))
        d = QDoubleSpinBox()
        d.setRange(0, 120)
        d.setSpecialValueText("auto")
        d.setSuffix(" s")
        d.setValue(sc.duration)
        d.valueChanged.connect(lambda _v: self._update_total())
        self.table.setCellWidget(r, 3, d)
        self._update_total()

    def scenes(self) -> list[original.Scene]:
        out = []
        for r in range(self.table.rowCount()):
            n = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            t = (self.table.item(r, 1).text() if self.table.item(r, 1) else "").strip()
            if not (n or t):
                continue
            vis, dur = self.table.cellWidget(r, 2), self.table.cellWidget(r, 3)
            out.append(original.Scene(n, t, (vis.currentData() or "") if vis else "", dur.value() if dur else 0.0))
        return out

    def _set_scenes(self, scenes: list[original.Scene]) -> None:
        self.table.setRowCount(0)
        for s in scenes:
            self._add_row(s)

    def _move(self, d: int) -> None:
        r = self.table.currentRow()
        sc = self.scenes()
        if r < 0 or not (0 <= r + d < len(sc)):
            return
        sc[r], sc[r + d] = sc[r + d], sc[r]
        self._set_scenes(sc)
        self.table.selectRow(r + d)

    def _del_row(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self._update_total()

    def _update_total(self) -> None:
        sc = self.scenes()
        est = sum(s.duration or original.reading_time(s.narration or s.text) for s in sc)
        self.total.setText(f"{len(sc)} scène(s) · environ {est:.0f} s sans voix")

    # -- voix ----------------------------------------------------------------------------------
    def _engine_changed(self) -> None:
        on = self.engine.currentData() is not None
        self.load_btn.setEnabled(on)
        self.voice.clear()
        self.voice.setEnabled(False)

    def _load_voices(self) -> None:
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            voices = tts_mod.make_engine(self.engine.currentData(), secrets.get_secret, self.app.paths.models).voices()
        except tts_mod.TTSError as exc:
            QMessageBox.warning(self, "Voix", str(exc))
            return
        finally:
            QGuiApplication.restoreOverrideCursor()
        self.voice.clear()
        for v in voices:
            self.voice.addItem(v.name + (f" · {v.category}" if v.category else ""), v.id)
        self.voice.setEnabled(bool(voices))

    # -- création ------------------------------------------------------------------------------
    def _create(self) -> None:
        sc = self.scenes()
        if not sc:
            QMessageBox.information(self, "Création", "Le script est vide.")
            return
        eng = self.engine.currentData()
        if eng and not self.voice.currentData():
            QMessageBox.information(self, "Création", "Chargez les voix et choisissez-en une, ou « Sans voix ».")
            return
        if eng and not self.consent.isChecked():
            QMessageBox.information(self, "Création", "Cochez la confirmation sur les droits des voix.")
            return
        if eng:
            self.app.settings.set("dub_consent", True)
        self.params = {"project_id": self.pid, "scenes": [s.to_dict() for s in sc], "width": self.W,
                       "height": self.H, "language": self.lang.currentData(), "tts": eng,
                       "voice": self.voice.currentData() or ""}
        data = self.app.projects.load_data(self.pid)
        data["original_script"] = self.params["scenes"]
        self.app.projects.save_data(self.pid, data)
        self.accept()
