"""Espaces simplifiés : Clipping (une liste d'extraits à cocher) et Traduction (un formulaire en 4 étapes).

Un projet = une vidéo, qui passe d'un écran à l'autre : même montage, même format. Clipping choisit le passage
(un projet par extrait coché), Traduction prépare les langues, l'onglet Export (`export_view.py`) exporte et
publie. L'import est dans l'en-tête commun du Studio. Les moteurs sont ceux du Studio : extraits
(`find_highlights`, `auto_clips`), suivi de la personne qui parle (`reframe`), traduction (`translate_video`)."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QProgressBar, QPushButton,
                               QScrollArea, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from ..ai.highlights import CATEGORY_HELP, CATEGORY_HELP_COMMON, CLIP_DURATIONS, DEFAULT_CRITERIA, Candidate, Request, criteria_request
from ..ai.lexicon import CATEGORIES
from ..core import AppContext
from ..edit import translation as trn
from ..edit.batch import BatchOptions, clip_timeline
from ..edit.model import Timeline
from ..media.ffmpeg import fmt_timecode
from . import theme
from .importing import DropZone, import_paths, pick_and_import
from .montage_player import MontagePlayer
from .widgets import button, label, tool

VIDEO_FILTER = "Vidéos (*.mp4 *.mov *.mkv *.webm *.avi *.m4v *.mts *.wmv)"


def _repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


def _row(*items, spacing: int = 8) -> QHBoxLayout:
    h = QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(spacing)
    for it in items:
        if it == "stretch":
            h.addStretch(1)
        elif isinstance(it, QHBoxLayout):
            h.addLayout(it)
        else:
            h.addWidget(it)
    return h


def _active_job(app: AppContext, kind: str, key: str, value) -> dict | None:
    """Tâche en file ou en cours de ce type pour ce média / projet (progression affichée dans l'écran)."""
    r = app.db.query_one(f"SELECT id, status, progress, message FROM job WHERE kind = ? AND status IN "
                         f"('pending','running') AND json_extract(params_json, '$.{key}') = ? ORDER BY id DESC "
                         "LIMIT 1", (kind, value))
    return dict(r) if r else None


def _last_job(app: AppContext, kind: str, key: str, value) -> dict | None:
    r = app.db.query_one(f"SELECT id, status, error, result_json FROM job WHERE kind = ? AND "
                         f"json_extract(params_json, '$.{key}') = ? ORDER BY id DESC LIMIT 1", (kind, value))
    if not r:
        return None
    d = dict(r)
    d["result"] = json.loads(d.pop("result_json") or "{}")
    return d


def _tc(t: float) -> str:
    """Temps court façon « 4:43.3 » (heures si besoin)."""
    t = max(0.0, float(t or 0))
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h}:{m:02d}:{s:04.1f}" if h else f"{m}:{s:04.1f}"


