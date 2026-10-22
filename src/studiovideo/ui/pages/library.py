"""Bibliothèque (§2.1 C, §16.1)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QSplitter, QStackedWidget, QTableView,
                               QVBoxLayout, QWidget)

from ...core import AppContext
from ...media.ffmpeg import fmt_timecode
from ..importing import KIND_LABELS, DropZone, import_paths, import_url, pick_and_import
from .. import theme
from ..widgets import Card, EmptyState, PageHeader, button, label

COLS = ["", "Nom", "Type", "Durée", "Résolution", "Concept", "Importé le"]


def human_size(n: int) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} To"


class LibraryPage(QWidget):
    def __init__(self, app: AppContext, parent=None):
        super().__init__(parent)
        self.app = app
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 30, 36, 8)
        lay.setSpacing(14)
        head = PageHeader("Extraits")
        head.add_action(button("URL", "ghost", "Importer depuis une adresse web", on_click=lambda: import_url(self.app, self),
                               icon="link"))
        head.add_action(button("Importer", "primary", "Importer des fichiers (Ctrl+I)",
                               on_click=lambda: pick_and_import(self.app, self), icon="upload"))
        lay.addWidget(head)

        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher…")
        self.search.addAction(theme.icon("search", theme.color("faint"), 16), QLineEdit.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        self.kind = QComboBox()
        self.kind.addItem("Tous les types", None)
        for k, v in KIND_LABELS.items():
            self.kind.addItem(v, k)
        self.kind.currentIndexChanged.connect(self.refresh)
        self.concept = QComboBox()
        self.concept.currentIndexChanged.connect(self.refresh)
        self.fav = QCheckBox("Favoris")
        self.fav.toggled.connect(self.refresh)
        self.in_speech = QCheckBox("Dans les paroles")
        self.in_speech.setToolTip("Chercher le texte dans les transcriptions de tous les médias")
        self.in_speech.toggled.connect(self.refresh)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.kind)
        bar.addWidget(self.concept)
        bar.addWidget(self.fav)
        bar.addWidget(self.in_speech)
        lay.addLayout(bar)

        self.stack = QStackedWidget()
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.addStretch(1)
        el.addWidget(EmptyState("Aucun média pour l'instant",
                                "Importez des vidéos, audios, images ou documents. Ils restent à leur "
                                "emplacement d'origine ; seules leurs informations sont enregistrées."))
        dz = DropZone()
        dz.dropped.connect(lambda p: import_paths(self.app, self, p))
        el.addWidget(dz)
        el.addStretch(1)
        self.stack.addWidget(empty)

        split = QSplitter(Qt.Horizontal)
        self.model = QStandardItemModel(0, len(COLS))
        self.model.setHorizontalHeaderLabels(COLS)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setIconSize(QSize(56, 32))
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.resizeSection(0, 70)
        self.table.setSortingEnabled(True)
        self.table.selectionModel().selectionChanged.connect(self._show_details)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.setAcceptDrops(True)
        split.addWidget(self.table)
        self.details = Card()
        self.details.setMinimumWidth(280)
        split.addWidget(self.details)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        self.stack.addWidget(split)
        sp = QWidget()
        spl = QVBoxLayout(sp)
        spl.setContentsMargins(0, 0, 0, 0)
        self.speech_info = label("", "muted", wrap=True)
        spl.addWidget(self.speech_info)
        self.speech = QListWidget()
        self.speech.setWordWrap(True)
        self.speech.itemActivated.connect(self._open_hit)
        spl.addWidget(self.speech, 1)
        self.stack.addWidget(sp)
        lay.addWidget(self.stack, 1)
        self.setAcceptDrops(True)
        self._fill_concepts()
        self.refresh()

    # glisser-déposer sur toute la page
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        import_paths(self.app, self, paths)

    def _fill_concepts(self) -> None:
        cur = self.concept.currentData()
        self.concept.blockSignals(True)
        self.concept.clear()
        self.concept.addItem("Tous les concepts", None)
        for c in self.app.concepts.list_concepts():
            self.concept.addItem(c.name, c.id)
        self.concept.setCurrentIndex(max(0, self.concept.findData(cur)))
        self.concept.blockSignals(False)

    def _speech_results(self, q: str) -> None:
        self.stack.setCurrentIndex(2)
        self.speech.clear()
        if len(q) < 2:
            self.speech_info.setText("Tapez au moins deux lettres : les phrases prononcées qui les contiennent "
                                     "s'affichent ici (médias transcrits uniquement). Double-clic pour y aller.")
            return
        hits = self.app.transcripts.search_all(q, limit=300)
        n_tr = self.app.db.query_one("SELECT COUNT(DISTINCT source_asset_id) n FROM transcript")["n"]
        self.speech_info.setText(f"{len(hits)}{'+' if len(hits) >= 300 else ''} passage(s) dans "
                                 f"{len({h[0] for h in hits})} média(s) — {n_tr} média(s) transcrit(s) au total. "
                                 "Double-clic pour ouvrir au bon moment.")
        for aid, name, _i, start, text in hits:
            it = QListWidgetItem(f"{name}  ·  {fmt_timecode(start)}\n{text.strip()}")
            it.setData(Qt.UserRole, (aid, start))
            self.speech.addItem(it)

    def _open_hit(self, item) -> None:
        aid, t = item.data(Qt.UserRole)
        win = self.window()
        if hasattr(win, "open_media_at"):
            win.open_media_at(aid, t)

    def refresh(self, *_):
        if getattr(self, "in_speech", None) is not None and self.in_speech.isChecked():
            self._speech_results(self.search.text().strip())
            return
        self._fill_concepts()
        rows = self.app.library.search(self.search.text().strip(), self.kind.currentData(),
                                       self.concept.currentData(), self.fav.isChecked())
        total = sum(self.app.library.counts().values())
        self.stack.setCurrentIndex(0 if total == 0 else 1)
        self.model.setRowCount(0)
        for r in rows:
            thumb = QStandardItem()
            if r["thumbnail_path"] and Path(r["thumbnail_path"]).exists():
                thumb.setIcon(QPixmap(r["thumbnail_path"]).scaled(56, 32, Qt.KeepAspectRatioByExpanding,
                                                                  Qt.SmoothTransformation))
            name = QStandardItem(("★ " if r["favorite"] else "") + r["file_name"]
                                 + ("  (introuvable)" if r["missing"] else ""))
            name.setData(r["id"], Qt.UserRole)
            if r["missing"]:
                name.setForeground(Qt.red)
            dur = QStandardItem(fmt_timecode(r["duration_s"]) if r["duration_s"] else "")
            dur.setData(r["duration_s"] or 0, Qt.UserRole + 1)
            res = f"{r['width']}×{r['height']}" if r["width"] else ""
            self.model.appendRow([thumb, name, QStandardItem(KIND_LABELS.get(r["kind"], r["kind"])), dur,
                                  QStandardItem(res), QStandardItem(r["concept_name"] or ""),
                                  QStandardItem(r["imported_at"][:16].replace("T", " "))])
        self._show_details()

    def _selected_id(self) -> int | None:
        idx = self.table.selectionModel().selectedRows(1) if self.table.selectionModel() else []
        return idx[0].data(Qt.UserRole) if idx else None

    def _show_details(self, *_):
        lay = self.details.lay
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
            elif it.layout():
                from .dashboard import _delete_layout
                _delete_layout(it.layout())
        aid = self._selected_id()
        a = self.app.library.get(aid) if aid else None
        if not a:
            lay.addWidget(label("Sélectionnez un média pour voir ses informations.", "faint", wrap=True))
            lay.addStretch(1)
            return
        if a["thumbnail_path"] and Path(a["thumbnail_path"]).exists():
            img = label()
            img.setPixmap(QPixmap(a["thumbnail_path"]).scaledToWidth(260, Qt.SmoothTransformation))
            lay.addWidget(img)
        t = label(a["file_name"], "h2", wrap=True)
        lay.addWidget(t)
        info = [("Type", KIND_LABELS.get(a["kind"], a["kind"])),
                ("Durée", fmt_timecode(a["duration_s"], True) if a["duration_s"] else None),
                ("Résolution", f"{a['width']} × {a['height']}" if a["width"] else None),
                ("Images/s", f"{a['fps']:g}" if a["fps"] else None),
                ("Codec vidéo", a["video_codec"]), ("Codec audio", a["audio_codec"]),
                ("Pistes audio", str(a["audio_tracks"]) if a["kind"] in ("video", "audio") else None),
                ("Taille", human_size(a["size_bytes"])),
                ("Provenance", a["origin_url"] or a["provenance"]),
                ("Emplacement", a["path"])]
        for k, v in info:
            if v:
                row = label(f"<span style='opacity:.6'>{k}</span><br>{v}", wrap=True)
                row.setTextInteractionFlags(Qt.TextSelectableByMouse)
                lay.addWidget(row)
        if a["missing"]:
            lay.addWidget(label("Le fichier source est introuvable à cet emplacement.", "error", wrap=True))
            rl = QHBoxLayout()
            rl.addWidget(button("Retrouver le fichier…", "primary", on_click=lambda: self._relink(aid)))
            rl.addWidget(button("Chercher dans un dossier…", on_click=self._relink_folder))
            lay.addLayout(rl)
        lay.addStretch(1)
        h = QHBoxLayout()
        h.addWidget(button("Retirer le favori" if a["favorite"] else "Favori",
                           on_click=lambda: (self.app.library.set_favorite(aid, not a["favorite"]), self.refresh())))
        h.addWidget(button("Afficher le fichier", on_click=lambda: reveal(a["path"])))
        lay.addLayout(h)

    def _menu(self, pos) -> None:
        aid = self._selected_id()
        if not aid:
            return
        m = QMenu(self)
        m.addAction("Afficher dans l'explorateur", lambda: reveal(self.app.library.get(aid)["path"]))
        m.addAction("Modifier les tags…", lambda: self._tags(aid))
        m.addSeparator()
        m.addAction("Retirer d'Extraits", lambda: self._remove(aid))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _relink(self, aid: int) -> None:
        from PySide6.QtWidgets import QFileDialog
        a = self.app.library.get(aid)
        path, _ = QFileDialog.getOpenFileName(self, f"Où est « {a['file_name']} » ?", str(Path(a["path"]).parent))
        if not path:
            return
        try:
            self.app.library.relink(aid, path)
        except ValueError as exc:
            if "pas identique" in str(exc) and QMessageBox.question(
                    self, "Retrouver le fichier", f"{exc}\n\nL'utiliser quand même ? Les montages qui l'utilisent "
                    "pourront ne plus tomber au bon endroit.") == QMessageBox.Yes:
                try:
                    self.app.library.relink(aid, path, force=True)
                except (ValueError, OSError) as exc2:
                    QMessageBox.warning(self, "Retrouver le fichier", str(exc2))
                    return
            else:
                QMessageBox.warning(self, "Retrouver le fichier", str(exc))
                return
        except OSError as exc:
            QMessageBox.warning(self, "Retrouver le fichier", str(exc))
            return
        self.refresh()

    def _relink_folder(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(self, "Dossier où chercher les fichiers introuvables")
        if not folder:
            return
        found = self.app.library.relink_folder(folder)
        left = self.app.db.query_one("SELECT COUNT(*) n FROM source_asset WHERE missing = 1")["n"]
        QMessageBox.information(self, "Fichiers introuvables", f"{len(found)} média(s) retrouvé(s). "
                                f"{left} toujours introuvable(s).")
        self.refresh()

    def _tags(self, aid: int) -> None:
        from PySide6.QtWidgets import QInputDialog
        cur = self.app.library.get(aid)["tags"]
        t, ok = QInputDialog.getText(self, "Tags", "Tags séparés par des virgules :", text=cur)
        if ok:
            self.app.library.set_tags(aid, t)
            self.refresh()

    def _remove(self, aid: int) -> None:
        if QMessageBox.question(self, "Retirer", "Retirer ce média d'Extraits ?\n"
                                "Le fichier sur le disque ne sera pas supprimé.") == QMessageBox.Yes:
            self.app.library.remove(aid)
            self.refresh()


def reveal(path: str) -> None:
    p = Path(path)
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(p)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)])
    else:
        target = p.parent if p.parent.exists() else Path.home()
        subprocess.Popen(["xdg-open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=os.environ.copy())
