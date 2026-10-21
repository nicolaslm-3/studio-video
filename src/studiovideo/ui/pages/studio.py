"""Studio (§2.1 B) — jalon 1 : médias du projet, lecteur, points d'entrée/sortie, clips.

La timeline, les sous-titres, le recadrage et l'export arrivent au jalon suivant
et sont signalés comme tels dans l'interface.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QInputDialog, QListWidget, QListWidgetItem, QMessageBox,
                               QSplitter, QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from ...core import AppContext
from ...media.ffmpeg import fmt_timecode
from ..importing import DropZone, import_paths, import_url, pick_and_import
from ..player import PlayerWidget
from ..widgets import Card, EmptyState, Unavailable, button, color_dot, label


class StudioPage(QWidget):
    new_project = Signal(object)

    def __init__(self, app: AppContext, parent=None):
        super().__init__(parent)
        self.app = app
        self.project_id: int | None = None
        self.asset_id: int | None = None
        self._save_timer = QTimer(self, singleShot=True, interval=800)
        self._save_timer.timeout.connect(self._autosave)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(label("Studio", "h1"))
        top.addSpacing(16)
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(280)
        self.project_combo.currentIndexChanged.connect(self._on_project_combo)
        top.addWidget(self.project_combo)
        self.concept_lbl = label("", "faint")
        top.addWidget(self.concept_lbl)
        top.addStretch(1)
        self.save_lbl = label("", "faint")
        top.addWidget(self.save_lbl)
        top.addWidget(button("Nouveau projet", on_click=lambda: self.new_project.emit(None)))
        root.addLayout(top)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(EmptyState("Aucun projet ouvert",
                                        "Créez un projet ou ouvrez-en un depuis le tableau de bord pour importer "
                                        "des médias et commencer le montage.", "Nouveau projet",
                                        lambda: self.new_project.emit(None)))

        work = QSplitter(Qt.Horizontal)
        # -- colonne médias
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(label("Médias du projet", "h2"))
        self.media_list = QListWidget()
        self.media_list.currentItemChanged.connect(self._on_media)
        ll.addWidget(self.media_list, 1)
        dz = DropZone("Déposez des fichiers ici")
        dz.setMinimumHeight(70)
        dz.dropped.connect(lambda p: import_paths(self.app, self, p, self.project_id))
        ll.addWidget(dz)
        hb = QHBoxLayout()
        hb.addWidget(button("Importer…", on_click=lambda: pick_and_import(self.app, self, self.project_id)))
        hb.addWidget(button("URL…", on_click=lambda: import_url(self.app, self, self.project_id)))
        hb.addWidget(button("Depuis la bibliothèque…", "ghost", on_click=self._attach_from_library))
        ll.addLayout(hb)
        work.addWidget(left)

        # -- lecteur + panneaux
        center = QSplitter(Qt.Vertical)
        pc = Card(padding=12)
        self.player = PlayerWidget()
        self.player.create_clip.connect(self._create_clip)
        self.player.in_out_changed.connect(lambda *_: self._save_timer.start())
        pc.lay.addWidget(self.player)
        center.addWidget(pc)
        tabs = QTabWidget()
        clips_w = QWidget()
        cl = QVBoxLayout(clips_w)
        cl.setContentsMargins(0, 8, 0, 0)
        self.clips = QListWidget()
        self.clips.itemDoubleClicked.connect(self._open_clip)
        cl.addWidget(self.clips)
        ch = QHBoxLayout()
        ch.addStretch(1)
        ch.addWidget(button("Renommer", on_click=self._rename_clip))
        ch.addWidget(button("Supprimer", "danger", on_click=self._delete_clip))
        cl.addLayout(ch)
        tabs.addTab(clips_w, "Clips")
        tabs.addTab(Unavailable("Timeline multipiste", "jalon 2 de la V1",
                                "Découpe, déplacement des plans, pistes audio et texte."), "Timeline")
        tabs.addTab(Unavailable("Transcription et sous-titres", "jalon 3 de la V1",
                                "Transcription locale (faster-whisper), correction et sous-titres stylés."),
                    "Sous-titres")
        tabs.addTab(Unavailable("Export MP4", "jalon 2 de la V1",
                                "Recadrage 9:16/1:1/16:9, miroir, titres, audio de base et export."), "Export")
        center.addWidget(tabs)
        center.setStretchFactor(0, 3)
        center.setStretchFactor(1, 2)
        work.addWidget(center)
        work.setStretchFactor(0, 1)
        work.setStretchFactor(1, 3)
        work.setSizes([280, 900])
        self.stack.addWidget(work)
        self.refresh()

    # -- projet -------------------------------------------------------------
    def refresh(self) -> None:
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for r in self.app.projects.recent(200):
            self.project_combo.addItem(f"{r['name']}  ·  {r['concept_name']}", r["id"])
        if self.project_id:
            self.project_combo.setCurrentIndex(max(0, self.project_combo.findData(self.project_id)))
        self.project_combo.setVisible(self.project_combo.count() > 0)
        self.project_combo.blockSignals(False)
        self.stack.setCurrentIndex(1 if self.project_id else 0)
        if self.project_id:
            self._load_media()
            self._load_clips()

    def open_project(self, project_id: int) -> None:
        self._autosave()
        self.player.unload()
        self.project_id = project_id
        self.asset_id = None
        p = self.app.projects.get(project_id)
        self.concept_lbl.setText(f"Concept : {p['concept_name']}" if p else "")
        self.refresh()
        data = self.app.projects.load_data(project_id)
        last = data.get("last_asset_id")
        row = 0
        for i in range(self.media_list.count()):
            if self.media_list.item(i).data(Qt.UserRole) == last:
                row = i
        if self.media_list.count():
            self.media_list.setCurrentRow(row)

    def _on_project_combo(self) -> None:
        pid = self.project_combo.currentData()
        if pid and pid != self.project_id:
            self.open_project(pid)

    def _load_media(self) -> None:
        cur = self.asset_id
        self.media_list.blockSignals(True)
        self.media_list.clear()
        for a in self.app.db.query("SELECT * FROM source_asset WHERE project_id = ? ORDER BY id", (self.project_id,)):
            it = QListWidgetItem(f"{a['file_name']}\n{fmt_timecode(a['duration_s']) if a['duration_s'] else a['kind']}"
                                 + ("  · introuvable" if a["missing"] else ""))
            it.setData(Qt.UserRole, a["id"])
            if a["thumbnail_path"] and Path(a["thumbnail_path"]).exists():
                it.setIcon(QPixmap(a["thumbnail_path"]))
            self.media_list.addItem(it)
            if a["id"] == cur:
                self.media_list.setCurrentItem(it)
        self.media_list.setIconSize(QSize(64, 36))
        self.media_list.blockSignals(False)

    def _on_media(self, item, _prev=None) -> None:
        if not item:
            return
        self._autosave()
        aid = item.data(Qt.UserRole)
        a = self.app.library.get(aid)
        if not a or a["kind"] not in ("video", "audio"):
            return
        if a["missing"] or not Path(a["path"]).exists():
            QMessageBox.warning(self, "Média introuvable", f"Le fichier n'existe plus :\n{a['path']}")
            return
        self.asset_id = aid
        io = self.app.projects.load_data(self.project_id).get("in_out", {}).get(str(aid), [None, None])
        self.player.load(a["path"], a["fps"], *io)

    def _attach_from_library(self) -> None:
        rows = self.app.db.query("SELECT id, file_name FROM source_asset WHERE project_id IS NULL "
                                 "AND kind IN ('video','audio') ORDER BY imported_at DESC LIMIT 300")
        if not rows:
            QMessageBox.information(self, "Bibliothèque", "Aucun média libre dans la bibliothèque.")
            return
        names = [f"{r['file_name']}  [#{r['id']}]" for r in rows]
        choice, ok = QInputDialog.getItem(self, "Ajouter depuis la bibliothèque", "Média :", names, 0, False)
        if ok:
            aid = rows[names.index(choice)]["id"]
            self.app.db.connect().execute("UPDATE source_asset SET project_id = ? WHERE id = ?",
                                          (self.project_id, aid))
            self._load_media()

    # -- sauvegarde automatique ----------------------------------------------
    def _autosave(self) -> None:
        if not self.project_id:
            return
        data = self.app.projects.load_data(self.project_id)
        if self.asset_id:
            data.setdefault("in_out", {})[str(self.asset_id)] = [self.player.in_s, self.player.out_s]
            data["last_asset_id"] = self.asset_id
        self.app.projects.save_data(self.project_id, data)
        self.save_lbl.setText("Enregistré")

    # -- clips --------------------------------------------------------------
    def _create_clip(self, start: float, end: float) -> None:
        if not self.asset_id:
            QMessageBox.information(self, "Créer un clip", "Sélectionnez d'abord un média vidéo ou audio.")
            return
        n = self.app.db.query_one("SELECT COUNT(*) n FROM clip WHERE project_id=?", (self.project_id,))["n"]
        with self.app.db.transaction() as c:
            c.execute("INSERT INTO clip(project_id, source_asset_id, name, start_s, end_s) VALUES (?,?,?,?,?)",
                      (self.project_id, self.asset_id, f"Clip {n + 1}", start, end))
        self._load_clips()

    def _load_clips(self) -> None:
        self.clips.clear()
        rows = self.app.db.query("SELECT c.*, a.file_name FROM clip c LEFT JOIN source_asset a ON a.id = "
                                 "c.source_asset_id WHERE c.project_id = ? ORDER BY c.id", (self.project_id,))
        if not rows:
            it = QListWidgetItem("Aucun clip. Définissez un début (I) et une fin (O), puis « Créer un clip » (C).")
            it.setFlags(Qt.NoItemFlags)
            self.clips.addItem(it)
        for r in rows:
            it = QListWidgetItem(f"{r['name']}   {fmt_timecode(r['start_s'], True)} → {fmt_timecode(r['end_s'], True)}"
                                 f"   ({fmt_timecode(r['end_s'] - r['start_s'], True)})   · {r['file_name'] or '—'}")
            it.setData(Qt.UserRole, r["id"])
            self.clips.addItem(it)

    def _clip_id(self) -> int | None:
        it = self.clips.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _open_clip(self, item) -> None:
        cid = item.data(Qt.UserRole)
        r = self.app.db.query_one("SELECT * FROM clip WHERE id = ?", (cid,))
        if not r:
            return
        for i in range(self.media_list.count()):
            if self.media_list.item(i).data(Qt.UserRole) == r["source_asset_id"]:
                self.media_list.setCurrentRow(i)
        self.player.in_s, self.player.out_s = r["start_s"], r["end_s"]
        self.player._refresh_io()
        self.player.preview_range()

    def _rename_clip(self) -> None:
        cid = self._clip_id()
        if not cid:
            return
        cur = self.app.db.query_one("SELECT name FROM clip WHERE id=?", (cid,))["name"]
        name, ok = QInputDialog.getText(self, "Renommer le clip", "Nom :", text=cur)
        if ok and name.strip():
            self.app.db.connect().execute("UPDATE clip SET name=? WHERE id=?", (name.strip(), cid))
            self._load_clips()

    def _delete_clip(self) -> None:
        cid = self._clip_id()
        if cid and QMessageBox.question(self, "Supprimer", "Supprimer ce clip ? Le média source n'est pas modifié."
                                        ) == QMessageBox.Yes:
            self.app.db.connect().execute("DELETE FROM clip WHERE id=?", (cid,))
            self._load_clips()