def _open_folder(path: str | Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class _View(QWidget):
    """Base commune : projet ouvert dans le Studio (`host`), import, rafraîchissement sur les tâches."""
    open_editor = Signal()
    open_settings = Signal()

    def __init__(self, app: AppContext, host, parent=None):
        super().__init__(parent)
        self.app, self.host = app, host
        self._refresh_timer = QTimer(self, singleShot=True, interval=120)
        self._refresh_timer.timeout.connect(self.refresh)

    @property
    def pid(self) -> int | None:
        return self.host.project_id

    def import_extra(self) -> dict | None:
        return None

    def browse(self) -> None:
        if self.pid and pick_and_import(self.app, self, self.pid, self.import_extra(), VIDEO_FILTER):
            self.note("Import de la vidéo en cours…")

    def dropped(self, paths: list[str]) -> None:
        if self.pid and import_paths(self.app, self, paths, self.pid, self.import_extra()):
            self.note("Import de la vidéo en cours…")

    def note(self, text: str) -> None:
        pass

    def video(self) -> dict | None:
        """La vidéo du projet (la même dans tous les onglets)."""
        return self.host.project_video()

    def schedule_refresh(self) -> None:
        self._refresh_timer.start()

    def refresh(self) -> None:
        pass


# -- Clipping ---------------------------------------------------------------------------------------------
class ScrubTime(QFrame):
    """Temps réglable à la souris, comme dans Premiere Pro : cliquer-glisser sur la valeur vers la gauche
    diminue, vers la droite augmente (Maj : plus vite, Ctrl : plus fin) ; molette : ±0,1 s ; − / + : ±0,5 s ;
    double-clic : saisie au clavier (« 4:43.3 » ou secondes)."""
    edited = Signal(float)                   # valeur validée (relâchement, molette, boutons, saisie)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "scrub")
        self._v, self._lo, self._hi = 0.0, 0.0, 1e6
        self._drag: tuple[float, float] | None = None
        h = QHBoxLayout(self)
        h.setContentsMargins(4, 2, 4, 2)
        h.setSpacing(2)
        self.minus, self.plus = QToolButton(), QToolButton()
        for b, txt, d in ((self.minus, "−", -0.5), (self.plus, "+", 0.5)):
            b.setText(txt)
            b.setAutoRaise(True)
            b.setToolTip(f"{d:+.1f} s".replace(".", ","))
            b.clicked.connect(lambda _=False, d=d: self._commit(self._v + d))
        self.val = QLabel()
        self.val.setAlignment(Qt.AlignCenter)
        self.val.setCursor(Qt.SizeHorCursor)
        self.val.setToolTip("Glisser à gauche ou à droite pour régler (Maj : plus vite, Ctrl : plus fin), "
                            "molette : ±0,1 s, double-clic : saisir")
        self.val.setStyleSheet("font-weight: 700; font-size: 14px; background: transparent;")
        self.edit = QLineEdit()
        self.edit.setAlignment(Qt.AlignCenter)
        self.edit.hide()
        self.edit.editingFinished.connect(self._typed)
        h.addWidget(self.minus)
        h.addWidget(self.val, 1)
        h.addWidget(self.edit, 1)
        h.addWidget(self.plus)
        self.val.installEventFilter(self)
        self._show()

    def value(self) -> float:
        return self._v

    def setValue(self, v: float) -> None:
        self._v = max(self._lo, min(self._hi, float(v)))
        self._show()

    def setRange(self, lo: float, hi: float) -> None:
        self._lo, self._hi = lo, max(lo, hi)

    def _show(self) -> None:
        self.val.setText(_tc(self._v))

    def _commit(self, v: float) -> None:
        self.setValue(v)
        self.edited.emit(self._v)

    def _typed(self) -> None:
        txt = self.edit.text().strip().replace(",", ".")
        self.edit.hide()
        self.val.show()
        try:
            parts = [float(p) for p in txt.split(":")]
        except ValueError:
            return
        v = 0.0
        for p in parts:
            v = v * 60 + p
        self._commit(v)

    def eventFilter(self, obj, e) -> bool:
        t = e.type()
        if t == QEvent.MouseButtonPress and e.button() == Qt.LeftButton and self.isEnabled():
            self._drag = (e.globalPosition().x(), self._v)
            return True
        if t == QEvent.MouseMove and self._drag is not None:
            dx = e.globalPosition().x() - self._drag[0]
            mods = e.modifiers()
            rate = 0.5 if mods & Qt.ShiftModifier else 0.02 if mods & Qt.ControlModifier else 0.1
            self.setValue(self._drag[1] + round(dx / 2) * rate)       # 2 pixels = un cran
            return True
        if t == QEvent.MouseButtonRelease and self._drag is not None:
            start = self._drag[1]
            self._drag = None
            if abs(self._v - start) > 1e-6:
                self.edited.emit(self._v)
            return True
        if t == QEvent.MouseButtonDblClick and self.isEnabled():
            self._drag = None
            self.val.hide()
            self.edit.setText(_tc(self._v))
            self.edit.show()
            self.edit.setFocus()
            self.edit.selectAll()
            return True
        return super().eventFilter(obj, e)

    def wheelEvent(self, e) -> None:
        if not self.isEnabled():
            return
        steps = e.angleDelta().y() / 120
        if steps:
            self._commit(self._v + steps * (1.0 if e.modifiers() & Qt.ShiftModifier else 0.1))


class CriteriaBox(QWidget):
    """Critères de recherche des extraits : catégories (aucune cochée = politique ; au survol, comment chacune
    est cherchée), durée, nombre."""
    changed = Signal(dict)

    def __init__(self, full: bool = True, parent=None):
        super().__init__(parent)
        self._loading = False
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        grid = QGridLayout()
        grid.setSpacing(6)
        self.cats: dict[str, QPushButton] = {}
        for i, (k, name) in enumerate(CATEGORIES.items()):
            b = button(name, "chip", tooltip=CATEGORY_HELP[k] + "\n\n" + CATEGORY_HELP_COMMON)
            b.setCheckable(True)
            b.toggled.connect(self._emit)
            grid.addWidget(b, i // 2, i % 2)
            self.cats[k] = b
        v.addLayout(grid)
        self.durs = QButtonGroup(self)
        self.durs.setExclusive(True)
        drow = [label("Durée", "muted")]
        for k, (name, *_x) in CLIP_DURATIONS.items():
            b = button(name, "chip")
            b.setCheckable(True)
            b.setProperty("dur", k)
            self.durs.addButton(b)
            drow.append(b)
        self.durs.buttonToggled.connect(lambda _b, on: on and self._emit())
        v.addLayout(_row(*drow, "stretch", spacing=6))
        self.count = QComboBox()
        for n in (5, 8, 12, 20):
            self.count.addItem(f"{n} extraits", n)
        self.count.currentIndexChanged.connect(self._emit)
        v.addLayout(_row(label("Nombre", "muted"), self.count, "stretch", spacing=6))

    def load(self, c: dict) -> None:
        self._loading = True
        try:
            for k, b in self.cats.items():
                b.setChecked(k in c["categories"])
            for b in self.durs.buttons():
                b.setChecked(b.property("dur") == c["duration"])
            self.count.setCurrentIndex(max(0, self.count.findData(int(c["count"]))))
        finally:
            self._loading = False

    def value(self) -> dict:
        d = self.durs.checkedButton()
        return {"categories": [k for k, b in self.cats.items() if b.isChecked()],
                "duration": d.property("dur") if d else "moyen", "count": self.count.currentData() or 8}

    def _emit(self, *_a) -> None:
        if not self._loading:
            self.changed.emit(self.value())


class MomentCard(QFrame):
    """Un extrait proposé : case à cocher, titre, accroche, position et durée, vidéo déjà créée."""
    clicked = Signal(int)
    checked = Signal(int, bool)

    def __init__(self, row, title: str, made: str, parent=None):
        super().__init__(parent)
        self.cid = row["id"]
        self.setProperty("role", "moment")
        self.setCursor(Qt.PointingHandCursor)
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 9, 14, 9)
        h.setSpacing(12)
        self.box = QCheckBox()
        self.box.setChecked(row["state"] == "accepted")
        self.box.toggled.connect(lambda on: self.checked.emit(self.cid, on))
        h.addWidget(self.box, 0, Qt.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        hook = (row["hook"] or "").strip()
        t = label(title[:90])
        t.setStyleSheet("font-size: 13px; font-weight: 700;")
        texts.addWidget(t)
        sub = (hook if hook and hook != title else (row["rationale"] or "")).strip()
        if sub:
            s = label(sub[:140], "muted", hint=False)
            s.setStyleSheet("font-size: 12px;")
            s.setToolTip(row["rationale"] or "")
            texts.addWidget(s)
        h.addLayout(texts, 1)
        right = QVBoxLayout()
        right.setSpacing(3)
        right.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        dur = (row["end_s"] or 0) - (row["start_s"] or 0)
        pos = label(f"{_tc(row['start_s'])} · {dur:.0f} s", "faint")
        pos.setStyleSheet("font-size: 12px;")
        right.addWidget(pos, 0, Qt.AlignRight)
        if made:
            right.addWidget(label(made, "ready"), 0, Qt.AlignRight)
        h.addLayout(right)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", "true" if on else "false")
        _repolish(self)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.cid)
        super().mouseReleaseEvent(e)


class ProjectItem(QFrame):
    """Un projet de la liste : clic = ouvrir, clic droit = renommer ou supprimer."""
    clicked = Signal(int)
    menu = Signal(int, object)

    def __init__(self, pid: int, name: str, status: str, current: bool):
        super().__init__()
        self.pid = pid
        self.setProperty("role", "projitem")
        self.setProperty("selected", "true" if current else "false")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Clic droit : renommer ou supprimer")
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(2)
        t = label(name)
        t.setStyleSheet("font-weight: 700;")
        v.addWidget(t)
        v.addWidget(label(status, "faint"))

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.pid)
        super().mouseReleaseEvent(e)

    def contextMenuEvent(self, e) -> None:
        self.menu.emit(self.pid, e.globalPos())


STEPS = ("Vidéo", "Transcription", "Sélection", "Vidéo")

# Onglets vers lesquels la vidéo du projet peut passer ensuite (le premier est proposé d'office)
NEXT_TABS = {"smart_clipping": ("translation", "texts", "free_edit", "export"),
             "translation": ("texts", "free_edit", "export")}


class NextTab(QWidget):
    """« Suivant : <onglet> » : la vidéo du projet passe telle quelle dans l'onglet suivant ; la petite flèche
    propose les autres onglets."""

    def __init__(self, host, mode: str, parent=None):
        super().__init__(parent)
        self.host, self.tabs = host, NEXT_TABS[mode]
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        name = self._name(self.tabs[0])
        self.go = button(f"Suivant : {name}", "big", tooltip=f"La vidéo de ce projet passe dans l'onglet {name}",
                         on_click=lambda: self.host.goto_tab.emit(self.tabs[0]))
        self.go.setIcon(theme.icon("arrow-right", "#FFFFFF", 18))
        self.go.setLayoutDirection(Qt.RightToLeft)            # flèche après le texte
        h.addWidget(self.go, 1)
        self.more = tool("chevron-down", "Envoyer vers un autre onglet", size=46)
        self.more.setPopupMode(QToolButton.InstantPopup)
        self.more.setProperty("kind", "soft")
        self.more.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        m = QMenu(self.more)
        for key in self.tabs:
            nm, ic, col = theme.PARCOURS[key][:3] if key in theme.PARCOURS else theme.EXPORT_TAB[:3]
            m.addAction(theme.icon(ic, col, 16), nm, lambda k=key: self.host.goto_tab.emit(k))
        self.more.setMenu(m)
        h.addWidget(self.more)

    @staticmethod
    def _name(key: str) -> str:
        return theme.PARCOURS[key][0] if key in theme.PARCOURS else theme.EXPORT_TAB[0]


