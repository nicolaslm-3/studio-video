"""Barre des projets, à gauche des onglets Clipping, Traduction et Textes : nom de l'onglet et « + » (nouveau
projet), puis les projets, chacun avec le nom du fichier importé ; ses versions traduites juste en dessous, en
retrait. Clic : ouvrir dans l'onglet affiché ; clic droit : renommer ou supprimer."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QToolButton, QVBoxLayout, QWidget

from ..ai.translate import LANG_NAMES
from ..media.ffmpeg import fmt_timecode
from . import theme
from .widgets import ElidedLabel, tool


class ProjectItem(QFrame):
    """Un projet de la liste : nom, et en gris le fichier importé (ou la langue d'une version traduite)."""
    clicked = Signal(int)
    menu = Signal(int, object)

    def __init__(self, pid: int, name: str, sub: str, current: bool, indent: int = 0):
        super().__init__()
        self.pid = pid
        self.setProperty("role", "projitem")
        self.setProperty("selected", "true" if current else "false")
        self.setCursor(Qt.PointingHandCursor)
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 5 if current else 8, 12, 5 if current else 8)     # projet ouvert : moins haut
        v.setSpacing(1)
        self.name = ElidedLabel(name)                    # une ligne chacun, « … » si la place manque
        self.name.setStyleSheet("font-weight: 700;" + (" font-size: 12px;" if indent else ""))
        v.addWidget(self.name)
        self.sub = ElidedLabel(sub, "faint")
        v.addWidget(self.sub)
        self.setToolTip(f"{name}\n{sub}\n\nClic droit : renommer ou supprimer")
        self.name.setToolTip(self.toolTip())
        self.sub.setToolTip(self.toolTip())
        if indent:
            self.setContentsMargins(18 * indent, 0, 0, 0)                           # appartient au projet au-dessus

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.pid)
        super().mouseReleaseEvent(e)

    def contextMenuEvent(self, e) -> None:
        self.menu.emit(self.pid, e.globalPos())


