"""Barre des vidéos, à gauche des onglets Clipping, Traduction, Textes et Export : nom de l'onglet et « + » (nouvelle
vidéo), puis les vidéos, chacune avec des mots-clés de sa transcription ; ses versions traduites juste en dessous, en
retrait. Clic : ouvrir dans l'onglet affiché ; au survol, trois petits boutons : renommer, archiver (menu des trois
barres → Archives), supprimer — aussi au clic droit."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QToolButton, QVBoxLayout, QWidget

from . import theme
from .widgets import ElidedLabel, color_dot, tool


class ProjectItem(QFrame):
    """Une vidéo de la liste : en haut son concept (point de sa couleur), dessous son nom ; au survol, renommer,
    archiver, supprimer."""
    clicked = Signal(int)
    menu = Signal(int, object)
    action = Signal(int, str)                           # « rename », « archive » ou « delete »

    def __init__(self, pid: int, name: str, sub: str, current: bool, indent: int = 0, color: str = ""):
        super().__init__()
        self.pid = pid
        self.setProperty("role", "projitem")
        self.setProperty("selected", "true" if current else "false")
        self.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 5 if current else 8, 6, 5 if current else 8)     # vidéo ouverte : moins haute
        row.setSpacing(2)
        v = QVBoxLayout()
        v.setSpacing(1)
        row.addLayout(v, 1)
        self.actions = QWidget()
        self.actions.setAttribute(Qt.WA_TranslucentBackground)
        self.actions.setStyleSheet("QWidget { background: transparent; } QToolButton { background: transparent; "
                                   "border: none; border-radius: 6px; } QToolButton:hover { "
                                   "background: rgba(128, 128, 128, 0.22); }")
        al = QHBoxLayout(self.actions)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(0)
        for kind, ic, tip in (("rename", "pencil", "Renommer"), ("archive", "archive", "Archiver (menu ☰ → Archives)"),
                              ("delete", "trash-2", "Supprimer")):
            b = tool(ic, tip, lambda _=False, k=kind: self.action.emit(self.pid, k), size=24, icon_px=14)
            al.addWidget(b)
        self.actions.hide()
        row.addWidget(self.actions)
        top = QHBoxLayout()
        top.setSpacing(5)
        self.dot = color_dot(color or theme.color("faint"), 7)
        top.addWidget(self.dot)
        self.sub = ElidedLabel(sub, "faint")             # le concept
        top.addWidget(self.sub, 1)
        v.addLayout(top)
        self.name = ElidedLabel(name)                    # une ligne chacun, « … » si la place manque
        self.name.setStyleSheet("font-weight: 700;" + (" font-size: 12px;" if indent else ""))
        v.addWidget(self.name)
        self.setToolTip(f"{name}\n{sub}")
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

    def enterEvent(self, e) -> None:
        self.actions.show()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self.actions.hide()
        super().leaveEvent(e)


class ProjectSidebar(QFrame):
    """La colonne des vidéos, sur toute la hauteur, pour l'onglet `mode` affiché."""

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
        self.new_btn = tool("plus", "Nouvelle vidéo", lambda: self.host.new_blank.emit(self.mode), size=30)
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

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self._sig = None
        name, ic, col, _d = theme.EXPORT_TAB if mode == "export" else theme.PARCOURS[mode]
        self.new_btn.setVisible(mode != "export")        # Export : on choisit une vidéo existante
        self.logo.setIcon(theme.icon(ic, col, 22))
        self.title.setText(name)
        self.new_btn.setIcon(theme.icon("plus", theme.color("muted"), 18))
        self.refresh()

    def entries(self) -> list[tuple[int, str, str, int]]:
        """(vidéo, nom affiché, concept, retrait) dans l'ordre d'affichage : chaque vidéo, puis en retrait ses
        versions traduites (« VEN-Nom de la vidéo ») et ses extraits devenus des vidéos. Le nom : celui de la vidéo
        (le nom du média tant qu'elle n'en a pas reçu d'autre)."""
        rows = list(self.app.projects.recent(200))
        data = {r["id"]: json.loads(r["data_json"] or "{}") for r in rows}
        files = {r["id"]: r["file_name"] for r in self.app.db.query(
            "SELECT id, file_name FROM source_asset WHERE kind = 'video'")}
        latest = {r["project_id"]: r["id"] for r in self.app.db.query(
            "SELECT project_id, id FROM source_asset WHERE kind = 'video' AND project_id IS NOT NULL "
            "ORDER BY id")}
        names = {r["id"]: r["name"] for r in rows}
        by_id = {r["id"]: r for r in rows}
        drafts = self.app.concepts.DRAFTS
        self.colors = {r["id"]: ("" if r["concept_name"] == drafts else r["concept_color"]) for r in rows}
        children: dict[int, list] = {}                   # versions traduites, puis extraits devenus vidéos
        for r in rows:
            par = data[r["id"]].get("parent")
            if par in names:
                children.setdefault(par, []).append(r)
        for r in sorted(rows, key=lambda r: r["id"]):
            for c in (data[r["id"]].get("clip_projects") or {}).values():
                if c in by_id and c != r["id"] and data[c].get("parent") not in names \
                        and by_id[c] not in children.get(r["id"], []):
                    children.setdefault(r["id"], []).append(by_id[c])
        placed = {c["id"] for cs in children.values() for c in cs}

        def concept(r) -> str:
            return "Sans concept" if r["concept_name"] == drafts else r["concept_name"]

        def shown_name(r) -> str:
            v = data[r["id"]].get("project_video")
            aid = v if v in files else latest.get(r["id"])
            if aid is not None and self.app.projects.is_auto_name(r["name"]):
                return Path(files[aid]).stem or r["name"]
            return r["name"]

        out = []

        def add(r, depth: int, parent_name: str) -> None:
            pid = r["id"]
            d = data[pid]
            if depth and d.get("version_lang"):            # version traduite : « VEN-Nom de la vidéo »
                out.append((pid, f"V{d['version_lang'].upper()}-{parent_name}", concept(r), depth))
            else:
                out.append((pid, shown_name(r) if depth == 0 else r["name"], concept(r), depth))
            if depth < 2:
                kids = children.get(pid, [])
                kids = sorted([k for k in kids if data[k["id"]].get("version_lang")],
                              key=lambda k: data[k["id"]]["version_lang"]) + \
                    [k for k in kids if not data[k["id"]].get("version_lang")]
                for k in kids:
                    add(k, depth + 1, out[-1][1] if not d.get("version_lang") else parent_name)

        for r in rows:
            if r["id"] not in placed:
                add(r, 0, "")
        return out

    def refresh(self) -> None:
        """Liste à jour ; identique (rien de modifié), elle n'est pas reconstruite."""
        entries = self.entries()
        sig = (tuple(entries), tuple(sorted(self.colors.items())), self.host.project_id, self.mode)
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
            it = ProjectItem(pid, name, sub, pid == self.host.project_id, indent=child,
                             color=self.colors.get(pid, ""))
            it.clicked.connect(lambda p: self.host.open_from_list(p, self.mode))
            it.menu.connect(lambda p, pos: self.host.project_menu(p, self.mode, self, pos))
            it.action.connect(lambda p, k: self.host.project_action(p, k, self.mode, self))
            self.items.addWidget(it)