class ClippingView(_View):
    """Clipping : vidéo → transcription (pendant laquelle on choisit ses critères) → extraits proposés → on
    coche → chaque extrait coché devient une vidéo (le premier, celle de ce projet ; les autres, un projet
    chacun), prête pour Traduction, Montage et Export."""

    def __init__(self, app: AppContext, host, export_dir: Path, parent=None):
        super().__init__(app, host, parent)
        self.export_dir = export_dir
        self.sel: int | None = None
        self.cards: dict[int, MomentCard] = {}
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- colonne des projets
        side = QFrame()
        side.setProperty("role", "sidebar")
        side.setFixedWidth(250)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 16, 14, 14)
        sl.setSpacing(10)
        name, ic, col, _d = theme.PARCOURS["smart_clipping"]
        logo = QToolButton()
        logo.setIcon(theme.icon(ic, col, 22))
        logo.setIconSize(QSize(22, 22))
        logo.setEnabled(False)
        logo.setStyleSheet("border: none; background: transparent;")
        brand = label("Clipping", "h2")
        brand.setStyleSheet("font-size: 18px; font-weight: 700;")
        sl.addLayout(_row(logo, brand, "stretch", spacing=6))
        sl.addWidget(button("Nouveau projet", tooltip="Nouveau projet", icon="plus",
                            on_click=lambda: host.new_blank.emit("smart_clipping")))
        self.projects = QVBoxLayout()
        self.projects.setSpacing(4)
        pw = QWidget()
        pv = QVBoxLayout(pw)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.addLayout(self.projects)
        pv.addStretch(1)
        ps = QScrollArea()
        ps.setWidgetResizable(True)
        ps.setFrameShape(QFrame.NoFrame)
        ps.setWidget(pw)
        sl.addWidget(ps, 1)
        root.addWidget(side)

        # -- contenu
        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(24, 18, 24, 12)
        ml.setSpacing(12)
        # état du projet : une ligne (sans encadré), progression et action éventuelle
        self.banner = QWidget()
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(2, 0, 0, 0)
        bl.setSpacing(12)
        bv = QVBoxLayout()
        bv.setSpacing(1)
        self.b_title = label("")
        self.b_title.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.b_title.setWordWrap(True)
        self.b_desc = label("", "muted", hint=False)
        self.b_desc.setWordWrap(True)
        for w in (self.b_title, self.b_desc):
            bv.addWidget(w)
        bl.addLayout(bv, 1)
        self.b_progress = QProgressBar()
        self.b_progress.setRange(0, 100)
        self.b_progress.setFixedWidth(160)
        bl.addWidget(self.b_progress)
        self.b_action = button("", "primary")
        self.b_action.clicked.connect(lambda: self._banner_fn and self._banner_fn())
        bl.addWidget(self.b_action)
        self._banner_fn = None
        ml.addWidget(self.banner)

        self.meta = label("", "muted", wrap=True, hint=False)      # en bas de la colonne de droite
        steps = QHBoxLayout()
        steps.setSpacing(6)
        self.step_bars, self.step_lbls = [], []
        for name in STEPS:
            col = QVBoxLayout()
            col.setSpacing(6)
            bar = QFrame()
            bar.setProperty("role", "stepbar")
            bar.setFixedHeight(4)
            lb = label(name, "muted")
            col.addWidget(bar)
            col.addWidget(lb)
            steps.addLayout(col, 1)
            self.step_bars.append(bar)
            self.step_lbls.append(lb)
        ml.addLayout(steps)

        self.body = QStackedWidget()
        ml.addWidget(self.body, 1)
        # page 0 : pas encore de vidéo
        dz = DropZone("Glisse ta vidéo ici")
        dz.dropped.connect(self.dropped)
        self.body.addWidget(dz)
        # page 1 : extraits + clip final
        work = QWidget()
        wl = QHBoxLayout(work)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(18)
        left = QVBoxLayout()
        left.setSpacing(8)
        self.count_lbl = label("Extraits", "h2")
        self.all_box = QCheckBox("Tout cocher")
        self.all_box.clicked.connect(self._check_all)
        left.addLayout(_row(self.count_lbl, "stretch", self.all_box))
        self.left_stack = QStackedWidget()
        # extraits
        self.list_host = QWidget()
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(0, 0, 6, 0)
        self.list_lay.setSpacing(6)
        self.list_lay.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setWidget(self.list_host)
        self.left_stack.addWidget(sc)
        # pas encore d'extraits : les critères sont demandés (pendant la transcription)
        ask = QFrame()
        ask.setProperty("role", "card")
        al = QVBoxLayout(ask)
        al.setContentsMargins(24, 20, 24, 20)
        al.setSpacing(10)
        q = label("Sur quels critères chercher les extraits ?")
        q.setStyleSheet("font-size: 16px; font-weight: 700;")
        al.addWidget(q)
        self.ask_note = label("", "muted", wrap=True, hint=False)
        al.addWidget(self.ask_note)
        self.ask_crit = CriteriaBox(full=False)
        self.ask_crit.changed.connect(self._save_criteria)
        al.addWidget(self.ask_crit)
        self.ask_go = button("Chercher les extraits", "primary", icon="sparkles", on_click=self._find)
        al.addLayout(_row(self.ask_go, "stretch"))
        al.addStretch(1)
        self.left_stack.addWidget(ask)
        left.addWidget(self.left_stack, 1)
        self.more_btn = button("Proposer d'autres extraits", "ghost", icon="sparkles", on_click=self._find)
        left.addLayout(_row(self.more_btn, "stretch"))
        wl.addLayout(left, 3)

        # colonne de droite : clip final, réglages de l'extrait, critères
        rc = QWidget()
        right = QVBoxLayout(rc)
        right.setContentsMargins(0, 0, 8, 0)
        right.setSpacing(8)
        self.player = MontagePlayer()
        self.player.note.setMaximumHeight(0)              # pas de note technique sous l'aperçu
        self.player.setMinimumHeight(340)
        right.addWidget(self.player)
        self.follow = tool("focus", "Suivre la personne qui parle : la vidéo est analysée et le cadre suit le "
                                    "visage de celui qui parle (vidéo horizontale mise en vertical). Désactivé : "
                                    "cadre centré.", checkable=True)
        self.follow.toggled.connect(self._toggle_follow)
        self.player.add_tool(self.follow)                 # à gauche des zones TikTok / Reels, même taille
        self.frame_note = label("", "faint", wrap=True, hint=False)
        right.addWidget(self.frame_note)
        self.edit_box = QWidget()                        # réglages de l'extrait sélectionné
        ed = QVBoxLayout(self.edit_box)
        ed.setContentsMargins(0, 0, 0, 0)
        ed.setSpacing(8)
        self.in_s, self.out_s = ScrubTime(), ScrubTime()
        self.in_s.edited.connect(lambda v: self._edit_times(start=v))
        self.out_s.edited.connect(lambda v: self._edit_times(end=v))
        self.fin_lbl = label("Fin", "h2")
        tg = QGridLayout()
        tg.setHorizontalSpacing(10)
        tg.setVerticalSpacing(4)
        tg.addWidget(label("Début", "h2"), 0, 0)
        tg.addWidget(self.fin_lbl, 0, 1)
        tg.addWidget(self.in_s, 1, 0)
        tg.addWidget(self.out_s, 1, 1)
        self.here_in = button("Début ici", tooltip="Le début de l'extrait à l'instant affiché dans l'aperçu",
                              on_click=lambda: self._here(True))
        self.here_out = button("Fin ici", tooltip="La fin de l'extrait à l'instant affiché dans l'aperçu",
                               on_click=lambda: self._here(False))
        tg.addWidget(self.here_in, 2, 0, Qt.AlignHCenter)          # chacun centré sous son réglage
        tg.addWidget(self.here_out, 2, 1, Qt.AlignHCenter)
        ed.addLayout(tg)
        right.addWidget(self.edit_box)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setProperty("role", "sep")
        right.addSpacing(6)
        right.addWidget(line)
        right.addWidget(label("Critères de recherche", "h2"))
        self.crit = CriteriaBox()
        self.crit.changed.connect(self._save_criteria)
        right.addWidget(self.crit)
        right.addStretch(1)
        right.addWidget(self.meta)
        rs = QScrollArea()
        rs.setWidgetResizable(True)
        rs.setFrameShape(QFrame.NoFrame)
        rs.setWidget(rc)
        col_r = QVBoxLayout()
        col_r.setSpacing(10)
        col_r.addWidget(rs, 1)
        self.gen = button("Créer les vidéos", "big", on_click=self._create)
        col_r.addWidget(self.gen)
        self.next = NextTab(host, "smart_clipping")       # vidéo prête : vers l'onglet suivant
        col_r.addWidget(self.next)
        rw = QWidget()
        rw.setLayout(col_r)
        rw.setFixedWidth(400)
        wl.addWidget(rw)
        self.body.addWidget(work)
        root.addWidget(main, 1)

    # -- données ------------------------------------------------------------------------
    def import_extra(self) -> dict:
        return {"auto_clips": True}          # transcription puis extraits, d'office

    def _data(self) -> dict:
        return self.app.projects.load_data(self.pid) if self.pid else {}

    def _made(self, data: dict) -> dict[int, int]:
        """Extraits déjà devenus une vidéo : {extrait: projet} (projets encore existants)."""
        return {int(k): v for k, v in (data.get("clip_projects") or {}).items() if self.app.projects.get(v)}

    def _criteria(self) -> dict:
        return {**DEFAULT_CRITERIA, **(self._data().get("clip_criteria") or {})}

    def _save_criteria(self, c: dict) -> None:
        if not self.pid:
            return
        data = self._data()
        data["clip_criteria"] = {**self._criteria(), **c}
        self.app.projects.save_data(self.pid, data)
        for box in (self.crit, self.ask_crit):
            if box is not self.sender():
                box.load(data["clip_criteria"])
        self._ask_text()

    def _title_of(self, row, data: dict | None = None) -> str:
        return (row["subject"] or "").strip() or (row["hook"] or "").strip() or "Extrait"

    def _rows(self):
        return [r for r in self.app.candidates.list(self.pid) if r["state"] != "rejected"] if self.pid else []

    # -- affichage ----------------------------------------------------------------------
    def refresh(self) -> None:
        if not self.pid:
            return
        self._fill_projects()
        v = self.video()
        if v:
            missing = "  · fichier introuvable" if v.get("missing") else ""
            self.meta.setText(f"Vidéo source : {v['file_name']} · {fmt_timecode(v['duration_s'] or 0)}{missing}")
        else:
            self.meta.setText("Aucune vidéo pour l'instant")
        self.body.setCurrentIndex(1 if v else 0)
        data = self._data()
        crit = {**DEFAULT_CRITERIA, **(data.get("clip_criteria") or {})}
        self.crit.load(crit)
        self.ask_crit.load(crit)
        self.follow.blockSignals(True)
        self.follow.setChecked(bool(crit["reframe"]))
        self.follow.blockSignals(False)
        made = self._made(data)
        rows = self._rows()
        self._fill_moments(rows, made, data)
        self._update_banner(v, rows, made, data)
        self._ask_text()
        self._auto_search(v, rows, data)

    def _fill_projects(self) -> None:
        while self.projects.count():
            w = self.projects.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)                  # sinon son affichage différé en fait une fenêtre
                w.deleteLater()
        for r in self.app.projects.recent(40):
            n = self.app.db.query_one("SELECT COUNT(*) n FROM clip_candidate WHERE project_id = ? AND state != "
                                      "'rejected'", (r["id"],))["n"]
            d = self.app.projects.load_data(r["id"])
            status = ("Extrait choisi" if d.get("clip_of") else f"{n} extrait(s) proposé(s)" if n
                      else "Vidéo à analyser" if d.get("project_video") or n else "Pas encore de vidéo")
            it = ProjectItem(r["id"], r["name"], status, r["id"] == self.pid)
            it.clicked.connect(lambda pid: self.host.open_project(pid, "smart_clipping"))
            it.menu.connect(lambda pid, pos: self.host.project_menu(pid, "smart_clipping", self, pos))
            self.projects.addWidget(it)

    def _fill_moments(self, rows, made: dict[int, int], data: dict) -> None:
        for c in self.cards.values():
            c.hide()
            c.setParent(None)                      # sinon son affichage différé en fait une fenêtre
            c.deleteLater()
        self.cards = {}
        for r in rows:
            tag = ("Vidéo de ce projet" if made.get(r["id"]) == self.pid else "Vidéo créée" if r["id"] in made
                   else "")
            card = MomentCard(r, self._title_of(r, data), tag)
            card.clicked.connect(self._select)
            card.checked.connect(self._check)
            self.list_lay.insertWidget(self.list_lay.count() - 1, card)
            self.cards[r["id"]] = card
        self.left_stack.setCurrentIndex(0 if rows else 1)
        n_checked = sum(r["state"] == "accepted" for r in rows)
        todo = sum(r["state"] == "accepted" and r["id"] not in made for r in rows)
        self.count_lbl.setText(f"Extraits  {len(rows)}" if rows else "Extraits")
        self.all_box.setVisible(bool(rows))
        self.all_box.setChecked(bool(rows) and n_checked == len(rows))
        ready = made.get(data.get("clip_of")) == self.pid
        self.gen.setText(f"Créer {todo} vidéo(s)" if todo else "Coche des extraits")
        self.gen.setEnabled(bool(todo))
        self.gen.setVisible(bool(todo) or not ready)
        self.next.setVisible(ready and not todo)
        v = self.video()
        tr_ok = bool(v and self.app.transcripts.has(v["id"]))
        searching = self._searching()
        self.more_btn.setVisible(bool(rows))
        self.more_btn.setEnabled(tr_ok and not searching)
        self.ask_go.setEnabled(tr_ok and not searching)
        if self.sel not in self.cards:
            self.sel = rows[0]["id"] if rows else None
        self._select(self.sel)

    def _searching(self) -> bool:
        return bool(_active_job(self.app, "find_highlights", "project_id", self.pid)
                    or _active_job(self.app, "auto_clips", "project_id", self.pid))

    def _ask_text(self) -> None:
        cats = self._criteria()["categories"]
        v = self.video()
        tr_ok = bool(v and self.app.transcripts.has(v["id"]))
        when = ("Les extraits seront cherchés dès la fin de la transcription."
                if not tr_ok else "Lance la recherche quand tu es prêt.")
        self.ask_note.setText(when + ("" if cats else " Rien de coché : Politique."))

    def _auto_search(self, v, rows, data: dict) -> None:
        """Vidéo transcrite sans extraits ni recherche faite (importée dans un autre parcours, par exemple) :
        la recherche part d'elle-même, une fois, avec les critères choisis."""
        if not (v and self.isVisible() and not rows and self.app.transcripts.has(v["id"])):
            return
        if data.get("auto_searched") == v["id"] or self._searching():
            return
        if _last_job(self.app, "find_highlights", "project_id", self.pid):
            return
        data["auto_searched"] = v["id"]
        self.app.projects.save_data(self.pid, data)
        self._find()

    def _step_state(self, current: int) -> None:
        for i, (bar, lb) in enumerate(zip(self.step_bars, self.step_lbls)):
            st = "done" if i < current else "current" if i == current else "todo"
            bar.setProperty("state", st)
            _repolish(bar)
            lb.setProperty("role", "h2" if i == current else "muted")
            lb.setStyleSheet("font-size: 13px;" if i == current else "")
            _repolish(lb)

    def _banner(self, title: str, desc: str = "", action: str = "", fn=None, step: int = 0,
                progress: float | None = None) -> None:
        self.b_title.setText(title)
        self.b_desc.setText(desc)
        self.b_desc.setVisible(bool(desc))
        self.b_action.setText(action)
        self.b_action.setVisible(bool(action and fn))
        self._banner_fn = fn
        self.b_progress.setVisible(progress is not None)
        if progress is not None:
            self.b_progress.setValue(int(progress * 100))
        self._step_state(step)

    def _update_banner(self, v, rows, made: dict[int, int], data: dict) -> None:
        from ..transcribe import engines
        if not v:
            return self._banner("Importe une vidéo", "Un débat, un live, un podcast, un match… Glisse le fichier "
                                "ou colle un lien.", "Importer", self.browse, 0)
        aid = v["id"]
        if not self.app.transcripts.has(aid):
            for kind, key, val in (("auto_clips", "project_id", self.pid), ("transcribe", "asset_id", aid)):
                j = _active_job(self.app, kind, key, val)
                if j:
                    return self._banner("Transcription en cours…", j["message"] or "Pendant ce temps, choisis tes "
                                        "critères : les extraits seront cherchés ensuite.", step=1,
                                        progress=j["progress"])
            if not v.get("audio_tracks"):
                return self._banner("Cette vidéo n'a pas de son", "Pas de transcription possible : découpe tes "
                                    "extraits à la main dans Montage libre.", "Montage libre",
                                    lambda: self.host.goto_tab.emit("free_edit"), 1)
            last = _last_job(self.app, "transcribe", "asset_id", aid)
            if last and last["status"] == "succeeded" and last["result"].get("segments") == 0:
                return self._banner("Aucune parole exploitable", "Aucun texte n'a été inventé. Tu peux découper "
                                    "tes extraits à la main dans Montage libre.", "Montage libre",
                                    lambda: self.host.goto_tab.emit("free_edit"), 1)
            if not (engines.available() and engines.installed_models(self.app.paths.models)):
                return self._banner("Installe un modèle de transcription", "Il sert à repérer ce qui est dit. "
                                    "Une seule fois, puis tout se fait sur ce PC.", "Ouvrir les réglages",
                                    self.open_settings.emit, 1)
            return self._banner("Transcris la vidéo", "Puis les extraits seront proposés.", "Transcrire",
                                self._transcribe, 1)
        j = _active_job(self.app, "find_highlights", "project_id", self.pid) or \
            _active_job(self.app, "auto_clips", "project_id", self.pid)
        if j:
            return self._banner("Recherche des extraits…", j["message"] or "", step=2, progress=j["progress"])
        if not rows:
            return self._banner("Cherche les extraits", "Choisis tes critères, puis lance la recherche. Rien "
                                "n'est inventé.", "Lancer", self._find, 2)
        checked = [r for r in rows if r["state"] == "accepted"]
        todo = [r for r in checked if r["id"] not in made]
        if todo:
            return self._banner(f"Crée {len(todo)} vidéo(s)", "Le premier extrait devient la vidéo de ce projet, "
                                "les autres un projet chacun : format vertical, sous-titres et accroche.", step=2)
        if made.get(data.get("clip_of")) == self.pid:
            others = len([c for c, p in made.items() if p != self.pid])
            return self._banner("Ta vidéo est prête", "Passe-la dans l'onglet suivant avec le bouton en bas à "
                                "droite." + (f" {others} autre(s) extrait(s) ont leur projet, à gauche."
                                             if others else ""), step=3)
        return self._banner("Coche les extraits à garder", f"{len(rows)} extrait(s) proposé(s), du plus fort "
                            "au moins fort. Clique sur un extrait pour le voir.", step=2)

    # -- extrait sélectionné ------------------------------------------------------------
    def _select(self, cid: int | None) -> None:
        self.sel = cid
        for k, c in self.cards.items():
            c.set_selected(k == cid)
        r = self.app.candidates.get(cid) if cid else None
        self.edit_box.setVisible(r is not None)
        if r is None:
            self.player.set_timeline(Timeline(), {})
            self.frame_note.setText("")
            return
        v = self.host.assets.get(r["source_asset_id"]) or {}
        dur = float(v.get("duration_s") or 1e6)
        self.in_s.setRange(0, dur)
        self.out_s.setRange(0, dur)
        self.in_s.setValue(r["start_s"])
        self.out_s.setValue(r["end_s"])
        self.fin_lbl.setText(f"Fin · {r['end_s'] - r['start_s']:.0f} s")
        self._preview(r)

    def _preview_sel(self) -> None:
        r = self.app.candidates.get(self.sel) if self.sel else None
        if r is not None:
            self._preview(r)

    def _reframe_keys(self, r) -> list | None:
        f = (self._data().get("clip_reframe") or {}).get(str(r["id"])) or {}
        if f.get("in") == r["start_s"] and f.get("out") == r["end_s"]:
            return f.get("keys")
        return None

    def _clip_tl(self, r) -> Timeline:
        """La vidéo d'un extrait : passage choisi, format du projet, sous-titres, accroche, cadre."""
        aid = r["source_asset_id"]
        crit = self._criteria()
        tl = clip_timeline(self.host.tl, aid, r["start_s"], r["end_s"], self.host.tl.format.aspect,
                           BatchOptions(subtitles=True, hook_title=True, reframe=bool(crit["reframe"])),
                           self.app.transcripts.get(aid), r["hook"] or "", self.app.montage.text_style_for(self.pid))
        keys = self._reframe_keys(r) if tl.video.auto_reframe else None
        if keys:
            tl.segments[0].reframe = [list(k) for k in keys]
        return tl

    def _toggle_follow(self, on: bool) -> None:
        self._save_criteria({"reframe": bool(on)})
        r = self.app.candidates.get(self.sel) if self.sel else None
        if r is not None:
            self._preview(r)
            self._sync_project(r)

    def _preview(self, r) -> None:
        aid = r["source_asset_id"]
        tl = self._clip_tl(r)
        a = self.host.assets.get(aid) or {}
        note = ""
        if tl.video.auto_reframe and (a.get("width") or 0) > (a.get("height") or 0):
            if tl.segments[0].reframe:
                note = "Le cadre suit la personne qui parle."
            elif _active_job(self.app, "reframe", "candidate_id", r["id"]):
                note = "Analyse des visages pour suivre la personne qui parle…"
            else:
                self.app.jobs.submit("reframe", f"Cadrage · {self._title_of(r, self._data())}",
                                     {"asset_id": aid, "src_in": r["start_s"], "src_out": r["end_s"],
                                      "project_id": self.pid, "candidate_id": r["id"]})
                note = "Analyse des visages pour suivre la personne qui parle…"
        elif tl.video.auto_reframe:
            note = "Vidéo déjà au bon format : rien à recadrer."
        self.frame_note.setText(note)
        self.player.set_timeline(tl, self.host.assets)

    def _check(self, cid: int, on: bool) -> None:
        self.app.candidates.set_state(cid, "accepted" if on else "proposed")
        self.schedule_refresh()

    def _check_all(self, on: bool) -> None:
        for r in self._rows():
            self.app.candidates.set_state(r["id"], "accepted" if on else "proposed")
        self.schedule_refresh()

    def _edit_times(self, start: float | None = None, end: float | None = None) -> None:
        r = self.app.candidates.get(self.sel) if self.sel else None
        if r is None:
            return
        a, b = r["start_s"] if start is None else start, r["end_s"] if end is None else end
        try:
            self.app.candidates.set_times(r["id"], a, b, json.loads(r["context_flags"] or "[]"))
        except ValueError as exc:
            QMessageBox.warning(self, "Extrait", str(exc))
            self.in_s.setValue(r["start_s"])
            self.out_s.setValue(r["end_s"])
            return
        self.fin_lbl.setText(f"Fin · {b - a:.0f} s")
        r = self.app.candidates.get(r["id"])
        self._preview(r)
        self._sync_project(r)
        self.player.seek(0 if end is None else max(0.0, (b - a) - 2.0))   # on voit l'endroit réglé
        self.schedule_refresh()

    def _here(self, is_start: bool) -> None:
        """Début / fin à l'instant affiché dans l'aperçu."""
        r = self.app.candidates.get(self.sel) if self.sel else None
        if r is None:
            return
        t = r["start_s"] + self.player.t
        if is_start:
            self._edit_times(start=t)
        else:
            self._edit_times(end=t)

    def _sync_project(self, r) -> None:
        """Extrait devenu la vidéo de ce projet : la vidéo suit ses réglages (début, fin, accroche, cadre)."""
        if r is not None and self._data().get("clip_of") == r["id"]:
            self.host.replace_timeline(self._clip_tl(r))

    def _transcribe(self) -> None:
        v = self.video()
        if v:
            self.app.jobs.submit("auto_clips", f"Extraits automatiques · {v['file_name']}",
                                 {"asset_id": v["id"], "project_id": self.pid})
            self.schedule_refresh()

    def _find(self) -> None:
        v = self.video()
        if not v or not self.app.transcripts.has(v["id"]) or self._searching():
            return
        llm = self.app.settings.get("llm", None) or {}
        eng = ({"kind": llm["kind"], "model": llm.get("model", ""), "base_url": llm.get("base_url", "")}
               if llm.get("kind") in ("ollama", "openai") and llm.get("model") else {"kind": "local"})
        req = criteria_request(self._criteria(), float(v["duration_s"] or 0))
        self.app.jobs.submit("find_highlights", f"Extraits · {v['file_name']}",
                             {"asset_id": v["id"], "project_id": self.pid, "request": req.__dict__, "engine": eng})
        self.schedule_refresh()

    def _create(self) -> None:
        """Chaque extrait coché devient une vidéo : le premier celle de ce projet (si elle n'est pas encore
        choisie), les autres un nouveau projet chacun, avec le même format et les mêmes réglages."""
        data = self._data()
        made = self._made(data)
        todo = [r for r in self._rows() if r["state"] == "accepted" and r["id"] not in made]
        if not todo:
            return
        self.host.flush()
        p = self.app.projects.get(self.pid)
        crit = self._criteria()
        here = data.get("clip_of") is None
        for k, r in enumerate(todo):
            tl = self._clip_tl(r)
            if k == 0 and here:
                self.host.replace_timeline(tl)
                data = self._data()
                data["clip_of"] = r["id"]
                made[r["id"]] = self.pid
                data["clip_projects"] = {str(c): v for c, v in made.items()}
                self.app.projects.save_data(self.pid, data)
                continue
            npid = self.app.projects.quick_create(self.app.concepts, "smart_clipping", self._title_of(r)[:60],
                                                  tl.format.aspect, concept_id=p["concept_id"])
            cid = self.app.candidates.replace_proposals(npid, r["source_asset_id"], [Candidate(
                start=r["start_s"], end=r["end_s"], score=r["score"] or 0, category=r["category"] or "",
                subject=r["subject"] or "", rationale=r["rationale"] or "", hook=r["hook"] or "")], Request())[0]
            self.app.candidates.set_state(cid, "accepted")
            nd = self.app.projects.load_data(npid)
            nd.update({"timeline": tl.to_dict(), "clip_of": cid, "clip_projects": {str(cid): npid},
                       "project_video": r["source_asset_id"], "auto_searched": r["source_asset_id"],
                       "clip_criteria": crit})
            keys = self._reframe_keys(r)
            if keys:
                nd["clip_reframe"] = {str(cid): {"in": r["start_s"], "out": r["end_s"], "keys": keys}}
            self.app.projects.save_data(npid, nd)
            made[r["id"]] = npid
        data = self._data()
        data["clip_projects"] = {str(c): v for c, v in made.items()}
        self.app.projects.save_data(self.pid, data)
        self.refresh()

    def _rename(self) -> None:
        p = self.app.projects.get(self.pid) if self.pid else None
        if not p:
            return
        name, ok = QInputDialog.getText(self, "Renommer", "Nom du projet :", text=p["name"])
        if ok and name.strip():
            self.app.projects.rename(self.pid, name.strip())
            self.schedule_refresh()

    def on_job(self, job) -> None:
        pid = job.params.get("project_id")
        if job.kind == "reframe" and job.params.get("candidate_id") and pid and self.app.projects.get(pid):
            if job.status == "succeeded":            # cadre calculé : gardé pour l'aperçu et l'export
                data = self.app.projects.load_data(pid)
                data.setdefault("clip_reframe", {})[str(job.params["candidate_id"])] = {
                    "in": job.params["src_in"], "out": job.params["src_out"], "keys": (job.result or {})["keys"]}
                self.app.projects.save_data(pid, data)
            if pid == self.pid and job.params["candidate_id"] == self.sel:
                if job.status == "failed":
                    self.frame_note.setText("Suivi des visages indisponible : " +
                                            ((job.error or "").strip().splitlines() or [""])[-1][:160])
                elif job.status == "succeeded":
                    self._preview_sel()
                    self._sync_project(self.app.candidates.get(self.sel))
            return
        if not self.pid or not self.isVisible():
            return
        if job.kind in ("import_file", "import_url", "transcribe", "auto_clips", "find_highlights"):
            if job.status in ("pending", "running"):
                v = self.video()
                if self.b_progress.isVisible() and v:      # progression seule, sans tout reconstruire
                    self.b_progress.setValue(int((job.progress or 0) * 100))
                    if job.message:
                        self.b_desc.setText(job.message)
                        self.b_desc.show()
                    return
            self.schedule_refresh()


