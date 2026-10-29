"""Transcription de la vidéo du projet (bouton « Transcription » en haut) ou une transcription traduite (drapeau de
la langue, onglet Traduction) : la lire et la corriger phrase par phrase, enregistré au fil de la frappe.

La version corrigée est celle que prend tout le logiciel (sous-titres, extraits, traduction, voix doublée).
L'horloge ouvre l'historique : chaque transcription (ou traduction) faite y reste, et on peut y revenir."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QMessageBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from ..core import AppContext, asr_params
from ..media.ffmpeg import fmt_timecode
from .widgets import HistoryPopover, button, label, local_time, tool


def engine_name(engine: str) -> str:
    """Nom lisible du moteur qui a produit une transcription, une traduction ou une voix."""
    from ..ai import translate as trm
    from ..ai import tts
    e = engine or ""
    if e.startswith("faster-whisper"):
        return f"Whisper {e.split(':', 1)[1]} · sur ce PC" if ":" in e else "Whisper · sur ce PC"
    if e == "elevenlabs:scribe_v2":
        return "ElevenLabs Scribe v2"
    if e == "elevenlabs-dubbing":
        return "ElevenLabs · doublage de la vidéo"
    if e.startswith("traduction:"):
        e = e.split(":", 1)[1]
    key = e.split(":")[0]
    if key in trm.ENGINES:
        return trm.ENGINES[key][0]
    if key in tts.ENGINES:
        return tts.ENGINES[key][0]
    return e or "Inconnu"


def history_entries(app: AppContext, asset_id: int, kind: str, language: str = "") -> list[tuple]:
    """(identifiant, moteur, début du texte, appliquée, date) de chaque version, la plus récente d'abord."""
    from ..edit.dubbing import DubManifest
    from ..transcribe.model import Transcript
    svc = app.dubs.history if kind == "dub" else app.transcripts.history
    out = []
    for r in svc.list(asset_id, kind, language):
        try:
            if kind == "dub":
                man = DubManifest.from_json(r["data_json"])
                first = man.lines[0].text if man.lines else "La voix de la vidéo, doublée"
            else:
                segs = Transcript.from_json(r["data_json"]).segments
                first = segs[0].text if segs else "(vide)"
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            first = ""
        title = r["label"] or engine_name(r["engine"])
        out.append((r["id"], title, first[:80], bool(r["active"]), local_time(r["created_at"])))
    return out


