"""Choisir la miniature d'une publication : un instant de la vidéo, avec ou sans titre."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QSlider, QVBoxLayout)

from ..core import AppContext
from ..media import ffmpeg
from ..media.ffmpeg import fmt_timecode
from ..publish import thumbnail
from .widgets import label


class ThumbnailDialog(QDialog):
    def __init__(self, app: AppContext, master_id: int, video: str, title: str, parent=None):
        super().__init__(parent)
        self.app, self.mid, self.video = app, master_id, Path(video)
        m = app.publications.master(master_id)
        self.setWindowTitle("Miniature et couverture")
        self.resize(760, 640)
        lay = QVBoxLayout(self)
        lay.addWidget(label("Choisissez l'instant de la vidéo. Il sert de miniature sur YouTube (chaîne validée) et "
                            "d'image de couverture sur TikTok et Instagram.", "muted", wrap=True))
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(360)
        self.preview.setStyleSheet("background:#0B0C0E; border-radius:8px;")
        lay.addWidget(self.preview, 1)
        try:
            self.duration = float(ffmpeg.probe(self.video).duration_s or 1.0)
        except Exception:
            self.duration = 1.0
        row = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, int(self.duration * 10))
        self.slider.setValue(int((m["cover_s"] if m and m["cover_s"] is not None else min(1.0, self.duration / 3)) * 10))
        self.tc = label("", "muted")
        row.addWidget(self.slider, 1)
        row.addWidget(self.tc)
        lay.addLayout(row)
        tr = QHBoxLayout()
        self.with_title = QCheckBox("Ajouter le titre")
        self.with_title.setChecked(True)
        self.title = QLineEdit(title)
        tr.addWidget(self.with_title)
        tr.addWidget(self.title, 1)
        lay.addLayout(tr)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Utiliser cette miniature")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.dest = app.paths.projects / "miniatures" / f"publication_{master_id}.jpg"
        self.tmp = app.paths.cache / "miniatures" / f"apercu_{master_id}.jpg"
        self.timer = QTimer(self, singleShot=True, interval=250)
        self.timer.timeout.connect(self._render)
        self.slider.valueChanged.connect(lambda _v: self.timer.start())
        self.title.textChanged.connect(lambda _t: self.timer.start())
        self.with_title.toggled.connect(lambda _v: self.timer.start())
        self._render()

    def t(self) -> float:
        return self.slider.value() / 10

    def _text(self) -> str:
        return self.title.text() if self.with_title.isChecked() else ""

    def _render(self) -> None:
        self.tc.setText(fmt_timecode(self.t(), True))
        try:
            thumbnail.make(self.video, self.t(), self._text(), self.tmp)
        except Exception as exc:
            self.preview.setText(f"Aperçu impossible : {exc}")
            return
        pm = QPixmap(str(self.tmp))
        self.preview.setPixmap(pm.scaled(self.preview.width() - 8, self.preview.height() - 8, Qt.KeepAspectRatio,
                                         Qt.SmoothTransformation))

    def _save(self) -> None:
        try:
            thumbnail.make(self.video, self.t(), self._text(), self.dest)
        except Exception as exc:
            QMessageBox.warning(self, "Miniature", str(exc))
            return
        self.app.publications.update_master(self.mid, thumbnail=str(self.dest), cover_s=round(self.t(), 2))
        self.accept()
