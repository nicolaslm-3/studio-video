"""Doubler une traduction (§10.2 modes 2-3, §11) : une voix par intervenant, reprise phrase par phrase."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QInputDialog, QListWidget, QListWidgetItem, QMessageBox, QVBoxLayout)

from ..ai import tts
from ..core import AppContext
from ..media.ffmpeg import fmt_timecode
from ..services import secrets
from .dialogs import LANGUAGES
from .widgets import button, label

CONSENT = ("Je confirme avoir le droit d'utiliser ces voix. Une voix clonée n'est utilisée qu'avec "
           "l'accord explicite de la personne concernée.")


class DubDialog(QDialog):
    def __init__(self, app: AppContext, asset_id: int, language: str, parent=None):
        super().__init__(parent)
        self.app, self.asset_id, self.language = app, asset_id, language
        self.job_ids: list[int] = []
        self.tr = app.transcripts.translation(asset_id, language)
        self.setWindowTitle(f"Doublage — {dict(LANGUAGES).get(language, language)}")
        self.setMinimumWidth(600)
        lay = QVBoxLayout(self)
        lay.addWidget(label("Chaque phrase traduite est lue par une voix de synthèse, calée sur le temps de la "
                            "phrase d'origine (accélération limitée à ×1,35). Dans le montage, choisissez ensuite "
                            "« Voix : doublage » dans l'onglet Audio.", "muted", wrap=True))
        form = QFormLayout()
        self.engine = QComboBox()
        has_key = bool(secrets.get_secret("elevenlabs_api_key"))
        self.engine.addItem("ElevenLabs" + ("" if has_key else " (clé à saisir dans Paramètres)"), "elevenlabs")
        try:
            tts.PiperTTS(app.paths.models / "piper")
            self.engine.addItem("Piper (hors ligne)", "piper")
        except tts.TTSError:
            pass
        form.addRow("Moteur", self.engine)
        row = QHBoxLayout()
        self.load = button("Charger les voix", on_click=self._load_voices)
        row.addWidget(self.load)
        self.voice_status = label("", "faint", wrap=True)
        row.addWidget(self.voice_status, 1)
        form.addRow("", row)
        self.speakers = sorted({s.speaker or "" for s in (self.tr.segments if self.tr else [])})
        self.voice_boxes: dict[str, QComboBox] = {}
        for sp in self.speakers:
            cb = QComboBox()
            cb.setEnabled(False)
            self.voice_boxes[sp] = cb
            form.addRow(f"Voix · {sp or 'intervenant'}", cb)
        lay.addLayout(form)
        self.consent = QCheckBox(CONSENT)
        self.consent.setChecked(bool(app.settings.get("dub_consent", False)))
        lay.addWidget(self.consent)
        lay.addWidget(label("Avec ElevenLabs, le texte traduit est envoyé au service et consomme vos crédits.",
                            "faint", wrap=True))

        existing = app.dubs.get(asset_id, language)
        self.lines = QListWidget()
        if existing:
            man, _ = existing
            lay.addWidget(label("Doublage existant", "h2"))
            for ln in man.lines:
                flag = "⚠ " if ln.error else ("⏩ " if ln.tempo > 1.2 else "")
                it = QListWidgetItem(f"{flag}{fmt_timecode(ln.start)}  {ln.speaker or ''}  {ln.text}")
                it.setData(Qt.UserRole, ln.index)
                tip = ln.error or (f"Accélérée ×{ln.tempo:.2f}" if ln.tempo > 1.0 else "")
                it.setToolTip(tip)
                self.lines.addItem(it)
            lay.addWidget(self.lines, 1)
            r2 = QHBoxLayout()
            r2.addWidget(button("Nouvelle prise de la phrase…", on_click=self._retake))
            r2.addWidget(button("Supprimer le doublage", "ghost", on_click=self._delete))
            r2.addStretch(1)
            lay.addLayout(r2)
        bb = QDialogButtonBox()
        self.go = bb.addButton("Refaire tout le doublage" if existing else "Doubler", QDialogButtonBox.AcceptRole)
        self.go.setProperty("kind", "primary")
        bb.addButton("Fermer", QDialogButtonBox.RejectRole)
        self.go.clicked.connect(self._launch)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.go.setEnabled(False)
        if self.tr is None:
            self.voice_status.setText("Cette traduction n'existe plus.")
            self.load.setEnabled(False)

    def _engine(self):
        return tts.make_engine(self.engine.currentData(), secrets.get_secret, self.app.paths.models)

    def _load_voices(self) -> None:
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            voices = self._engine().voices()
        except tts.TTSError as exc:
            self.voice_status.setText(str(exc))
            return
        finally:
            QGuiApplication.restoreOverrideCursor()
        if not voices:
            self.voice_status.setText("Aucune voix disponible pour ce moteur.")
            return
        saved = self.app.settings.get("dub_voices", None) or {}
        for i, (sp, cb) in enumerate(self.voice_boxes.items()):
            cb.clear()
            for v in voices:
                cb.addItem(f"{v.name}" + (f" · {v.category}" if v.category else ""), v.id)
            pref = saved.get(sp)
            j = cb.findData(pref) if pref else -1
            cb.setCurrentIndex(j if j >= 0 else i % len(voices))
            cb.setEnabled(True)
        self.voice_status.setText(f"{len(voices)} voix disponibles.")
        self.go.setEnabled(True)

    def _launch(self) -> None:
        if not self.consent.isChecked():
            QMessageBox.information(self, "Doublage", "Cochez la confirmation sur les droits des voix.")
            return
        voices = {sp: cb.currentData() for sp, cb in self.voice_boxes.items() if cb.currentData()}
        if not voices:
            return
        self.app.settings.set("dub_consent", True)
        self.app.settings.set("dub_voices", {**(self.app.settings.get("dub_voices", None) or {}), **voices})
        a = self.app.library.get(self.asset_id)
        self.job_ids.append(self.app.jobs.submit(
            "dub", f"Doublage {self.language.upper()} · {a['file_name']}",
            {"asset_id": self.asset_id, "language": self.language, "engine": self.engine.currentData(),
             "voices": voices, "default_voice": next(iter(voices.values()))}))
        self.accept()

    def _retake(self) -> None:
        it = self.lines.currentItem()
        if it is None:
            QMessageBox.information(self, "Doublage", "Sélectionnez une phrase.")
            return
        man, _ = self.app.dubs.get(self.asset_id, self.language)
        ln = next(x for x in man.lines if x.index == it.data(Qt.UserRole))
        text, ok = QInputDialog.getText(self, "Nouvelle prise", "Texte à lire (raccourcir aide si la phrase "
                                        "est accélérée) :", text=ln.text)
        if not ok or not text.strip():
            return
        self.job_ids.append(self.app.jobs.submit(
            "dub_line", f"Doublage · phrase {ln.index + 1}",
            {"asset_id": self.asset_id, "language": self.language, "index": ln.index, "text": text.strip()}))
        self.accept()

    def _delete(self) -> None:
        if QMessageBox.question(self, "Doublage", "Supprimer ce doublage ?") == QMessageBox.Yes:
            self.app.dubs.delete(self.asset_id, self.language)
            self.reject()