class TranscriptDialog(QDialog):
    saved = Signal(str)                      # langue modifiée ("" : la transcription d'origine)
    open_settings = Signal()                 # roue crantée : moteurs de transcription

    def __init__(self, app: AppContext, asset: dict | None, language: str = "", parent=None):
        super().__init__(parent)
        from ..ai.translate import LANG_NAMES
        self.app, self.asset, self.lang = app, asset, language
        self.aid = asset["id"] if asset else None
        self.setWindowTitle("Transcription" if not language else
                            f"Transcription traduite · {LANG_NAMES.get(language, language).capitalize()}")
        self.resize(760, 600)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(label(self.windowTitle(), "h2"))
        head.addStretch(1)
        self.history_btn = tool("history", "Historique des transcriptions" if not language else
                                "Historique des traductions", self._history)
        head.addWidget(self.history_btn)
        if not language:
            head.addWidget(tool("settings", "Moteur de transcription : sur ce PC ou ElevenLabs Scribe v2",
                                self.open_settings.emit))
        lay.addLayout(head)
        self.info = label("", "muted", wrap=True, hint=False)
        lay.addWidget(self.info)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Temps", "Texte"])
        self.table.verticalHeader().hide()
        self.table.setWordWrap(True)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                   | QAbstractItemView.AnyKeyPressed)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.itemChanged.connect(self._edited)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.again_btn = button("Transcrire à nouveau", "ghost", on_click=self._transcribe,
                                tooltip="Avec le moteur choisi (roue crantée) ; la transcription actuelle reste "
                                "dans l'historique")
        self.go_btn = button("Transcrire", "primary", on_click=self._transcribe)
        row.addWidget(self.again_btn)
        row.addStretch(1)
        row.addWidget(self.go_btn)
        row.addWidget(button("Fermer", "ghost", on_click=self.accept))
        lay.addLayout(row)
        self._save_timer = QTimer(self, singleShot=True, interval=700)   # enregistré pendant la frappe
        self._save_timer.timeout.connect(self._save)
        self._dirty = False
        self._loading = False
        self.load()

    def _current(self):
        if self.aid is None:
            return None
        return self.app.transcripts.for_language(self.aid, self.lang)

    def load(self) -> None:
        self._loading = True
        try:
            self._load()
        finally:
            self._loading = False

    def _load(self) -> None:
        self.tr = self._current()
        self.table.setRowCount(0)
        has = self.tr is not None and bool(self.tr.segments)
        self.table.setVisible(has)
        self.go_btn.setVisible(not has and not self.lang and self.aid is not None)
        self.again_btn.setVisible(has and not self.lang)
        self.history_btn.setEnabled(self.aid is not None)
        if self.aid is None:
            self.info.setText("Pas encore de vidéo : importe-la en haut.")
            return
        if not has:
            running = self.app.db.query_one(
                "SELECT id FROM job WHERE kind IN ('transcribe','auto_clips','translate_video') AND status IN "
                "('pending','running') AND json_extract(params_json, '$.asset_id') = ?", (self.aid,))
            if self.lang:
                self.info.setText("Pas encore de traduction dans cette langue : « Traduire » dans l'onglet "
                                  "Traduction.")
            elif running:
                self.info.setText("Transcription en cours… Rouvre cette fenêtre quand la tâche est finie.")
                self.go_btn.setVisible(False)
            else:
                self.info.setText("Pas encore de transcription pour cette vidéo.")
            return
        self.info.setText(f"{self.asset['file_name']} · {engine_name(self.tr.engine)}")
        self.table.setRowCount(len(self.tr.segments))
        for i, s in enumerate(self.tr.segments):
            t = QTableWidgetItem(fmt_timecode(s.start))
            t.setFlags(t.flags() & ~Qt.ItemIsEditable)
            t.setForeground(self.palette().placeholderText())
            self.table.setItem(i, 0, t)
            self.table.setItem(i, 1, QTableWidgetItem(s.text))
        self.table.resizeRowsToContents()

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._loading or self.tr is None or item.column() != 1:
            return
        self.tr.edit_segment(item.row(), item.text())
        self._dirty = True
        self._save_timer.start()

    def _save(self) -> None:
        """Corrections enregistrées d'elles-mêmes ; tout le logiciel reprend la version corrigée."""
        self._save_timer.stop()
        if not self._dirty or self.tr is None:
            return
        self._dirty = False
        if self.lang:
            self.app.transcripts.save_translation_edit(self.aid, self.tr)
        else:
            self.app.transcripts.save_edit(self.aid, self.tr)
        self.saved.emit(self.lang)

    def done(self, r: int) -> None:
        self._save()                                      # rien ne se perd en fermant
        super().done(r)

    def _history(self) -> None:
        self._save()
        kind = "translation" if self.lang else "transcript"
        self.pop = HistoryPopover("Historique des transcriptions" if not self.lang else "Historique des traductions",
                                  history_entries(self.app, self.aid, kind, self.lang), self)
        self.pop.picked.connect(self._apply)
        self.pop.show_at(self.history_btn)

    def _apply(self, hid: int) -> None:
        lang = self.app.transcripts.apply_version(hid)
        if lang is None:
            return
        self.load()
        self.saved.emit(lang)

    def _transcribe(self) -> None:
        if self.aid is None:
            return
        how = asr_params(self.app)
        if isinstance(how, str):
            QMessageBox.information(self, "Transcription", "Transcription impossible : " + how)
            return
        self._save()
        self.app.jobs.submit("transcribe", f"Transcription · {self.asset['file_name']}", {"asset_id": self.aid, **how})
        self.info.setText("Transcription lancée : suis-la dans la liste des tâches. La nouvelle transcription sera "
                          "appliquée, l'actuelle restera dans l'historique.")
        self.go_btn.setVisible(False)
        self.again_btn.setVisible(False)