# -- Traduction -------------------------------------------------------------------------------------------
LANGS = [("en", "Anglais"), ("de", "Allemand"), ("es", "Espagnol"), ("pt", "Portugais (BR)"), ("it", "Italien"),
         ("ja", "Japonais"), ("ru", "Russe"), ("ko", "Coréen"), ("ar", "Arabe"), ("tr", "Turc"), ("pl", "Polonais"),
         ("nl", "Néerlandais"), ("id", "Indonésien"), ("hi", "Hindi"), ("fr", "Français"), ("zh", "Chinois")]


class VoiceDialog(QDialog):
    """Choix de la voix du doublage (ElevenLabs ou Piper), ou aucune voix (sous-titres seulement)."""

    def __init__(self, app: AppContext, current: dict | None, parent=None):
        super().__init__(parent)
        from ..ai import tts
        from ..services import secrets
        self.app, self.choice = app, current
        self.setWindowTitle("Voix du doublage")
        self.resize(460, 480)
        lay = QVBoxLayout(self)
        self.engine = QComboBox()
        has_key = bool(secrets.get_secret("elevenlabs_api_key"))
        self.engine.addItem("ElevenLabs" + ("" if has_key else " (clé à saisir : icône réglages)"), "elevenlabs")
        llm = app.settings.get("llm", None) or {}
        if llm.get("kind") == "openai" and secrets.get_secret("llm_api_key"):
            self.engine.addItem("OpenAI (même clé que le modèle de langage)", "openai")
        try:
            tts.PiperTTS(app.paths.models)
            self.engine.addItem("Piper (hors ligne, gratuit : une voix par langue)", "piper")
        except tts.TTSError:
            pass
        if current and current.get("engine"):
            self.engine.setCurrentIndex(max(0, self.engine.findData(current["engine"])))
        self.engine.currentIndexChanged.connect(self._load)
        lay.addLayout(_row(label("Moteur", "muted"), self.engine, "stretch"))
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _it: self.accept())
        lay.addWidget(self.list, 1)
        self.status = label("", "faint", wrap=True, hint=False)
        lay.addWidget(self.status)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Choisir")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        QTimer.singleShot(0, self._load)

    def _load(self) -> None:
        from ..ai import tts
        from ..services import secrets
        self.list.clear()
        none = QListWidgetItem("Aucune voix : garder la voix d'origine, sous-titres traduits")
        none.setData(Qt.UserRole, None)
        self.list.addItem(none)
        self.list.setCurrentItem(none)
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            voices = tts.make_engine(self.engine.currentData(), secrets.get_secret, self.app.paths.models,
                                     self.app.settings.get("llm", None) or {}).voices()
        except tts.TTSError as exc:
            self.status.setText(str(exc))
            return
        finally:
            QGuiApplication.restoreOverrideCursor()
        from ..ai.translate import LANG_NAMES
        for v in voices:
            lang = f" · {LANG_NAMES.get(v.language, v.language)}" if v.language else ""
            it = QListWidgetItem(v.name + lang + (f" · {v.category}" if v.category else ""))
            it.setData(Qt.UserRole, {"engine": self.engine.currentData(), "id": v.id, "name": v.name})
            self.list.addItem(it)
            if self.choice and self.choice.get("id") == v.id:
                self.list.setCurrentItem(it)
        note = {"elevenlabs": "Le texte traduit est envoyé à ElevenLabs et consomme tes crédits ; la voix parle "
                              "toutes les langues.",
                "openai": "Le texte traduit est envoyé à OpenAI (payant à l'usage) ; la voix parle toutes les "
                          "langues.",
                "piper": "Hors ligne : pour chaque langue, une voix de cette langue est prise (à télécharger dans "
                         "Réglages si elle manque)."}.get(self.engine.currentData(), "")
        self.status.setText(f"{len(voices)} voix disponibles. {note}")

    def accept(self) -> None:
        it = self.list.currentItem()
        self.choice = it.data(Qt.UserRole) if it else None
        super().accept()