class ProjectSidebar(QFrame):
    """La colonne des projets, sur toute la hauteur, pour l'onglet `mode` affiché."""

    def __init__(self, app, host, parent=None):
        super().__init__(parent)
        self.app, self.host = app, host
        self.mode = "smart_clipping"
        self.setProperty("role", "sidebar")
        self.setFixedWidth(250)
        sl = QVBoxLayout(self)
        sl.setContentsMargins(14, 16, 12, 14)
        sl.setSpacing(10)
        self.logo = QToolButton()
        self.logo.setIconSize(QSize(22, 22))
        self.logo.setEnabled(False)
        self.logo.setStyleSheet("border: none; background: transparent;")
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 17px; font-weight: 700;")
        self.title.setWordWrap(True)
        self.new_btn = tool("plus", "Nouveau projet", lambda: self.host.new_blank.emit(self.mode), size=30)
        head = QHBoxLayout()
        head.setSpacing(6)
        head.addWidget(self.logo)
        head.addWidget(self.title, 1)
        head.addWidget(self.new_btn)
        sl.addLayout(head)
        self.items = QVBoxLayout()
        self.items.setSpacing(3)
        pw = QWidget()
        pv = QVBoxLayout(pw)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.addLayout(self.items)
        pv.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sc.setWidget(pw)
        sl.addWidget(sc, 1)
        self._sig = None
        self._kw: dict[int, str] = {}                    # mots-clés par média transcrit

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self._sig = None
        name, ic, col, _d = theme.EXPORT_TAB if mode == "export" else theme.PARCOURS[mode]
        self.new_btn.setVisible(mode != "export")        # Export : on choisit un projet existant
        self.logo.setIcon(theme.icon(ic, col, 22))
        self.title.setText(name)
        self.new_btn.setIcon(theme.icon("plus", theme.color("muted"), 18))
        self.refresh()

    def _keywords(self, aid: int | None) -> str:
        """3 ou 4 mots-clés qui résument la vidéo, tirés de sa transcription (calculés une fois)."""
        if aid is None:
            return ""
        if aid not in self._kw:
            if not self.app.transcripts.has(aid):
                return ""
            from ..ai.highlights import _keywords
            tr = self.app.transcripts.get(aid)
            self._kw[aid] = " · ".join(_keywords([s.text for s in tr.segments], 4)) if tr else ""
        return self._kw[aid]

    def entries(self) -> list[tuple[int, str, str, int]]:
        """(projet, nom affiché, ligne grise, retrait) dans l'ordre d'affichage : chaque projet, puis en retrait ses
        versions traduites et ses extraits devenus des vidéos (et leurs versions). Nom : celui du projet (le nom du média s'il n'en a pas reçu d'autre) ; en gris,
        des mots-clés de la transcription."""
        rows = list(self.app.projects.recent(200))
        data = {r["id"]: json.loads(r["data_json"] or "{}") for r in rows}
        files = {r["id"]: r["file_name"] for r in self.app.db.query(
            "SELECT id, file_name FROM source_asset WHERE kind = 'video'")}
        latest = {r["project_id"]: r["id"] for r in self.app.db.query(
            "SELECT project_id, id FROM source_asset WHERE kind = 'video' AND project_id IS NOT NULL "
            "ORDER BY id")}
        names = {r["id"]: r["name"] for r in rows}
        by_id = {r["id"]: r for r in rows}
        children: dict[int, list] = {}                   # versions traduites, puis extraits devenus projets
        for r in rows:
            par = data[r["id"]].get("parent")
            if par in names:
                children.setdefault(par, []).append(r)
        for r in sorted(rows, key=lambda r: r["id"]):
            for c in (data[r["id"]].get("clip_projects") or {}).values():
                if c in by_id and c != r["id"] and data[c].get("parent") not in names                         and by_id[c] not in children.get(r["id"], []):
                    children.setdefault(r["id"], []).append(by_id[c])
        placed = {c["id"] for cs in children.values() for c in cs}

        def video(pid: int) -> int | None:
            v = data[pid].get("project_video")
            return v if v in files else latest.get(pid)

        def summary(pid: int) -> str:
            aid = video(pid)
            if aid is None:
                return "Pas encore de vidéo"
            return self._keywords(aid) or "Transcription à venir"

        def shown_name(r) -> str:
            aid = video(r["id"])
            if aid is not None and self.app.projects.is_auto_name(r["name"]):
                return Path(files[aid]).stem or r["name"]
            return r["name"]

        def clip_range(pid: int) -> str:
            cid = data[pid].get("clip_of")
            r = self.app.candidates.get(cid) if cid else None
            return f"Extrait · {fmt_timecode(r['start_s'])} – {fmt_timecode(r['end_s'])}" if r else summary(pid)

        out = []

        def add(r, depth: int, parent_name: str) -> None:
            pid = r["id"]
            d = data[pid]
            if depth == 0:
                out.append((pid, shown_name(r), summary(pid), 0))
            elif d.get("version_lang"):
                lang = d["version_lang"]
                prefix = parent_name + " · "
                shown = r["name"][len(prefix):] if r["name"].startswith(prefix) else r["name"]
                out.append((pid, shown, LANG_NAMES.get(lang, lang.upper()).capitalize() + " · " + summary(pid),
                            depth))
            else:                                       # un extrait du projet au-dessus, devenu une vidéo
                out.append((pid, r["name"], clip_range(pid), depth))
            if depth < 2:
                kids = children.get(pid, [])
                kids = sorted([k for k in kids if data[k["id"]].get("version_lang")],
                              key=lambda k: data[k["id"]]["version_lang"]) +                     [k for k in kids if not data[k["id"]].get("version_lang")]
                for k in kids:
                    add(k, depth + 1, r["name"])

        for r in rows:
            if r["id"] not in placed:
                add(r, 0, "")
        return out

    def refresh(self) -> None:
        """Liste à jour ; identique (rien de modifié), elle n'est pas reconstruite."""
        entries = self.entries()
        sig = (tuple(entries), self.host.project_id, self.mode)
        if sig == self._sig:
            return
        self._sig = sig
        while self.items.count():
            w = self.items.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)                  # sinon son affichage différé en fait une fenêtre
                w.deleteLater()
        for pid, name, sub, child in entries:
            it = ProjectItem(pid, name, sub, pid == self.host.project_id, indent=child)
            it.clicked.connect(lambda p: self.host.open_from_list(p, self.mode))
            it.menu.connect(lambda p, pos: self.host.project_menu(p, self.mode, self, pos))
            self.items.addWidget(it)
