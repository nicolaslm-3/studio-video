"""Panneau de transcription du média sélectionné (§6).

Transcrire, corriger le texte et les intervenants, rechercher, cliquer sur une
phrase pour s'y placer, créer un clip ou un extrait de timeline depuis le texte,
exporter (TXT, SRT, VTT, JSON, ASS) et importer des sous-titres existants.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QLineEdit, QMenu, QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import AppContext
from ..media.ffmpeg import fmt_timecode
from ..transcribe import engines
from ..transcribe.model import EXPORT_FORMATS, Transcript, export_text, import_subtitles
from .dialogs import LANGUAGES
from .widgets import button, label


class TranscriptPanel(QWidget):
    seek = Signal(float)                     # temps source
    make_clip = Signal(float, float)         # début, fin (temps source)
    add_to_timeline = Signal(float, float)
    open_models = Signal()
    changed = Signal(int)                    # asset_id dont la transcription a changé
    text_edit = Signal(list, bool)           # plages à conserver, remplacer la timeline

    def __init__(self, app: AppContext, parent=None):
        super().__init__(parent)
        self.app = app
        self.asset_id: int | None = None
        self.tr: Transcript | None = None
        self.lang: str = ""          # "" = transcription d'origine, sinon langue de la traduction affichée
        self.job_id: int | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(6)
        self.status = label("Sélectionnez un média.", "muted", wrap=True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.model = QComboBox()
        self.model.setToolTip("Modèle de reconnaissance vocale (téléchargeable dans Paramètres)")
        self.lang = QComboBox()
        self.lang.addItem("Langue auto", None)
        for code, name in LANGUAGES:
            self.lang.addItem(name, code)
        row.addWidget(self.model, 1)
        row.addWidget(self.lang, 1)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        self.go = button("Transcrire", "primary", on_click=self._transcribe)
        row2.addWidget(self.go)
        self.more = button("Plus ▾", tooltip="Importer / exporter / revenir à l'original / modèles")
        self.more.clicked.connect(self._menu)
        row2.addWidget(self.more)
        lay.addLayout(row2)
        self.view_lang = QComboBox()
        self.view_lang.setToolTip("Afficher la transcription d'origine ou une traduction")
        self.view_lang.currentIndexChanged.connect(self._lang_changed)
        lay.addWidget(self.view_lang)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher dans la transcription…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Temps", "Qui", "Texte"])
        self.table.verticalHeader().hide()
        self.table.setWordWrap(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.cellClicked.connect(self._clicked)
        self.table.itemChanged.connect(self._edited)
        lay.addWidget(self.table, 1)
        row3 = QHBoxLayout()
        row3.addWidget(button("Sélection → Clip", tooltip="Crée un clip couvrant les phrases sélectionnées",
                              on_click=lambda: self._selection(self.make_clip)))
        row3.addWidget(button("→ Timeline", tooltip="Ajoute les phrases sélectionnées à la timeline",
                              on_click=lambda: self._selection(self.add_to_timeline)))
        lay.addLayout(row3)
        lay.addWidget(label("Double-cliquez sur un texte pour le corriger, sur « Qui » pour nommer l'intervenant. "
                            "L'original reste conservé.", "faint", wrap=True))
        self._loading = False
        self.translate_jobs: set[int] = set()
        self.glossary_hint: list[str] = []
        self.refresh_models()

    # -- état -------------------------------------------------------------
    def refresh_models(self) -> None:
        cur = self.model.currentData()
        self.model.clear()
        inst = engines.installed_models(self.app.paths.models) if engines.available() else []
        for m in engines.MODELS:
            if m.key in inst:
                self.model.addItem(m.label, m.key)
        if not inst:
            self.model.addItem("Aucun modèle installé", None)
        pref = cur or self.app.settings.get("whisper_model", None)
        i = self.model.findData(pref)
        self.model.setCurrentIndex(max(0, i))
        self._update_buttons()

    def set_asset(self, asset_id: int | None) -> None:
        if asset_id != self.asset_id:
            self.lang = ""
        self.asset_id = asset_id
        self._fill_langs()
        self.tr = self.app.transcripts.for_language(asset_id, self.lang) if asset_id else None
        self._fill()

    def _fill_langs(self) -> None:
        from .dialogs import LANGUAGES
        names = dict(LANGUAGES)
        self.view_lang.blockSignals(True)
        self.view_lang.clear()
        self.view_lang.addItem("Texte d'origine", "")
        for code in (self.app.transcripts.languages(self.asset_id) if self.asset_id else []):
            self.view_lang.addItem(f"Traduction : {names.get(code, code)}", code)
        i = self.view_lang.findData(self.lang)
        if i < 0:
            self.lang, i = "", 0
        self.view_lang.setCurrentIndex(i)
        self.view_lang.setVisible(self.view_lang.count() > 1)
        self.view_lang.blockSignals(False)

    def _lang_changed(self) -> None:
        self.lang = self.view_lang.currentData() or ""
        self.tr = self.app.transcripts.for_language(self.asset_id, self.lang) if self.asset_id else None
        self._fill()

    def _update_buttons(self) -> None:
        a = self.app.library.get(self.asset_id) if self.asset_id else None
        if self.job_id is not None:
            self.go.setText("Annuler")
            self.go.setEnabled(True)
            return
        self.go.setText("Retranscrire" if self.tr else "Transcrire")
        # sans modèle, le bouton reste actif : il propose d'ouvrir la page des modèles
        self.go.setEnabled(bool(a and a["audio_tracks"]))

    def _fill(self) -> None:
        self._loading = True
        self.table.setRowCount(0)
        a = self.app.library.get(self.asset_id) if self.asset_id else None
        if not a:
            self.status.setText("Sélectionnez un média vidéo ou audio.")
        elif not a["audio_tracks"]:
            self.status.setText("Ce média n'a pas de son : pas de transcription possible.")
        elif self.tr is None:
            msg = "Pas encore transcrit."
            if not engines.available():
                msg += " Le moteur local n'est pas installé dans cette version."
            elif not engines.installed_models(self.app.paths.models):
                msg += " Téléchargez d'abord un modèle (bouton « Plus » → Modèles)."
            self.status.setText(msg)
        else:
            edited = (self.app.transcripts.is_edited(self.asset_id) if not self.lang else False)
            self.status.setText(f"{len(self.tr.segments)} phrases · langue {self.tr.language or '?'} · "
                                f"{self.tr.engine}" + (" · corrigé" if edited else ""))
            for i, s in enumerate(self.tr.segments):
                self.table.insertRow(i)
                t = QTableWidgetItem(fmt_timecode(s.start))
                t.setFlags(t.flags() & ~Qt.ItemIsEditable)
                t.setData(Qt.UserRole, i)
                self.table.setItem(i, 0, t)
                self.table.setItem(i, 1, QTableWidgetItem(s.speaker))
                self.table.setItem(i, 2, QTableWidgetItem(s.text))
            self.table.resizeRowsToContents()
        self._loading = False
        self._filter(self.search.text())
        self._update_buttons()

    def _filter(self, q: str) -> None:
        if not self.tr:
            return
        hits = set(self.tr.search(q)) if q.strip() else None
        for r in range(self.table.rowCount()):
            self.table.setRowHidden(r, hits is not None and r not in hits)

    def highlight_time(self, t: float) -> None:
        if not self.tr or self.table.hasFocus():
            return
        i = self.tr.segment_at(t)
        if i is not None and i != self.table.currentRow():
            self.table.blockSignals(True)
            self.table.selectRow(i)
            self.table.blockSignals(False)
            self.table.scrollToItem(self.table.item(i, 0), QAbstractItemView.EnsureVisible)

    # -- actions --------------------------------------------------------------
    def _clicked(self, row: int, _col: int) -> None:
        if self.tr and 0 <= row < len(self.tr.segments):
            self.seek.emit(self.tr.segments[row].start)

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._loading or not self.tr:
            return
        i, c = item.row(), item.column()
        if c == 2:
            self.tr.edit_segment(i, item.text())
        elif c == 1:
            old = self.tr.segments[i].speaker
            new = item.text().strip()
            if old and new and QMessageBox.question(
                    self, "Intervenant", f"Renommer « {old} » partout en « {new} » ?") == QMessageBox.Yes:
                self.tr.rename_speaker(old, new)
                self._save()
                self._fill()
                self.changed.emit(self.asset_id)
                return
            self.tr.segments[i].speaker = new
        else:
            return
        self._save()
        self.changed.emit(self.asset_id)

    def _save(self) -> None:
        if self.lang:
            self.app.transcripts.save_translation_edit(self.asset_id, self.tr)
        else:
            self.app.transcripts.save_edit(self.asset_id, self.tr)

    def _selection(self, signal) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not self.tr or not rows:
            QMessageBox.information(self, "Sélection", "Sélectionnez une ou plusieurs phrases.")
            return
        a, b = self.tr.segments[rows[0]].start, self.tr.segments[rows[-1]].end
        signal.emit(max(0.0, a - 0.05), b + 0.15)

    def _transcribe(self) -> None:
        if self.job_id is not None:
            self.app.jobs.cancel(self.job_id)
            return
        if not engines.available():
            QMessageBox.information(self, "Transcription", "Le moteur de transcription locale n'est pas inclus "
                                    "dans cette version.")
            return
        model = self.model.currentData()
        if model is None:
            if QMessageBox.question(self, "Transcription", "Aucun modèle de reconnaissance vocale n'est installé. "
                                    "Ouvrir la page des modèles ?") == QMessageBox.Yes:
                self.open_models.emit()
            return
        if self.lang:
            self.view_lang.setCurrentIndex(0)
        if self.tr and QMessageBox.question(self, "Retranscrire", "Remplacer la transcription actuelle, "
                                            "y compris vos corrections ?") != QMessageBox.Yes:
            return
        a = self.app.library.get(self.asset_id)
        self.app.settings.set("whisper_model", model)
        self.job_id = self.app.jobs.submit("transcribe", f"Transcription · {a['file_name']}",
                                           {"asset_id": self.asset_id, "model": model,
                                            "language": self.lang.currentData()})
        self.status.setText("Transcription en cours… (suivi dans le panneau des tâches)")
        self._update_buttons()

    def on_job(self, job) -> None:
        if job.kind == "download_model" and job.status == "succeeded":
            self.refresh_models()
        if job.id in self.translate_jobs and job.status in ("succeeded", "failed", "cancelled"):
            self.translate_jobs.discard(job.id)
            title = {"dub": "Doublage", "dub_line": "Doublage", "diarize": "Intervenants"}.get(job.kind, "Traduction")
            if job.status == "failed":
                QMessageBox.warning(self, title, job.error[:800])
            elif job.kind == "dub":
                r = job.result or {}
                msg = f"Doublage {r.get('language', '').upper()} prêt : {r.get('lines', 0)} phrase(s)."
                if r.get("errors"):
                    msg += f" {len(r['errors'])} phrase(s) en échec (⚠), à reprendre."
                if r.get("accelerated"):
                    msg += f" {len(r['accelerated'])} phrase(s) nettement accélérée(s) (⏩)."
                self.status.setText(msg)
                self.changed.emit(r.get("asset_id") or self.asset_id)
            elif job.kind == "diarize":
                r = job.result or {}
                self.status.setText(f"{r.get('speakers', 0)} intervenant(s) détecté(s). Double-cliquez un nom pour "
                                    "le renommer.")
                self.set_asset(self.asset_id)
                self.changed.emit(r.get("asset_id") or self.asset_id)
            elif job.kind == "dub_line":
                self.status.setText("Nouvelle prise enregistrée.")
                self.changed.emit(self.asset_id)
            else:
                lang = job.result.get("language") if job.result else None
                if lang and job.result.get("asset_id") == self.asset_id:
                    self.lang = lang
                self.set_asset(self.asset_id)
                self.changed.emit(job.result.get("asset_id") if job.result else self.asset_id)
            return
        if job.id != self.job_id:
            return
        if job.status == "running":
            self.status.setText(f"{job.message or 'Transcription'} — {int(job.progress * 100)} %")
            return
        if job.status in ("succeeded", "failed", "cancelled"):
            self.job_id = None
            if job.status == "failed":
                QMessageBox.warning(self, "Transcription", job.error[:800])
            aid = job.result.get("asset_id") if job.result else None
            if aid:
                self.changed.emit(aid)
            self.set_asset(self.asset_id)

    def _menu(self) -> None:
        m = QMenu(self)
        exp = m.addMenu("Exporter la transcription")
        for f in EXPORT_FORMATS:
            exp.addAction(f.upper(), lambda f=f: self._export(f)).setEnabled(self.tr is not None)
        trn = m.addAction("Traduire…", self._translate)
        trn.setEnabled(bool(self.asset_id and self.app.transcripts.has(self.asset_id)))
        if self.lang:
            label = "Doublage de cette traduction…" if self.app.dubs.get(self.asset_id, self.lang) \
                else "Doubler cette traduction (voix)…"
            m.addAction(label, self._dub)
            m.addAction("Supprimer cette traduction", self._delete_translation)
        dz = m.addAction("Identifier les intervenants…", self._diarize)
        dz.setEnabled(bool(self.asset_id and self.app.transcripts.has(self.asset_id)) and not self.lang)
        te = m.addAction("Monter depuis le texte…", self._text_edit)
        te.setEnabled(self.tr is not None)
        m.addSeparator()
        m.addAction("Importer des sous-titres (SRT, VTT)…", self._import)
        a = m.addAction("Revenir au texte original", self._revert)
        a.setEnabled(bool(self.asset_id and self.app.transcripts.is_edited(self.asset_id)))
        m.addSeparator()
        m.addAction("Modèles de reconnaissance vocale…", self.open_models.emit)
        m.exec(self.more.mapToGlobal(self.more.rect().bottomLeft()))

    def _translate(self) -> None:
        from .translate_dialog import TranslateDialog
        orig = self.app.transcripts.get(self.asset_id)
        hint = list(orig.speakers()) if orig else []
        hint += self.glossary_hint
        dlg = TranslateDialog(self.app, self.asset_id, hint, self)
        if dlg.exec():
            self.translate_jobs.update(dlg.job_ids)
            self.status.setText(f"{len(dlg.job_ids)} traduction(s) en cours…")

    def _diarize(self) -> None:
        from ..ai import diarize
        if not diarize.installed(self.app.paths.models):
            if QMessageBox.question(self, "Intervenants", "Le modèle d'empreintes vocales (26 Mo) n'est pas encore "
                                    "installé. Ouvrir la page des modèles ?") == QMessageBox.Yes:
                self.open_models.emit()
            return
        choices = ["Automatique"] + [str(n) for n in range(1, 9)]
        choice, ok = QInputDialog.getItem(self, "Identifier les intervenants",
                                          "Nombre d'intervenants (le calcul se fait sur ce PC ; les noms actuels "
                                          "seront remplacés, l'original reste disponible) :", choices, 0, False)
        if not ok:
            return
        n = None if choice == "Automatique" else int(choice)
        a = self.app.library.get(self.asset_id)
        jid = self.app.jobs.submit("diarize", f"Intervenants · {a['file_name']}",
                                   {"asset_id": self.asset_id, "n_speakers": n})
        self.translate_jobs.add(jid)
        self.status.setText("Identification des intervenants…")

    def _dub(self) -> None:
        from .dub_dialog import DubDialog
        dlg = DubDialog(self.app, self.asset_id, self.lang, self)
        if dlg.exec():
            self.translate_jobs.update(dlg.job_ids)
            self.status.setText("Doublage en cours…")

    def _delete_translation(self) -> None:
        if QMessageBox.question(self, "Traduction", "Supprimer cette traduction ?") == QMessageBox.Yes:
            self.app.transcripts.delete_translation(self.asset_id, self.lang)
            self.lang = ""
            self.set_asset(self.asset_id)
            self.changed.emit(self.asset_id)

    def _text_edit(self) -> None:
        from .text_edit_dialog import TextEditDialog
        a = self.app.library.get(self.asset_id)
        if a["kind"] != "video":
            QMessageBox.information(self, "Montage par le texte", "Disponible pour les vidéos.")
            return
        dlg = TextEditDialog(self.tr, a["file_name"], self)
        if dlg.exec() and dlg.ranges:
            self.text_edit.emit(dlg.ranges, dlg.replace)

    def _export(self, fmt: str) -> None:
        a = self.app.library.get(self.asset_id)
        default = str(Path(a["path"]).with_suffix(f".{fmt}"))
        path, _ = QFileDialog.getSaveFileName(self, "Exporter la transcription", default, f"*.{fmt}")
        if path:
            Path(path).write_text(export_text(self.tr, fmt), encoding="utf-8")

    def _import(self) -> None:
        if not self.asset_id:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Importer des sous-titres", "", "Sous-titres (*.srt *.vtt)")
        if not path:
            return
        try:
            tr = import_subtitles(Path(path).read_text(encoding="utf-8", errors="replace"))
        except ValueError as exc:
            QMessageBox.warning(self, "Import", str(exc))
            return
        if self.tr and QMessageBox.question(self, "Import", "Remplacer la transcription actuelle ?") != QMessageBox.Yes:
            return
        self.app.transcripts.store_original(self.asset_id, tr)
        self.set_asset(self.asset_id)
        self.changed.emit(self.asset_id)

    def _revert(self) -> None:
        if QMessageBox.question(self, "Original", "Abandonner vos corrections et revenir au texte original ?"
                                ) == QMessageBox.Yes:
            self.app.transcripts.revert(self.asset_id)
            self.set_asset(self.asset_id)
            self.changed.emit(self.asset_id)

    def rename_speaker_dialog(self) -> None:
        if not self.tr:
            return
        names = self.tr.speakers()
        if not names:
            return
        old, ok = QInputDialog.getItem(self, "Intervenant", "Intervenant :", names, 0, False)
        if ok:
            new, ok2 = QInputDialog.getText(self, "Intervenant", "Nouveau nom :", text=old)
            if ok2 and new.strip():
                self.tr.rename_speaker(old, new.strip())
                self.app.transcripts.save_edit(self.asset_id, self.tr)
                self._fill()