class TranslationView(_View):
    """Traduction en un écran : vidéo, langues, voix, textes et style des sous-titres, puis « Générer »."""

    def __init__(self, app: AppContext, host, export_dir: Path, parent=None):
        super().__init__(app, host, parent)
        self.export_dir = export_dir
        self.job_id: int | None = None
        self._loading = False
        self._preview_timer = QTimer(self, singleShot=True, interval=250)
        self._preview_timer.timeout.connect(self._preview)
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 16, 26, 10)
        root.setSpacing(14)
        body = QHBoxLayout()
        body.setSpacing(28)
        self.left = QStackedWidget()
        dz = DropZone("Glisse ta vidéo ici\nou importe-la en haut")
        dz.dropped.connect(self.dropped)
        self.left.addWidget(dz)
        self.player = MontagePlayer()
        self.player.note.setMaximumHeight(0)
        self.left.addWidget(self.player)
        self.left.setMinimumWidth(320)
        body.addWidget(self.left, 2)

        card = QFrame()
        card.setProperty("role", "card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(24, 20, 24, 18)
        cl.setSpacing(10)
        cl.addWidget(label("1  ·  VIDÉO", "step"))
        self.video_lbl = label("", "muted", wrap=True, hint=False)
        cl.addWidget(self.video_lbl)
        cl.addSpacing(6)
        cl.addWidget(label("2  ·  LANGUES", "step"))
        grid = QGridLayout()
        grid.setSpacing(8)
        self.chips: dict[str, QPushButton] = {}
        for i, (code, nm) in enumerate(LANGS):
            b = button(nm, "chip")
            b.setCheckable(True)
            b.toggled.connect(self._changed)
            grid.addWidget(b, i // 4, i % 4)
            self.chips[code] = b
        cl.addLayout(grid)
        self.engine = QComboBox()
        self.engine.currentIndexChanged.connect(self._changed)
        cl.addLayout(_row(label("Traduction par", "faint"), self.engine, "stretch",
                          tool("settings", "Moteurs de traduction : alternatives, conseils, clés",
                               self._engines_dialog)))
        cl.addSpacing(6)
        cl.addWidget(label("3  ·  VOIX", "step"))
        self.voice: dict | None = None
        self.voice_lbl = label("")
        self.voice_lbl.setStyleSheet("font-weight: 700;")
        mic = QToolButton()
        mic.setIcon(theme.icon("mic", theme.color("muted"), 16))
        mic.setEnabled(False)
        mic.setStyleSheet("border: none; background: transparent;")
        cl.addLayout(_row(mic, self.voice_lbl, "stretch", button("Choisir…", on_click=self._pick_voice),
                          tool("settings", "Voix : alternatives, conseils, clés", self._voices_dialog), spacing=6))
        cl.addSpacing(6)
        cl.addWidget(label("4  ·  TEXTES", "step"))
        self.top_text = QLineEdit()
        self.top_text.setPlaceholderText("Texte en haut de la vidéo (facultatif, sera traduit)")
        self.top_text.textChanged.connect(self._changed)
        cl.addWidget(self.top_text)
        self.presets = QButtonGroup(self)
        self.presets.setExclusive(True)
        prow = [label("Sous-titres", "muted")]
        for key, (nm, _st) in trn.SUB_PRESETS.items():
            b = button(nm, "chip")
            b.setCheckable(True)
            b.setProperty("preset", key)
            self.presets.addButton(b)
            prow.append(b)
        self.presets.buttonToggled.connect(lambda _b, on: on and self._changed())
        cl.addLayout(_row(*prow, "stretch"))
        self.keep_music = QCheckBox("Garder la musique")
        self.keep_music.setToolTip("Avec une voix : la musique et l'ambiance d'origine sont séparées de la voix et "
                                   "gardées sous le doublage.")
        self.erase = QCheckBox("Effacer les textes d'origine")
        self.erase.setToolTip("Les textes incrustés détectés sont masqués. Le résultat n'est jamais parfait : "
                              "vérifie la vidéo.")
        for cb in (self.keep_music, self.erase):
            cb.toggled.connect(self._changed)
        cl.addLayout(_row(self.keep_music, self.erase, "stretch", spacing=22))
        cl.addSpacing(4)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        cl.addWidget(self.progress)
        self.go = button("Préparer", "big", on_click=self._go)
        cl.addWidget(self.go)
        self.status = label("", "muted", wrap=True, hint=False)
        cl.addWidget(self.status)
        self.next = NextTab(host, "translation")          # langues prêtes : vers l'onglet suivant
        self.next.hide()
        cl.addWidget(self.next)
        holder = QWidget()                                # la carte garde sa hauteur, sans vide sous le bouton
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(card)
        hl.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setWidget(holder)
        body.addWidget(sc, 3)
        root.addLayout(body, 1)

    # -- état enregistré dans le projet ---------------------------------------------------
    def _state(self) -> dict:
        st = {"langs": ["en"], "voice": None, "top_text": "", "preset": "white", "keep_music": True, "erase": False}
        st.update(self.app.settings.get("translate_defaults", None) or {})
        if self.pid:
            st.update(self.app.projects.load_data(self.pid).get("translate_ui") or {})
        return st

    def _changed(self, *_a) -> None:
        if self._loading or not self.pid:
            return
        st = {"langs": [c for c, b in self.chips.items() if b.isChecked()], "voice": self.voice,
              "top_text": self.top_text.text(),
              "preset": (self.presets.checkedButton().property("preset") if self.presets.checkedButton()
                         else "white"),
              "keep_music": self.keep_music.isChecked(), "erase": self.erase.isChecked()}
        data = self.app.projects.load_data(self.pid)
        data["translate_ui"] = st
        self.app.projects.save_data(self.pid, data)
        self.app.settings.set("translate_defaults", {k: st[k] for k in ("langs", "voice", "preset", "keep_music",
                                                                         "erase")})
        if self.engine.currentData():
            self.app.settings.set("translate_engine", self.engine.currentData())
        self._update_go()
        self._preview_timer.start()

    def _engines(self) -> list[tuple[str, str]]:
        from .engines import translation_choices
        return translation_choices(self.app)

    def _engines_dialog(self) -> None:
        from .engines import TranslationEnginesDialog
        dlg = TranslationEnginesDialog(self.app, self)
        dlg.changed.connect(self.schedule_refresh)
        if dlg.exec() == 2:
            self.open_settings.emit()
        self.refresh()

    def _voices_dialog(self) -> None:
        from .engines import VoiceEnginesDialog
        dlg = VoiceEnginesDialog(self.app, self)
        if dlg.exec() == 3:
            self.open_settings.emit()
        self.refresh()

    def refresh(self) -> None:
        if not self.pid:
            return
        self._loading = True
        try:
            st = self._state()
            for c, b in self.chips.items():
                b.setChecked(c in st["langs"])
            self.voice = st["voice"]
            self.voice_lbl.setText(self.voice["name"] if self.voice else "Aucune voix (sous-titres seulement)")
            if self.top_text.text() != st["top_text"]:
                self.top_text.setText(st["top_text"])
            for b in self.presets.buttons():
                b.setChecked(b.property("preset") == st["preset"])
            self.keep_music.setChecked(bool(st["keep_music"]))
            self.erase.setChecked(bool(st["erase"]))
            self.engine.clear()
            for k, nm in self._engines():
                self.engine.addItem(nm, k)
            self.engine.setCurrentIndex(max(0, self.engine.findData(self.app.settings.get("translate_engine", None)
                                                                    or "auto")))
            v = self.video()
            self.left.setCurrentIndex(1 if v else 0)
            src = self.app.transcripts.get(v["id"]) if v and self.app.transcripts.has(v["id"]) else None
            for c, b in self.chips.items():
                same = bool(src and src.language == c)
                b.setEnabled(not same)
                b.setToolTip("Langue d'origine de la vidéo" if same else "")
            self._update_video_line(v, src)
            last = _last_job(self.app, "translate_video", "project_id", self.pid)
            running = _active_job(self.app, "translate_video", "project_id", self.pid)
            self.job_id = running["id"] if running else None
            self.progress.setVisible(running is not None)
            ready = (self.app.projects.load_data(self.pid).get("translations") or {}).get("ready") or []
            names = dict(LANGS)
            if running:
                self.progress.setValue(int((running["progress"] or 0) * 100))
                self.status.setText(running["message"] or "Préparation…")
            elif last and last["status"] == "failed":
                self.status.setText("Échec : " + ((last["error"] or "").strip().splitlines() or [""])[-1][:300])
            elif ready:
                res = (last or {}).get("result", {}) if last and last["status"] == "succeeded" else {}
                how = (" Traduit avec " + ", ".join(res["engines"]) + ".") if res.get("engines") else ""
                aside = "".join(f"\nÉcarté : {n}" for n in (res.get("notes") or [])[:2])
                w = res.get("warning")
                self.status.setText("✓ Prêt : " + ", ".join(names.get(x, x) for x in ready)
                                    + "." + how + " Choisis la langue dans Export." + (f"\n{w}" if w else "")
                                    + aside)
            else:
                self.status.setText("" if v else "Ajoute une vidéo pour commencer.")
            self.next.setVisible(bool(not running and ready))
        finally:
            self._loading = False
        self._update_go()
        self._preview()

    def _update_video_line(self, v, src) -> None:
        if not v:
            self.video_lbl.setText("")
            return
        names = dict(LANGS)
        if src is not None:
            lang = names.get(src.language, src.language.upper() if src.language else "?")
            info = f"transcrite ({lang} détecté, {len(src.segments)} phrases)"
        elif _active_job(self.app, "transcribe", "asset_id", v["id"]):
            info = "transcription en cours…"
        elif not v.get("audio_tracks"):
            info = "pas de son : rien à traduire"
        else:
            last = _last_job(self.app, "transcribe", "asset_id", v["id"])
            info = ("aucune parole exploitable détectée" if last and last["status"] == "succeeded"
                    and last["result"].get("segments") == 0 else "transcription automatique au lancement")
        self.video_lbl.setText(f"{v['file_name']} · {fmt_timecode(v['duration_s'] or 0)} · {info}")

    def _langs(self) -> list[str]:
        return [c for c, b in self.chips.items() if b.isChecked() and b.isEnabled()]

    def _update_go(self) -> None:
        if self.job_id is not None:
            self.go.setText("Annuler")
            self.go.setEnabled(True)
            return
        n = len(self._langs())
        self.go.setText(f"Préparer  ·  {n} langue{'s' if n > 1 else ''}" if n else "Préparer")
        self.go.setEnabled(bool(self.video() and n and self.engine.currentData()))

    def _pick_voice(self) -> None:
        dlg = VoiceDialog(self.app, self.voice, self)
        if dlg.exec():
            self.voice = dlg.choice
            self.voice_lbl.setText(self.voice["name"] if self.voice else "Aucune voix (sous-titres seulement)")
            self._changed()

    def _preview(self) -> None:
        v = self.video()
        if not v:
            return
        aid = v["id"]
        langs = self._langs()
        tr = self.app.transcripts.translation(aid, langs[0]) if langs else None
        lang = langs[0] if tr is not None else ""
        tr = tr or self.app.transcripts.get(aid)
        preset = self.presets.checkedButton().property("preset") if self.presets.checkedButton() else "white"
        tl = trn.translated_timeline(self.host.tl, aid, float(v["duration_s"] or 0), lang, tr, preset,
                                     top_text=self.top_text.text(), text_style=self.app.montage.text_style_for(self.pid))
        self.player.set_timeline(tl, self.host.assets)

    def _go(self) -> None:
        if self.job_id is not None:
            self.app.jobs.cancel(self.job_id)
            return
        v = self.video()
        langs = self._langs()
        if not v or not langs:
            return
        if self.voice and not self.app.settings.get("dub_consent", False):
            from .dub_dialog import CONSENT
            if QMessageBox.question(self, "Voix", CONSENT + " ?") != QMessageBox.Yes:
                return
            self.app.settings.set("dub_consent", True)
        self.host.flush()
        p = self.app.projects.get(self.pid)
        st = self._state()
        top = st["top_text"].strip() or next((t.text for t in self.host.tl.texts if t.position == "top"), "")
        self.job_id = self.app.jobs.submit(
            "translate_video", f"Traduction · {v['file_name']} · {', '.join(c.upper() for c in langs)}",
            {"asset_id": v["id"], "project_id": self.pid, "languages": langs, "engine": self.engine.currentData(),
             "voice": self.voice, "top_text": top, "preset": st["preset"], "keep_music": st["keep_music"],
             "erase_texts": st["erase"], "timeline": self.host.tl.to_dict(), "folder": str(self.export_dir),
             "export": False, "glossary": [p["concept_name"]] if p["concept_name"] else []})
        self.status.setText("Préparation…")
        self.progress.setValue(0)
        self.progress.show()
        self.next.hide()
        self._update_go()

    def note(self, text: str) -> None:
        self.status.setText(text)

    def on_job(self, job) -> None:
        pid = job.params.get("project_id")
        if job.kind == "translate_video" and job.status == "succeeded" and pid and self.app.projects.get(pid) \
                and not job.params.get("export", True):
            # langues prêtes : gardées dans le projet, l'onglet Export en fait la vidéo
            r = job.result or {}
            data = self.app.projects.load_data(pid)
            old = data.get("translations") or {}
            data["translations"] = {
                "ready": sorted(set(old.get("ready") or []) | set(r.get("ready") or [])),
                "top_texts": {**(old.get("top_texts") or {}), **(r.get("top_texts") or {})},
                "masks": r.get("masks") or [], "dubbed": bool(r.get("dubbed")),
                "keep_music": bool(r.get("keep_music", True)), "preset": job.params.get("preset") or "white"}
            self.app.projects.save_data(pid, data)
        if not self.pid:
            return
        if job.kind == "translate_video" and job.status in ("pending", "running") and job.id == self.job_id:
            self.progress.setValue(int((job.progress or 0) * 100))
            self.status.setText(job.message or "Préparation…")
            return
        if job.kind in ("translate_video", "import_file", "import_url", "transcribe") and \
                job.status not in ("pending", "running"):
            self.schedule_refresh()
        elif job.kind == "transcribe" and job.status == "running" and not self.video_lbl.text().endswith("…"):
            self.schedule_refresh()
