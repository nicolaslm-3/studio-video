"""Inspecteur du montage : extrait, cadrage, image, textes, audio, export."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QLabel, QCheckBox, QComboBox, QFileDialog, QGridLayout,
                               QMessageBox, QFormLayout, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QPushButton, QScrollArea, QSlider, QTabWidget, QToolButton,
                               QVBoxLayout, QWidget)

from .scrub import double_spin, int_spin
from ..core import AppContext
from ..edit import compose
from ..edit.model import ASPECTS, TextItem, Timeline
from ..media.ffmpeg import fmt_timecode
from .color_picker import ColorButton
from .widgets import tool, button, label

FIT_MODES = [("fill", "Remplir (recadré)"), ("blur", "Image entière + fond flou"), ("fit", "Image entière + bandes noires")]
POSITIONS = [("top", "En haut"), ("center", "Au centre"), ("bottom", "En bas"), ("custom", "Libre (X / Y)")]
TEXT_KINDS = (("title", "Titre"), ("hook", "Accroche"), ("cta", "Call to action"), ("lower", "Nom"))
LOUDNESS = [(-14.0, "Réseaux sociaux (-14 LUFS)"), (-16.0, "Podcast / voix (-16 LUFS)"),
            (-23.0, "Diffusion TV (-23 LUFS)"), (-11.0, "Très fort (-11 LUFS)")]
QUALITY = [(18, "Très haute (fichier lourd)"), (20, "Haute (recommandé)"), (23, "Standard"), (27, "Légère")]
SPEED = [("veryfast", "Rapide"), ("medium", "Équilibré"), ("slow", "Lent (meilleure compression)")]
SLIDER_W = 96                          # curseurs (largeur max, X, Y, opacité…) : une longueur fixe, courte
SCRUB_W = 78                           # valeurs réglables à la souris de l'onglet Textes (début, fin, taille)


def _get(obj, path: str):
    for p in path.split("."):
        obj = getattr(obj, p)
    return obj


def _set(obj, path: str, value) -> None:
    *head, last = path.split(".")
    for p in head:
        obj = getattr(obj, p)
    setattr(obj, last, value)


POPULAR_FONTS = ["Segoe UI", "Segoe UI Black", "Arial", "Arial Black", "Impact", "Bahnschrift", "Montserrat",
                 "Poppins", "Inter", "Roboto", "Bebas Neue", "Anton", "Oswald", "Calibri", "Verdana", "Trebuchet MS",
                 "Tahoma", "Georgia", "Times New Roman", "Cambria", "Comic Sans MS", "Segoe Print", "Segoe Script",
                 "Gabriola", "Consolas", "Courier New", "DejaVu Sans", "Noto Sans", "Liberation Sans"]


class FontPicker(QComboBox):
    """Menu déroulant de polices : les plus courantes d'abord (chacune affichée dans son style), puis toutes."""
    fontChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtGui import QFontDatabase
        self.setMaxVisibleItems(16)
        fams = sorted(set(QFontDatabase.families()), key=str.lower) or ["Segoe UI", "Arial", "Impact"]
        # (environnement sans polices installées : liste minimale plutôt qu'un menu vide)
        have = {f.lower(): f for f in fams}
        top = [have[f.lower()] for f in POPULAR_FONTS if f.lower() in have]
        for f in top:
            self._add(f)
        if top and len(top) < len(fams):            # séparateur seulement s'il y a d'autres polices après
            self.insertSeparator(self.count())
        for f in fams:
            if f not in top and not f.startswith("@"):
                self._add(f)
        self.currentIndexChanged.connect(lambda _i: self.currentData() and self.fontChanged.emit(self.currentData()))

    def _add(self, family: str) -> None:
        self.addItem(family, family)
        f = QFont(family)
        f.setPointSizeF(11)
        self.setItemData(self.count() - 1, f, Qt.FontRole)
        self.setItemData(self.count() - 1, family, Qt.ToolTipRole)      # nom entier au survol

    def set_family(self, family: str) -> None:
        i = self.findData(family)
        if i < 0 and family:
            self._add(family)
            i = self.count() - 1
        self.blockSignals(True)
        self.setCurrentIndex(max(0, i))
        self.blockSignals(False)
        self.setFont(QFont(family or self.font().family()))
        self.setToolTip(family)


class Binder:
    """Relie des contrôles à des attributs de la timeline (ou du texte sélectionné)."""

    def __init__(self, panel: "MontagePanel", target: str = "tl"):
        self.panel, self.target = panel, target
        self.items: list[tuple] = []

    def _obj(self):
        return self.panel.tl if self.target == "tl" else self.panel.current_text()

    def _commit(self, path, value):
        obj = self._obj()
        if obj is None or self.panel._loading:
            return
        _set(obj, path, value)
        self.panel.changed.emit(path)

    def spin(self, path, lo, hi, step=0.1, decimals=1, suffix=""):
        """Nombre décimal réglable à la souris (glisser, molette, + / −)."""
        from .scrub import ScrubNumber
        w = ScrubNumber(decimals=decimals, step=step, suffix=suffix)
        w.setRange(lo, hi)
        w.valueChanged.connect(lambda v: self._commit(path, round(v, decimals)))
        self.items.append(("spin", path, w))
        return w

    def ispin(self, path, lo, hi, suffix=""):
        """Nombre entier réglable à la souris."""
        from .scrub import ScrubNumber
        w = ScrubNumber(decimals=0, step=1, suffix=suffix)
        w.setRange(lo, hi)
        w.valueChanged.connect(lambda v: self._commit(path, int(v)))
        self.items.append(("spin", path, w))
        return w

    def slider(self, path, lo, hi, scale=100.0) -> QSlider:
        w = QSlider(Qt.Horizontal)
        w.setFixedWidth(SLIDER_W)
        w.setRange(int(lo * scale), int(hi * scale))
        w.valueChanged.connect(lambda v: self._commit(path, round(v / scale, 3)))
        w._scale = scale  # type: ignore[attr-defined]
        self.items.append(("slider", path, w))
        return w

    def check(self, path, text) -> QCheckBox:
        w = QCheckBox(text)
        w.toggled.connect(lambda v: self._commit(path, bool(v)))
        self.items.append(("check", path, w))
        return w

    def combo(self, path, options) -> QComboBox:
        w = QComboBox()
        for val, name in options:
            w.addItem(name, val)
        w.currentIndexChanged.connect(lambda _i: self._commit(path, w.currentData()))
        self.items.append(("combo", path, w))
        return w

    def color(self, path) -> ColorButton:
        w = ColorButton()
        w.picked.connect(lambda v: self._commit(path, v))
        self.items.append(("color", path, w))
        return w

    def font(self, path) -> "FontPicker":
        w = FontPicker()
        w.fontChanged.connect(lambda fam: (w.setToolTip(fam), self._commit(path, fam)))
        self.items.append(("font", path, w))
        return w

    def scrub(self, path, lo, hi):
        """Nombre réglable à la souris (glisser, molette), comme les temps de Clipping."""
        from .scrub import ScrubNumber
        w = ScrubNumber()
        w.setRange(lo, hi)
        w.edited.connect(lambda v: self._commit(path, int(v)))
        self.items.append(("scrub", path, w))
        return w

    def tbtn(self, path, text, tooltip, bold=False, italic=False) -> QToolButton:
        """Bouton à enfoncer (gras, italique…)."""
        w = QToolButton()
        w.setText(text)
        w.setCheckable(True)
        w.setToolTip(tooltip)
        w.setFixedSize(34, 34)
        f = QFont(w.font())
        f.setPointSizeF(13)
        f.setBold(bold)
        f.setItalic(italic)
        w.setFont(f)
        w.toggled.connect(lambda v: self._commit(path, bool(v)))
        self.items.append(("check", path, w))
        return w

    def choice(self, path, options) -> "ChoiceButtons":
        """Boutons-icônes exclusifs (alignement…)."""
        w = ChoiceButtons(options)
        w.picked.connect(lambda v: self._commit(path, v))
        self.items.append(("choice", path, w))
        return w

    def load(self) -> None:
        obj = self._obj()
        for kind, path, w in self.items:
            w.setEnabled(obj is not None)
            if obj is None:
                continue
            v = _get(obj, path)
            w.blockSignals(True)
            if kind == "spin":
                w.setValue(v)
            elif kind == "slider":
                w.setValue(int(round(v * w._scale)))
            elif kind == "check":
                w.setChecked(bool(v))
            elif kind == "combo":
                i = w.findData(v)
                if i < 0 and isinstance(v, float):
                    i = next((k for k in range(w.count()) if w.itemData(k) == v), -1)
                w.setCurrentIndex(max(0, i))
            elif kind == "color":
                w.set(v)
            elif kind == "font":
                w.set_family(v)
            elif kind in ("scrub", "choice"):
                w.setValue(v)
            w.blockSignals(False)


def _hint_row(icon: str, tip: str) -> QHBoxLayout:
    """Ligne de réglages commençant par une petite icône grise (le nom du réglage au survol, sans libellé)."""
    from . import theme
    h = QHBoxLayout()
    h.setSpacing(6)
    ic = QLabel()
    ic.setPixmap(theme.icon(icon, theme.color("faint"), 16).pixmap(16, 16))
    ic.setToolTip(tip)
    ic.setFixedWidth(18)
    h.addWidget(ic)
    return h


class ChoiceButtons(QWidget):
    """Petits boutons-icônes dont un seul est enfoncé (gauche, centre, droite…)."""
    picked = Signal(str)

    def __init__(self, options, parent=None):
        super().__init__(parent)
        from . import theme
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QToolButton] = {}
        for val, ic, tip in options:
            b = tool(ic, tip, checkable=True)
            b.setIcon(theme.icon(ic, theme.color("muted"), 18))
            self.group.addButton(b)
            self.buttons[val] = b
            b.clicked.connect(lambda _=False, v=val: self.picked.emit(v))
            h.addWidget(b)

    def setValue(self, v) -> None:                   # noqa: N802
        for val, b in self.buttons.items():
            b.setChecked(val == v)


def preset_pixmap(params: dict, text: str, px: int = 20) -> QPixmap:
    """Petit aperçu d'un texte dans son style (police, couleur, contour, fond) : l'image d'un bouton d'ajout,
    dessinée par le rendu même des textes de la vidéo (`edit/textrender.py`), puis recadrée."""
    from ..edit.textrender import REFERENCE_HEIGHT, render_text_image
    dpr = 2
    item = TextItem(text, **{k: v for k, v in params.items() if k in TextItem.__dataclass_fields__
                              and k not in ("text", "id", "start", "end")})
    item.position, item.max_width, item.fade = "center", 1.0, 0.0
    height = max(60, int(REFERENCE_HEIGHT * px * dpr / max(8, item.size)))     # police de px pixels (x2)
    width = int(px * dpr * (len(text) + 4))
    img = render_text_image(item, width, height)
    font = QFont(item.font)
    font.setPixelSize(px * dpr)
    font.setBold(item.bold)
    font.setItalic(item.italic)
    fm = QFontMetricsF(font)
    pad = px * dpr * (0.35 if item.box else 0.0) + item.outline * px * dpr / 72 * 2 + 2
    bw, bh = fm.horizontalAdvance(text) + 2 * pad, fm.height() * 1.05 + 2 * pad
    rect = QRectF((width - bw) / 2, (height - bh) / 2, bw, bh).toAlignedRect().intersected(img.rect())
    pm = QPixmap.fromImage(img.copy(rect))
    pm.setDevicePixelRatio(dpr)
    return pm


def _shrinkable(root: QWidget) -> None:
    """Aucun champ ne doit imposer sa largeur (listes de polices, libellés longs) : pas de débordement."""
    for cb in root.findChildren(QComboBox):
        from PySide6.QtWidgets import QSizePolicy
        cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        cb.setMinimumContentsLength(8)
        cb.setSizePolicy(QSizePolicy.Expanding, cb.sizePolicy().verticalPolicy())
    for b in root.findChildren(QPushButton):
        if isinstance(b, ColorButton):
            continue                                                 # pastille de couleur : carrée
        b.setMinimumWidth(min(b.minimumSizeHint().width(), 56))     # peut rétrécir au lieu de déborder
        if not b.toolTip() and b.text():
            b.setToolTip(b.text())
    from PySide6.QtWidgets import QAbstractSpinBox, QPlainTextEdit
    for sp in root.findChildren(QAbstractSpinBox):
        sp.setMinimumWidth(min(sp.minimumSizeHint().width(), 64))
    for te in root.findChildren(QPlainTextEdit):
        te.setMinimumWidth(80)
    for cb in root.findChildren(QCheckBox):
        cb.setMinimumWidth(min(cb.minimumSizeHint().width(), 60))
        if not cb.toolTip():
            cb.setToolTip(cb.text())
    for lb in root.findChildren(QLabel):
        if not lb.wordWrap() and len(lb.text()) > 22 and lb.textFormat() != Qt.RichText:
            lb.setWordWrap(True)


def _scroll(inner: QWidget) -> QScrollArea:
    _shrinkable(inner)
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    sc.setFrameShape(QScrollArea.NoFrame)
    sc.setWidget(inner)
    return sc


def _form() -> tuple[QWidget, QFormLayout]:
    w = QWidget()
    f = QFormLayout(w)
    f.setContentsMargins(4, 10, 8, 10)
    f.setLabelAlignment(Qt.AlignLeft)
    f.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
    f.setRowWrapPolicy(QFormLayout.WrapLongRows)         # panneau étroit : le champ passe sous son libellé
    return w, f


class MontagePanel(QTabWidget):
    changed = Signal(str)                 # chemin modifié (ou "structure")
    split_requested = Signal()
    analyze_requested = Signal()
    export_requested = Signal()
    save_concept_requested = Signal()
    apply_concept_requested = Signal()
    text_added = Signal(str)
    generate_subs_requested = Signal()
    optimize_requested = Signal()
    separate_requested = Signal()
    detect_text_requested = Signal()
    reframe_requested = Signal(bool)          # True : suivre les visages ; False : retirer
    cue_selected = Signal(float)

    def __init__(self, app: AppContext, parent=None):
        super().__init__(parent)
        self.app = app
        self.tl = Timeline()
        self.project_id: int | None = None
        self.concept_name = ""
        self.sel_segment: str | None = None
        self.sel_text: str | None = None
        self.playhead = 0.0
        self.durations: dict[int, float] = {}
        self._loading = False
        self.setMinimumWidth(330)
        self.setUsesScrollButtons(False)
        self.tabBar().setStyleSheet("QTabBar::tab { padding: 6px 10px; margin: 2px 1px 6px 0; }")
        self.setDocumentMode(True)
        self.b = Binder(self)
        self.bt = Binder(self, "text")
        from . import theme
        from PySide6.QtCore import QSize
        self.setIconSize(QSize(19, 19))
        for page, name, ic in ((self._segment_tab(), "Extrait", "film"), (self._frame_tab(), "Cadrage", "crop"),
                               (self._texts_tab(), "Textes", "type"), (self._subs_tab(), "Sous-titres", "captions"),
                               (self._audio_tab(), "Audio", "audio-waveform"),
                               (self._export_tab(), "Export", "share-2")):
            i = self.addTab(_scroll(page), theme.icon(ic, theme.color("muted"), 19), "")
            self.setTabToolTip(i, name)
            self.tabBar().setTabData(i, name)
        self.currentChanged.connect(self._tab_title)
        self._tab_title(0)

    def tab_name(self, i: int) -> str:
        return self.tabBar().tabData(i) or ""

    def _tab_title(self, i: int) -> None:
        """Onglet actif : icône dans la couleur d'accent, les autres en gris."""
        from . import theme
        for k, ic in enumerate(("film", "crop", "type", "captions", "audio-waveform", "share-2")):
            if k < self.count():
                self.setTabIcon(k, theme.icon(ic, theme.color("accent") if k == i else theme.color("muted"), 19))

    # -- chargement ---------------------------------------------------------
    def set_timeline(self, tl: Timeline, project_id: int, concept_name: str, durations: dict[int, float]) -> None:
        self.tl, self.project_id, self.concept_name, self.durations = tl, project_id, concept_name, durations
        self.refresh()

    def refresh(self) -> None:
        self._loading = True
        try:
            self.b.load()
            self.aspect.setCurrentIndex(max(0, self.aspect.findData(self.tl.format.aspect)))
            short = min(self.tl.format.width, self.tl.format.height)
            self.res.setCurrentIndex(max(0, self.res.findData(short)) if self.res.findData(short) >= 0 else 1)
            self._load_segment()
            self._load_texts()
            self._load_cues()
            self._load_music_choices()
            self._load_voice_choices()
            self._load_masks()
            self._load_layout()
            self.reload_reframe()
            self._load_history()
            self.save_concept_btn.setText(f"Mémoriser ces réglages pour « {self.concept_name} »")
            self.apply_concept_btn.setText(f"Appliquer les réglages de « {self.concept_name} »")
            d = self.tl.export.directory
            self.dir_lbl.setText(("…" + d[-38:] if len(d) > 40 else d) if d else "Vidéos\\Studio Vidéo (par défaut)")
            self.dir_lbl.setToolTip(d)
            self.out_info.setText(f"{self.tl.format.width}×{self.tl.format.height} · {self.tl.format.fps:g} i/s · "
                                  f"durée {fmt_timecode(self.tl.duration, True)}")
        finally:
            self._loading = False

    def select_segment(self, seg_id) -> None:
        self.sel_segment = seg_id
        self._loading = True
        self._load_segment()
        self._loading = False
        if seg_id:
            self.setCurrentIndex(0)

    def select_text(self, text_id) -> None:
        self.sel_text = text_id
        self._loading = True
        self._load_texts()
        self._loading = False
        if text_id:
            self.setCurrentIndex(2)

    def current_text(self):
        try:
            return self.tl.text(self.sel_text) if self.sel_text else None
        except KeyError:
            return None

    # -- Extrait ------------------------------------------------------------
    def _segment_tab(self) -> QWidget:
        w, f = _form()
        self.seg_title = label("Aucun extrait sélectionné", "h2", wrap=True)
        f.addRow(self.seg_title)
        self.seg_in = double_spin()
        self.seg_out = double_spin()
        for sp in (self.seg_in, self.seg_out):
            sp.setDecimals(2)
            sp.setRange(0, 1e6)
            sp.setSingleStep(0.1)
            sp.setSuffix(" s")
            sp.setKeyboardTracking(False)
            sp.valueChanged.connect(self._seg_trim)
        f.addRow("Début dans la source", self.seg_in)
        f.addRow("Fin dans la source", self.seg_out)
        self.seg_dur = label("", "faint")
        f.addRow("", self.seg_dur)
        self.seg_vol = double_spin()
        self.seg_vol.setRange(-40, 20)
        self.seg_vol.setSuffix(" dB")
        self.seg_vol.setKeyboardTracking(False)
        self.seg_vol.valueChanged.connect(lambda v: self._seg_set("volume_db", round(v, 1)))
        f.addRow("Volume de l'extrait", self.seg_vol)
        self.seg_zoom = double_spin()
        self.seg_zoom.setRange(100, 200)
        self.seg_zoom.setDecimals(0)
        self.seg_zoom.setSingleStep(5)
        self.seg_zoom.setSuffix(" %")
        self.seg_zoom.setKeyboardTracking(False)
        self.seg_zoom.setToolTip("Plan resserré sur cet extrait seulement (s'ajoute au zoom du cadrage)")
        self.seg_zoom.valueChanged.connect(lambda v: self._seg_set("zoom", round(v / 100, 3)))
        f.addRow("Zoom de l'extrait", self.seg_zoom)
        self.seg_fin, self.seg_fout = double_spin(), double_spin()
        for sp, attr in ((self.seg_fin, "fade_in"), (self.seg_fout, "fade_out")):
            sp.setRange(0, 5)
            sp.setDecimals(1)
            sp.setSingleStep(0.1)
            sp.setSuffix(" s")
            sp.setKeyboardTracking(False)
            sp.setSpecialValueText("aucun")
            sp.valueChanged.connect(lambda v, a=attr: self._seg_set(a, round(v, 2)))
        self.seg_fin.setToolTip("Apparition depuis le noir, image et son")
        self.seg_fout.setToolTip("Disparition vers le noir, image et son")
        f.addRow("Fondu d'ouverture", self.seg_fin)
        f.addRow("Fondu de fermeture", self.seg_fout)
        self.seg_mute = QCheckBox("Couper le son de cet extrait")
        self.seg_mute.toggled.connect(lambda v: self._seg_set("muted", bool(v)))
        f.addRow(self.seg_mute)
        row = QHBoxLayout()
        self.seg_left = button("◀ Plus tôt", tooltip="Déplacer l'extrait vers le début", on_click=lambda: self._seg_move(-1))
        self.seg_right = button("Plus tard ▶", tooltip="Déplacer l'extrait vers la fin", on_click=lambda: self._seg_move(1))
        row.addWidget(self.seg_left)
        row.addWidget(self.seg_right)
        f.addRow(row)
        row2 = QHBoxLayout()
        row2.addWidget(button("Couper ici (S)", tooltip="Coupe l'extrait à la tête de lecture",
                              on_click=self.split_requested.emit))
        self.seg_dup = button("Dupliquer", on_click=self._seg_dup)
        row2.addWidget(self.seg_dup)
        f.addRow(row2)
        self.seg_del = button("Supprimer l'extrait", "danger", on_click=self._seg_del)
        f.addRow(self.seg_del)
        f.addRow(label("Cliquez sur un extrait dans la timeline pour le régler. Les fichiers sources ne sont "
                       "jamais modifiés.", "faint", wrap=True))
        return w

    def _font_row(self, picker, size=None) -> QWidget:
        """Police (menu court, nom entier au survol), « + » pour en ajouter une, et sa taille réglable à la souris."""
        from . import theme
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(2)
        picker.setMaximumWidth(170)
        h.addWidget(picker)                               # collé à gauche, comme le reste
        add = tool("plus", "Ajouter une police (.ttf, .otf)…", lambda: self._add_font(picker), size=20)
        add.setIcon(theme.icon("plus", theme.color("faint"), 13))          # petit et discret, contre le menu
        h.addWidget(add)
        if size is not None:
            h.addSpacing(4)
            size.setToolTip("Taille du texte : glisser à gauche ou à droite, molette, ou double-clic pour saisir")
            size.setFixedWidth(SCRUB_W)                   # même taille que Début et Fin
            h.addWidget(size)
        h.addStretch(1)
        return w

    def _add_font(self, picker) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Ajouter une police", "", "Polices (*.ttf *.otf)")
        if not path:
            return
        from ..media.fonts import install_font
        try:
            fams = install_font(self.app.paths.root, path)
        except ValueError as exc:
            QMessageBox.warning(self, "Police", str(exc))
            return
        for fam in fams:
            picker.set_family(fam)
        if fams:
            picker.fontChanged.emit(fams[0])

    def _seg(self):
        try:
            return self.tl.segments[self.tl.index_of(self.sel_segment)] if self.sel_segment else None
        except KeyError:
            return None

    def _load_segment(self) -> None:
        s = self._seg()
        for wdg in (self.seg_in, self.seg_out, self.seg_vol, self.seg_zoom, self.seg_fin, self.seg_fout, self.seg_mute, self.seg_left, self.seg_right,
                    self.seg_dup, self.seg_del):
            wdg.setEnabled(s is not None)
        if not s:
            self.seg_title.setText("Aucun extrait sélectionné")
            self.seg_dur.setText("")
            return
        a = self.app.montage.asset_row(s.asset_id)
        i = self.tl.index_of(s.id)
        self.seg_title.setText(f"Extrait {i + 1} · {a['file_name'] if a else '?'}")
        dur = self.durations.get(s.asset_id) or 1e6
        for sp, v in ((self.seg_in, s.src_in), (self.seg_out, s.src_out)):
            sp.blockSignals(True)
            sp.setMaximum(dur)
            sp.setValue(v)
            sp.blockSignals(False)
        self.seg_dur.setText(f"Durée {fmt_timecode(s.duration, True)} · position sur la timeline "
                             f"{fmt_timecode(self.tl.segment_start(i), True)}")
        self.seg_vol.blockSignals(True)
        self.seg_vol.setValue(s.volume_db)
        self.seg_vol.blockSignals(False)
        for sp, v in ((self.seg_zoom, round(max(1.0, s.zoom) * 100)), (self.seg_fin, s.fade_in),
                      (self.seg_fout, s.fade_out)):
            sp.blockSignals(True)
            sp.setValue(v)
            sp.blockSignals(False)
        self.seg_mute.blockSignals(True)
        self.seg_mute.setChecked(s.muted)
        self.seg_mute.blockSignals(False)

    def _seg_trim(self) -> None:
        s = self._seg()
        if not s or self._loading:
            return
        try:
            self.tl.trim(s.id, self.seg_in.value(), self.seg_out.value(), self.durations.get(s.asset_id))
        except ValueError:
            pass
        self.changed.emit("structure")
        self._loading = True
        self._load_segment()
        self._loading = False

    def _seg_set(self, attr, v) -> None:
        s = self._seg()
        if s and not self._loading:
            setattr(s, attr, v)
            self.changed.emit("structure")

    def _seg_move(self, d) -> None:
        if self._seg():
            self.tl.move_segment(self.sel_segment, d)
            self.changed.emit("structure")
            self._load_segment()

    def _seg_dup(self) -> None:
        s = self._seg()
        if s:
            n = self.tl.add_segment(s.asset_id, s.src_in, s.src_out, self.tl.index_of(s.id) + 1)
            n.volume_db, n.muted, n.zoom, n.reframe = s.volume_db, s.muted, s.zoom, list(s.reframe)
            n.fade_in, n.fade_out = s.fade_in, s.fade_out
            self.sel_segment = n.id
            self.changed.emit("structure")
            self._load_segment()

    def _seg_del(self) -> None:
        if self._seg():
            self.tl.remove_segment(self.sel_segment)
            self.sel_segment = None
            self.changed.emit("structure")
            self._load_segment()

    # -- Cadrage et image ---------------------------------------------------------
    def _frame_tab(self) -> QWidget:
        w, f = _form()
        b = self.b
        f.addRow(label("Format", "h2"))
        self.aspect = QComboBox()
        for a in ASPECTS:
            self.aspect.addItem({"9:16": "9:16 vertical (TikTok, Reels, Shorts)", "16:9": "16:9 horizontal (YouTube)",
                                 "1:1": "1:1 carré", "4:5": "4:5 portrait (Instagram)"}[a], a)
        self.aspect.currentIndexChanged.connect(self._aspect)
        f.addRow("Format", self.aspect)
        self.res = QComboBox()
        for side, name in ((720, "720p"), (1080, "1080p (recommandé)"), (1440, "1440p"), (2160, "4K")):
            self.res.addItem(name, side)
        self.res.currentIndexChanged.connect(self._aspect)
        f.addRow("Définition", self.res)
        f.addRow("Images / seconde", b.combo("format.fps", [(24.0, "24"), (25.0, "25"), (30.0, "30"),
                                                            (50.0, "50"), (60.0, "60")]))
        f.addRow("Remplissage", b.combo("format.fit", FIT_MODES))
        self._layout_section(f)
        f.addRow(label("Cadrage", "h2"))
        f.addRow("Zoom", b.slider("video.zoom", 1.0, 3.0))
        f.addRow("Position horizontale", b.slider("video.offset_x", -1.0, 1.0))
        f.addRow("Position verticale", b.slider("video.offset_y", -1.0, 1.0))
        f.addRow("Rotation", b.combo("video.rotation", [(0, "Aucune"), (90, "90° horaire"), (180, "180°"),
                                                        (270, "90° anti-horaire")]))
        f.addRow(b.check("video.mirror", "Effet miroir (horizontal)"))
        f.addRow(b.check("video.flip", "Retourner verticalement"))
        f.addRow(button("Recentrer", on_click=self._recenter))
        rf = QHBoxLayout()
        self.reframe_btn = button("Suivre les visages (recadrage auto)", "primary",
                                  tooltip="Le cadre suit le visage principal, extrait par extrait (format vertical "
                                          "ou carré à partir d'une vidéo horizontale)",
                                  on_click=lambda: self.reframe_requested.emit(True))
        rf.addWidget(self.reframe_btn)
        rf.addWidget(button("Retirer", "ghost", on_click=lambda: self.reframe_requested.emit(False)))
        f.addRow(rf)
        self.reframe_note = label("", "faint", wrap=True)
        f.addRow(self.reframe_note)
        f.addRow(label("Image", "h2"))
        f.addRow("Luminosité", b.slider("video.brightness", -0.5, 0.5))
        f.addRow("Contraste", b.slider("video.contrast", 0.5, 1.8))
        f.addRow("Saturation", b.slider("video.saturation", 0.0, 2.0))
        f.addRow("Température", b.ispin("video.temperature", 2500, 10000, " K"))
        f.addRow("Netteté", b.slider("video.sharpen", 0.0, 1.0))
        f.addRow(button("Réinitialiser l'image", on_click=self._reset_look))
        f.addRow(label("Masquer une zone (texte incrusté, logo)", "h2"))
        self.mask_list = QListWidget()
        self.mask_list.setMaximumHeight(90)
        self.mask_list.currentRowChanged.connect(self._mask_selected)
        f.addRow(self.mask_list)
        mb = QHBoxLayout()
        mb.addWidget(button("Bande du bas (sous-titres)", on_click=lambda: self._add_mask(0.05, 0.78, 0.9, 0.16)))
        mb.addWidget(button("Coin (logo)", on_click=lambda: self._add_mask(0.78, 0.04, 0.19, 0.12)))
        mb.addWidget(button("Supprimer", "ghost", on_click=self._del_mask))
        f.addRow(mb)
        self.detect_btn = button("Détecter le texte incrusté et les logos", tooltip="Analyse l'extrait choisi (ou le "
                                 "premier) et propose des zones, à vérifier dans l'aperçu fidèle",
                                 on_click=self.detect_text_requested.emit)
        f.addRow(self.detect_btn)
        self.mask_mode = QComboBox()
        for k, n in (("blur", "Flou"), ("delogo", "Effacement (reconstruit depuis les bords)"),
                     ("box", "Bande noire")):
            self.mask_mode.addItem(n, k)
        self.mask_mode.currentIndexChanged.connect(self._mask_edit)
        f.addRow("Méthode", self.mask_mode)
        self.mask_spins = {}
        for key, name in (("x", "Gauche"), ("y", "Haut"), ("w", "Largeur"), ("h", "Hauteur")):
            sp = double_spin()
            sp.setRange(0, 100)
            sp.setDecimals(1)
            sp.setSuffix(" %")
            sp.setKeyboardTracking(False)
            sp.valueChanged.connect(self._mask_edit)
            self.mask_spins[key] = sp
        g = QHBoxLayout()
        for key in ("x", "y", "w", "h"):
            g.addWidget(self.mask_spins[key])
        f.addRow("Zone (gauche, haut, largeur, hauteur)", g)
        f.addRow(label("Zone en % de l'image d'origine. Contrôlez le résultat avec « Aperçu fidèle » : la "
                       "lecture en direct n'affiche pas les masques. La détection propose des zones : vérifiez-les "
                       "toujours avant d'exporter.", "faint", wrap=True))
        f.addRow(label("Le miroir ou un filtre ne rendent pas un contenu « original » : vérifiez vos droits "
                       "sur les sources.", "faint", wrap=True))
        return w

    # -- mise en page (double cadrage, deux vidéos, incrustation) ------------------------
    def _layout_section(self, f: QFormLayout) -> None:
        f.addRow(label("Mise en page", "h2"))
        self.lay_kind = QComboBox()
        for k, n in compose.KINDS.items():
            self.lay_kind.addItem(n, k)
        self.lay_kind.setToolTip("Double cadrage : la même vidéo en deux vues indépendantes (ex. intervenant A en "
                                 "haut, B en bas), synchronisées, avec un seul son.")
        self.lay_kind.currentIndexChanged.connect(self._lay_kind)
        f.addRow("Disposition", self.lay_kind)
        self.lay_box = QWidget()
        g = QFormLayout(self.lay_box)
        g.setContentsMargins(0, 0, 0, 0)
        g.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.lay_view = QComboBox()
        self.lay_view.currentIndexChanged.connect(lambda _i: self._reload_layout())
        g.addRow("Vue réglée", self.lay_view)
        self.lay_sl = {}
        for key, name, lo, hi in (("zoom", "Zoom de la vue", 100, 400), ("cx", "Position horizontale", 0, 100),
                                  ("cy", "Position verticale", 0, 100)):
            sl = QSlider(Qt.Horizontal)
            sl.setFixedWidth(SLIDER_W)
            sl.setRange(lo, hi)
            sl.valueChanged.connect(lambda v, k=key: self._lay_view_set(k, v))
            self.lay_sl[key] = sl
            g.addRow(name, sl)
        self.lay_ratio = int_spin()
        self.lay_ratio.setRange(int(compose.RATIO_MIN * 100), int(compose.RATIO_MAX * 100))
        self.lay_ratio.setSuffix(" % en haut")
        self.lay_ratio.setKeyboardTracking(False)
        self.lay_ratio.valueChanged.connect(lambda v: self._lay_set("ratio", v / 100))
        self.lay_ratio_row = QWidget()
        rr = QHBoxLayout(self.lay_ratio_row)
        rr.setContentsMargins(0, 0, 0, 0)
        rr.addWidget(self.lay_ratio, 1)
        for a in (50, 60, 40):
            rr.addWidget(button(f"{a}/{100 - a}", "ghost", on_click=lambda a=a: self.lay_ratio.setValue(a)))
        g.addRow("Partage", self.lay_ratio_row)
        self.lay_swap = button("Inverser haut et bas",
                               on_click=lambda: self._lay_set("swap", not self._lay()["swap"], reload=True))
        g.addRow(self.lay_swap)
        self.lay_second = QComboBox()
        self.lay_second.currentIndexChanged.connect(
            lambda _i: self._lay_set("asset2", self.lay_second.currentData(), reload=True))
        g.addRow("Seconde vidéo", self.lay_second)
        self.lay_offset = double_spin()
        self.lay_offset.setRange(-36000, 36000)
        self.lay_offset.setDecimals(2)
        self.lay_offset.setSingleStep(0.1)
        self.lay_offset.setSuffix(" s")
        self.lay_offset.setKeyboardTracking(False)
        self.lay_offset.setToolTip("Décalage de la seconde vidéo par rapport à la principale")
        self.lay_offset.valueChanged.connect(lambda v: self._lay_set("offset", round(v, 2)))
        g.addRow("Décalage", self.lay_offset)
        self.lay_audio = QComboBox()
        for k, n in compose.AUDIO.items():
            self.lay_audio.addItem(n, k)
        self.lay_audio.currentIndexChanged.connect(lambda _i: self._lay_set("audio", self.lay_audio.currentData()))
        g.addRow("Son conservé", self.lay_audio)
        self.lay_pip = {}
        for key, name, lo, hi in (("w", "Largeur de l'incrustation", 10, 100),
                                  ("h", "Hauteur de l'incrustation", 6, 100),
                                  ("x", "Incrustation : gauche", 0, 90), ("y", "Incrustation : haut", 0, 94)):
            sp = int_spin()
            sp.setRange(lo, hi)
            sp.setSuffix(" %")
            sp.setKeyboardTracking(False)
            sp.valueChanged.connect(lambda v, k=key: self._lay_pip_set(k, v))
            self.lay_pip[key] = sp
            g.addRow(name, sp)
        self.lay_border = int_spin()
        self.lay_border.setRange(0, 40)
        self.lay_border.setSuffix(" px")
        self.lay_border.setKeyboardTracking(False)
        self.lay_border.valueChanged.connect(lambda v: self._lay_set("border", int(v)))
        self.lay_border_col = ColorButton("#FFFFFF")
        self.lay_border_col.picked.connect(lambda c: self._lay_set("border_color", c))
        self.lay_border_row = QWidget()
        br = QHBoxLayout(self.lay_border_row)
        br.setContentsMargins(0, 0, 0, 0)
        br.addWidget(self.lay_border, 1)
        br.addWidget(self.lay_border_col)
        g.addRow("Contour", self.lay_border_row)
        self.lay_note = label("", "faint", wrap=True)
        g.addRow(self.lay_note)
        f.addRow(self.lay_box)

    def _lay(self) -> dict:
        return compose.norm(self.tl.video.layout)

    def _reload_layout(self) -> None:
        was = self._loading
        self._loading = True
        try:
            self._load_layout()
        finally:
            self._loading = was

    def _lay_commit(self, lay: dict, reload: bool = False) -> None:
        self.tl.video.layout = compose.norm(lay)
        if reload:
            self._reload_layout()
        self.changed.emit("video.layout")

    def _lay_set(self, key: str, value, reload: bool = False) -> None:
        if self._loading:
            return
        lay = self._lay()
        lay[key] = value
        self._lay_commit(lay, reload)

    def _lay_kind(self) -> None:
        if self._loading:
            return
        lay = self._lay()
        lay["kind"] = self.lay_kind.currentData()
        if lay["kind"] == "split2" and lay["asset2"] is None:
            used = {s.asset_id for s in self.tl.segments}
            others = [aid for aid, _n in self._project_videos() if aid not in used]
            lay["asset2"] = others[0] if others else None
        if lay["kind"] == "pip" and lay["views"][0] == compose.DEFAULT["views"][0]:
            lay["views"][0] = {"cx": 0.5, "cy": 0.5, "zoom": 1.0}        # vidéo principale : plan d'ensemble
        self._lay_commit(lay, reload=True)

    def _lay_view_set(self, key: str, v: int) -> None:
        if self._loading:
            return
        lay = self._lay()
        lay["views"][max(0, self.lay_view.currentIndex())][key] = v / 100
        self._lay_commit(lay)

    def _lay_pip_set(self, key: str, v: int) -> None:
        if self._loading:
            return
        lay = self._lay()
        lay["pip"][key] = v / 100
        self._lay_commit(lay)

    def _project_videos(self) -> list[tuple[int, str]]:
        if not self.project_id:
            return []
        return [(r["id"], r["file_name"]) for r in self.app.db.query(
            "SELECT id, file_name FROM source_asset WHERE project_id = ? AND kind = 'video' ORDER BY id",
            (self.project_id,))]

    def _show_row(self, w: QWidget, on: bool) -> None:
        w.setVisible(on)
        lb = self.lay_box.layout().labelForField(w)
        if lb:
            lb.setVisible(on)

    def _load_layout(self) -> None:
        # appelé avec _loading = True : aucun réglage n'est réécrit pendant le remplissage
        lay = self._lay()
        kind = lay["kind"]
        self.lay_kind.setCurrentIndex(max(0, self.lay_kind.findData(kind)))
        self.lay_box.setVisible(kind != "single")
        if kind == "single":
            return
        two = kind in ("split2", "pip")
        if kind == "pip":
            names = ["Vidéo principale", "Incrustation"]
        else:
            names = ["Vue du bas", "Vue du haut"] if lay["swap"] else ["Vue du haut", "Vue du bas"]
            if kind == "split2":
                names[1] += " (seconde vidéo)"
        cur = max(0, self.lay_view.currentIndex())
        self.lay_view.blockSignals(True)
        self.lay_view.clear()
        self.lay_view.addItems(names)
        self.lay_view.setCurrentIndex(min(cur, 1))
        self.lay_view.blockSignals(False)
        v = lay["views"][self.lay_view.currentIndex()]
        for key, sl in self.lay_sl.items():
            sl.setValue(int(round(v[key] * 100)))
        self._show_row(self.lay_ratio_row, kind != "pip")
        self.lay_swap.setVisible(kind != "pip")
        self.lay_ratio.setValue(int(round(lay["ratio"] * 100)))
        for w in (self.lay_second, self.lay_offset, self.lay_audio):
            self._show_row(w, two)
        self.lay_second.blockSignals(True)
        self.lay_second.clear()
        if kind == "pip":
            self.lay_second.addItem("La même vidéo (gros plan)", None)
        for aid, name in self._project_videos():
            self.lay_second.addItem(name, aid)
        self.lay_second.setCurrentIndex(max(0, self.lay_second.findData(lay["asset2"])))
        self.lay_second.blockSignals(False)
        self.lay_offset.setValue(float(lay["offset"] or 0))
        self.lay_audio.setCurrentIndex(max(0, self.lay_audio.findData(lay["audio"])))
        for key, sp in self.lay_pip.items():
            self._show_row(sp, kind == "pip")
            sp.setValue(int(round(lay["pip"][key] * 100)))
        self._show_row(self.lay_border_row, kind == "pip")
        self.lay_border.setValue(int(lay["border"] or 0))
        self.lay_border_col.set(lay["border_color"] or "#FFFFFF")
        note = "Glissez une vue dans l'aperçu pour la déplacer, molette pour zoomer."
        if kind == "split2" and lay["asset2"] is None:
            note = "Importez une seconde vidéo (Médias) pour l'afficher en bas. " + note
        elif not compose.uses_second(lay):
            note += " La vidéo est lue une seule fois : le son n'est pas doublé."
        self.lay_note.setText(note)

    # -- zones masquées ------------------------------------------------------------
    def _load_masks(self) -> None:
        names = {"blur": "Flou", "delogo": "Effacement", "box": "Bande noire"}
        row = self.mask_list.currentRow()
        self.mask_list.blockSignals(True)
        self.mask_list.clear()
        for m in self.tl.video.masks:
            self.mask_list.addItem(f"{names.get(m.get('mode'), '?')} · {m['x'] * 100:.0f}, {m['y'] * 100:.0f} — "
                                   f"{m['w'] * 100:.0f} × {m['h'] * 100:.0f} %")
        self.mask_list.blockSignals(False)
        if self.tl.video.masks:
            self.mask_list.setCurrentRow(min(max(0, row), len(self.tl.video.masks) - 1))
        self._mask_selected(self.mask_list.currentRow())

    def _mask_selected(self, row: int) -> None:
        on = 0 <= row < len(self.tl.video.masks)
        was = self._loading
        self._loading = True
        try:
            for k, sp in self.mask_spins.items():
                sp.setEnabled(on)
                sp.setValue(self.tl.video.masks[row][k] * 100 if on else 0)
            self.mask_mode.setEnabled(on)
            if on:
                self.mask_mode.setCurrentIndex(max(0, self.mask_mode.findData(self.tl.video.masks[row].get("mode"))))
        finally:
            self._loading = was

    def add_detected(self, zones: list[dict]) -> int:
        """Ajoute les zones détectées qui ne recouvrent pas déjà une zone existante."""
        added = 0
        for z in zones:
            dup = any(abs(z["x"] - m["x"]) < 0.04 and abs(z["y"] - m["y"]) < 0.04 for m in self.tl.video.masks)
            if not dup:
                self.tl.video.masks.append({k: z[k] for k in ("x", "y", "w", "h", "mode")})
                added += 1
        if added:
            self._loading = True
            try:
                self._load_masks()
                self.mask_list.setCurrentRow(len(self.tl.video.masks) - 1)
            finally:
                self._loading = False
            self.changed.emit("structure")
        return added

    def _add_mask(self, x, y, w, h) -> None:
        self.tl.video.masks.append({"x": x, "y": y, "w": w, "h": h, "mode": "blur"})
        self._loading = True
        try:
            self._load_masks()
            self.mask_list.setCurrentRow(len(self.tl.video.masks) - 1)
        finally:
            self._loading = False
        self.changed.emit("structure")

    def _del_mask(self) -> None:
        row = self.mask_list.currentRow()
        if 0 <= row < len(self.tl.video.masks):
            self.tl.video.masks.pop(row)
            self._loading = True
            try:
                self._load_masks()
            finally:
                self._loading = False
            self.changed.emit("structure")

    def _mask_edit(self) -> None:
        row = self.mask_list.currentRow()
        if self._loading or not (0 <= row < len(self.tl.video.masks)):
            return
        m = self.tl.video.masks[row]
        for k, sp in self.mask_spins.items():
            m[k] = round(sp.value() / 100, 4)
        m["mode"] = self.mask_mode.currentData()
        self._loading = True
        try:
            self._load_masks()
        finally:
            self._loading = False
        self.changed.emit("video.masks")

    def reload_reframe(self) -> None:
        segs = self.tl.segments
        done = sum(bool(sg.reframe) for sg in segs)
        if not segs:
            self.reframe_note.setText("")
        elif self.tl.format.fit != "fill":
            self.reframe_note.setText("Le recadrage automatique s'applique en mode « Remplir » (onglet Cadrage).")
        elif done:
            self.reframe_note.setText(f"✓ Le cadre suit les visages sur {done}/{len(segs)} extrait(s). Zoom et "
                                      "position verticale restent réglables ; la position horizontale est automatique.")
        else:
            self.reframe_note.setText("Le cadre est fixe. « Suivre les visages » l'oriente automatiquement vers la "
                                      "personne qui parle ou qui est la plus visible.")

    def _aspect(self) -> None:
        if self._loading:
            return
        self.tl.set_aspect(self.aspect.currentData(), self.res.currentData())
        self.changed.emit("format")

    def _recenter(self) -> None:
        self.tl.video.zoom, self.tl.video.offset_x, self.tl.video.offset_y = 1.0, 0.0, 0.0
        self.refresh()
        self.changed.emit("video")

    def _reset_look(self) -> None:
        v = self.tl.video
        v.brightness, v.contrast, v.saturation, v.temperature, v.sharpen = 0.0, 1.0, 1.0, 6500, 0.0
        self.refresh()
        self.changed.emit("video")

    # -- Textes -------------------------------------------------------------
    def _texts_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 10, 8, 10)
        self.text_list = QListWidget()
        self.text_list.setMaximumHeight(120)
        self.text_list.currentItemChanged.connect(self._text_pick)
        lay.addWidget(self.text_list)
        # boutons d'ajout : chacun montre le texte qu'il ajoute, dans son style
        grid = QGridLayout()
        grid.setSpacing(6)
        self.add_btns: dict[str, QPushButton] = {}
        for i, (kind, name) in enumerate(TEXT_KINDS):
            b = QPushButton()
            b.setToolTip(f"Ajouter : {name}")
            b.setFixedHeight(32)
            b.setStyleSheet("padding: 2px 6px;")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=kind: self._add_text(k))
            grid.addWidget(b, i // 2, i % 2)
            self.add_btns[kind] = b
        lay.addLayout(grid)
        fw, f = _form()
        f.setVerticalSpacing(12)                          # de l'air entre les réglages, sans libellés
        bt = self.bt
        self.text_edit = QPlainTextEdit()
        self.text_edit.setMaximumHeight(64)
        self.text_edit.setPlaceholderText("Texte")
        self.text_edit.textChanged.connect(self._text_changed)
        f.addRow(self.text_edit)
        # début et fin réglables à la souris (comme en Clipping), puis « Tout »
        from .scrub import ScrubTime
        self.t_start, self.t_end = ScrubTime(), ScrubTime()
        self.t_start.setToolTip("Début")
        self.t_end.setToolTip("Fin")
        for s_ in (self.t_start, self.t_end):
            s_.setFixedWidth(SCRUB_W)
        self.t_start.edited.connect(lambda v: self._text_time_set(start=v))
        self.t_end.edited.connect(lambda v: self._text_time_set(end=v))
        self.t_all = button("Tout", "mini", tooltip="Pendant toute la vidéo", on_click=lambda: self._text_time("all"))
        tg = QGridLayout()
        tg.setHorizontalSpacing(6)
        tg.setVerticalSpacing(2)
        tg.addWidget(label("Début", "faint"), 0, 0)
        tg.addWidget(label("Fin", "faint"), 0, 1)
        tg.addWidget(self.t_start, 1, 0)
        tg.addWidget(self.t_end, 1, 1)
        tg.addWidget(self.t_all, 1, 2)
        tg.setColumnStretch(3, 1)
        f.addRow(tg)
        # position : haut / centre / bas, puis X et Y libres (partent de la position choisie)
        self.pos_group = QButtonGroup(self)
        self.pos_group.setExclusive(False)
        self.pos_btns: dict[str, QPushButton] = {}
        pr = QHBoxLayout()
        pr.setSpacing(5)
        for key, name in (("top", "Haut"), ("center", "Centre"), ("bottom", "Bas")):
            b = button(name, "mini", tooltip=f"Position : {dict(POSITIONS)[key].lower()}")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self._text_pos(k))
            self.pos_group.addButton(b)
            self.pos_btns[key] = b
            pr.addWidget(b)
        pr.addStretch(1)
        f.addRow(pr)
        self.text_x, self.text_y = QSlider(Qt.Horizontal), QSlider(Qt.Horizontal)
        xy = QHBoxLayout()
        xy.setSpacing(6)
        for name, s_ in (("X", self.text_x), ("Y", self.text_y)):
            s_.setRange(0, 1000)
            s_.setFixedWidth(SLIDER_W)
            s_.setToolTip(f"Position {name} (libre)")
            s_.valueChanged.connect(self._text_xy)
            xy.addWidget(label(name, "faint"))
            xy.addWidget(s_)
            xy.addSpacing(8)
        xy.addStretch(1)
        f.addRow(xy)
        # police, taille, gras, italique, alignement : sans libellés, comme un éditeur de texte
        f.addRow(self._font_row(bt.font("font"), bt.scrub("size", 16, 240)))
        st = QHBoxLayout()
        st.setSpacing(2)
        st.addWidget(bt.tbtn("bold", "B", "Gras", bold=True))
        st.addWidget(bt.tbtn("italic", "I", "Italique", italic=True))
        st.addSpacing(10)
        st.addWidget(bt.choice("align", [("left", "align-left", "Aligné à gauche"),
                                         ("center", "align-center", "Centré"),
                                         ("right", "align-right", "Aligné à droite")]))
        st.addStretch(1)
        f.addRow(st)
        # couleur, contour, fond, largeur, fondu : une icône grise et la commande (le nom au survol)
        cl = _hint_row("text-color", "Couleur du texte")
        c_ = bt.color("color")
        c_.setToolTip("Couleur du texte")
        cl.addWidget(c_)
        cl.addStretch(1)
        f.addRow(cl)
        ol = _hint_row("text-outline", "Contour")
        o_ = bt.color("outline_color")
        o_.setToolTip("Couleur du contour")
        ol.addWidget(o_)
        ow = bt.ispin("outline", 0, 30, " px")
        ow.setToolTip("Épaisseur du contour")
        ol.addWidget(ow)
        ol.addStretch(1)
        f.addRow(ol)
        bx = _hint_row("text-box", "Fond")
        b_ = bt.color("box_color")
        b_.setToolTip("Couleur du fond")
        bx.addWidget(b_)
        self.box_op = bt.slider("box_opacity", 0.0, 1.0)  # opacité 0 : pas de fond
        self.box_op.setToolTip("Fond : opacité (tout à gauche, pas de fond)")
        self.box_op.valueChanged.connect(self._box_opacity)
        bx.addWidget(self.box_op)
        bx.addStretch(1)
        f.addRow(bx)
        wl = _hint_row("move-horizontal", "Largeur maximale du texte")
        mw = bt.slider("max_width", 0.3, 1.0)
        mw.setToolTip("Largeur maximale du texte")
        wl.addWidget(mw)
        wl.addSpacing(14)
        fl_ = _hint_row("blend", "Fondu (apparition et disparition)")
        wl.addLayout(fl_)
        fd = bt.spin("fade", 0, 3, 0.1, 1, " s")
        fd.setToolTip("Fondu (apparition et disparition)")
        wl.addWidget(fd)
        wl.addStretch(1)
        f.addRow(wl)
        self.text_del = button("Supprimer ce texte", "danger", on_click=self._del_text)
        f.addRow(self.text_del)
        lay.addWidget(fw)
        lay.addWidget(label("Les accroches ne doivent pas annoncer un contenu absent de la vidéo.", "faint", wrap=True))
        lay.addStretch(1)
        return w

    def _load_texts(self) -> None:
        self.text_list.blockSignals(True)
        self.text_list.clear()
        for t in sorted(self.tl.texts, key=lambda x: x.start):
            it = QListWidgetItem(f"{fmt_timecode(t.start)}–{fmt_timecode(t.end)}  {t.text.splitlines()[0] if t.text else ''}")
            it.setData(Qt.UserRole, t.id)
            self.text_list.addItem(it)
            if t.id == self.sel_text:
                self.text_list.setCurrentItem(it)
        self.text_list.blockSignals(False)
        t = self.current_text()
        self.text_edit.blockSignals(True)
        self.text_edit.setPlainText(t.text if t else "")
        self.text_edit.setEnabled(t is not None)
        self.text_edit.blockSignals(False)
        self.text_del.setEnabled(t is not None)
        self.bt.load()
        if t is not None and not t.box:                   # sans fond : l'opacité est à zéro
            self.box_op.blockSignals(True)
            self.box_op.setValue(0)
            self.box_op.blockSignals(False)
        for w_ in (self.t_start, self.t_end, self.t_all, self.text_x, self.text_y, *self.pos_btns.values()):
            w_.setEnabled(t is not None)
        dur = max(0.2, self.tl.duration)
        for s_, v in ((self.t_start, t.start if t else 0.0), (self.t_end, t.end if t else 0.0)):
            s_.setRange(0.0, max(dur, v))
            s_.setValue(v)
        self._load_position(t)
        self._paint_add_buttons()

    def _paint_add_buttons(self) -> None:
        """Chaque bouton d'ajout montre son texte avec le style qu'il aura dans la vidéo."""
        style = self.app.montage.text_style_for(self.project_id) if self.project_id else {}
        for kind, name in TEXT_KINDS:
            params = {**TextItem("").__dict__, **self._preset_params(kind, style)}
            pm = preset_pixmap(params, name, 14)
            b = self.add_btns[kind]
            b.setIcon(QIcon(pm))
            b.setIconSize(QSize(min(120, int(pm.width() / pm.devicePixelRatio())),
                                int(pm.height() / pm.devicePixelRatio())))

    def _load_position(self, t) -> None:
        from ..edit.textrender import anchor
        for k, b in self.pos_btns.items():
            b.setChecked(t is not None and t.position == k)
        x, y = anchor(t, self.tl.format.width, self.tl.format.height) if t else (0.5, 0.5)
        for s_, v in ((self.text_x, x), (self.text_y, y)):
            s_.blockSignals(True)
            s_.setValue(int(round(v * 1000)))
            s_.blockSignals(False)

    def _text_pos(self, key: str) -> None:
        """Haut / Centre / Bas : le texte y retourne, et X / Y prennent ces coordonnées."""
        t = self.current_text()
        if not t:
            return
        from ..edit.textrender import anchor
        t.position = key
        t.x, t.y = anchor(t, self.tl.format.width, self.tl.format.height)
        self._load_position(t)
        self.changed.emit("texts")

    def _text_xy(self) -> None:
        """X / Y bougés : le texte se place librement (plus en haut, au centre ou en bas)."""
        t = self.current_text()
        if not t or self._loading:
            return
        t.position = "custom"
        t.x, t.y = self.text_x.value() / 1000, self.text_y.value() / 1000
        for b in self.pos_btns.values():
            b.setChecked(False)
        self.changed.emit("texts")

    def _text_time_set(self, start: float | None = None, end: float | None = None) -> None:
        t = self.current_text()
        if not t:
            return
        if start is not None:
            t.start = max(0.0, min(start, t.end - 0.2))
        if end is not None:
            t.end = max(end, t.start + 0.2)
        self._loading = True
        self._load_texts()
        self._loading = False
        self.changed.emit("texts")

    def _text_pick(self, it, _prev=None) -> None:
        if it and not self._loading:
            self.sel_text = it.data(Qt.UserRole)
            self._loading = True
            self._load_texts()
            self._loading = False
            self.changed.emit("text-select")

    @staticmethod
    def _preset_params(kind: str, style: dict) -> dict:
        presets = {
            "title": dict(text="Titre", position="top", size=80, box=False),
            "hook": dict(text="Accroche", position="center", size=96, box=True, box_color="#000000",
                         box_opacity=0.5),
            "cta": dict(text="Abonnez-vous !", position="bottom", size=70, color="#FFD54A"),
            "lower": dict(text="Nom de l'intervenant", position="custom", x=0.5, y=0.7, size=56, box=True),
        }[kind]
        style = {k: v for k, v in style.items() if k not in ("text", "start", "end", "id")}
        return {**style, **presets} if kind != "title" else {**presets, **style, "text": "Titre"}

    def _add_text(self, kind: str) -> None:
        style = self.app.montage.text_style_for(self.project_id) if self.project_id else {}
        t0 = self.playhead
        params = self._preset_params(kind, style)
        text = params.pop("text")
        if kind in ("title", "hook") and self.tl.duration > 0.3:
            t0, end = 0.0, self.tl.duration                 # titre et accroche : toute la vidéo
        else:
            end = min(self.tl.duration, t0 + 3.0) if self.tl.duration > t0 + 0.3 else t0 + 3.0
        item = self.tl.add_text(text, t0, end, **params)
        self.sel_text = item.id
        self._loading = True
        self._load_texts()
        self._loading = False
        self.changed.emit("texts")
        self.text_edit.setFocus()
        self.text_edit.selectAll()
        self.text_added.emit(item.id)

    def _text_changed(self) -> None:
        t = self.current_text()
        if t and not self._loading:
            t.text = self.text_edit.toPlainText()
            cur = self.text_list.currentItem()
            if cur:
                cur.setText(f"{fmt_timecode(t.start)}–{fmt_timecode(t.end)}  {t.text.splitlines()[0] if t.text else ''}")
            self.changed.emit("texts")

    def _text_time(self, which: str) -> None:
        t = self.current_text()
        if not t:
            return
        if which == "start":
            t.start = min(self.playhead, t.end - 0.2)
        elif which == "end":
            t.end = max(self.playhead, t.start + 0.2)
        else:
            t.start, t.end = 0.0, max(0.2, self.tl.duration)
        self.refresh()
        self.changed.emit("texts")

    def _box_opacity(self, v: int) -> None:
        """Le fond existe dès que son opacité dépasse zéro (plus de case « Rectangle »)."""
        t = self.current_text()
        if t is None or self._loading or t.box == (v > 0):
            return
        t.box = v > 0
        self.changed.emit("texts")

    def _del_text(self) -> None:
        if self.sel_text:
            self.tl.remove_text(self.sel_text)
            self.sel_text = None
            self.refresh()
            self.changed.emit("texts")

    # -- Sous-titres ----------------------------------------------------------
    def _subs_tab(self) -> QWidget:
        w, f = _form()
        b = self.b
        f.addRow(b.check("subtitles.enabled", "Afficher des sous-titres"))
        self.sub_lang = QComboBox()
        self.sub_lang.setToolTip("Langue des sous-titres : texte d'origine ou une traduction (puis « Générer »)")
        self.sub_lang.currentIndexChanged.connect(self._sub_lang)
        f.addRow("Langue", self.sub_lang)
        self.gen_btn = button("Générer depuis la transcription", "primary",
                              tooltip="Crée les sous-titres à partir des transcriptions des extraits de la timeline",
                              on_click=self.generate_subs_requested.emit)
        f.addRow(self.gen_btn)
        self.subs_info = label("", "faint", wrap=True)
        f.addRow(self.subs_info)
        f.addRow(label("Découpage", "h2"))
        f.addRow("Mots max.", b.ispin("subtitles.rules.max_words", 1, 20))
        f.addRow("Caractères / ligne", b.ispin("subtitles.rules.max_chars", 8, 60))
        f.addRow("Lignes max.", b.ispin("subtitles.rules.max_lines", 1, 3))
        f.addRow("Couper après une pause de", b.spin("subtitles.rules.pause_split", 0.2, 3.0, 0.05, 2, " s"))
        f.addRow("Durée minimale", b.spin("subtitles.rules.min_duration", 0.2, 3.0, 0.1, 1, " s"))
        f.addRow(label("Après un changement de découpage, cliquez sur « Générer » (vos corrections seront "
                       "remplacées).", "faint", wrap=True))
        f.addRow(label("Style", "h2"))
        f.addRow("Animation", b.combo("subtitles.animation", [
            ("highlight", "Mot prononcé surligné"), ("word", "Apparition mot par mot"),
            ("pop", "Effet pop + surlignage"), ("fade", "Apparition progressive"), ("none", "Aucune")]))
        f.addRow("Position", b.combo("subtitles.style.position", POSITIONS))
        f.addRow("Hauteur (libre)", b.slider("subtitles.style.y", 0.1, 0.9))
        f.addRow(self._font_row(b.font("subtitles.style.font"), b.scrub("subtitles.style.size", 20, 200)))
        st = QHBoxLayout()
        st.setSpacing(2)
        st.addWidget(b.tbtn("subtitles.style.bold", "B", "Gras", bold=True))
        st.addWidget(b.tbtn("subtitles.style.italic", "I", "Italique", italic=True))
        st.addSpacing(10)
        st.addWidget(b.check("subtitles.style.uppercase", "MAJUSCULES"))
        st.addStretch(1)
        f.addRow(st)
        cl = QHBoxLayout()
        cl.addWidget(label("Texte", "faint"))
        cl.addWidget(b.color("subtitles.style.color"))
        cl.addWidget(label("Mot actif", "faint"))
        cl.addWidget(b.color("subtitles.style.highlight_color"))
        cl.addStretch(1)
        f.addRow("Couleurs", cl)
        ol = QHBoxLayout()
        ol.addWidget(b.color("subtitles.style.outline_color"))
        ol.addWidget(b.ispin("subtitles.style.outline", 0, 30, " px"))
        ol.addWidget(b.check("subtitles.style.shadow", "Ombre"))
        f.addRow("Contour", ol)
        bx = QHBoxLayout()
        bx.addWidget(b.check("subtitles.style.box", "Rectangle"))
        bx.addWidget(b.color("subtitles.style.box_color"))
        bx.addWidget(b.slider("subtitles.style.box_opacity", 0.0, 1.0))
        f.addRow("Fond", bx)
        f.addRow("Largeur max.", b.slider("subtitles.style.max_width", 0.4, 1.0))
        f.addRow(label("Sortie", "h2"))
        f.addRow(b.check("subtitles.burn", "Incruster dans la vidéo"))
        f.addRow(b.check("subtitles.sidecar_srt", "Créer aussi un fichier .srt séparé"))
        f.addRow(label("Sous-titres", "h2"))
        self.cue_list = QListWidget()
        self.cue_list.setMinimumHeight(150)
        self.cue_list.currentItemChanged.connect(self._cue_pick)
        f.addRow(self.cue_list)
        self.cue_text = QLineEdit()
        self.cue_text.setPlaceholderText("Texte du sous-titre sélectionné")
        self.cue_text.editingFinished.connect(self._cue_text)
        f.addRow("Texte", self.cue_text)
        from .scrub import ScrubTime
        self.cue_start, self.cue_end = ScrubTime(), ScrubTime()      # réglables à la souris
        tm = QGridLayout()
        tm.setHorizontalSpacing(6)
        tm.setVerticalSpacing(2)
        tm.addWidget(label("Début", "muted"), 0, 0)
        tm.addWidget(label("Fin", "muted"), 0, 1)
        for i, sp in enumerate((self.cue_start, self.cue_end)):
            sp.edited.connect(lambda _v: self._cue_times())
            tm.addWidget(sp, 1, i)
        f.addRow(tm)
        f.addRow(button("Supprimer ce sous-titre", "danger", on_click=self._cue_delete))
        f.addRow(button("+ Sous-titre à la tête de lecture", "ghost", on_click=self._cue_add))
        return w

    def _cue(self):
        it = self.cue_list.currentItem()
        if not it:
            return None
        try:
            return self.tl.subtitles.cue(it.data(Qt.UserRole))
        except KeyError:
            return None

    def _fill_sub_langs(self) -> None:
        from .dialogs import LANGUAGES
        names = dict(LANGUAGES)
        langs = set()
        for sg in self.tl.segments:
            langs.update(self.app.transcripts.languages(sg.asset_id))
        if self.tl.subtitles.language:
            langs.add(self.tl.subtitles.language)
        self.sub_lang.blockSignals(True)
        self.sub_lang.clear()
        self.sub_lang.addItem("Texte d'origine", "")
        for c in sorted(langs):
            self.sub_lang.addItem(names.get(c, c), c)
        self.sub_lang.setCurrentIndex(max(0, self.sub_lang.findData(self.tl.subtitles.language)))
        self.sub_lang.blockSignals(False)

    def _sub_lang(self) -> None:
        if self._loading:
            return
        self.tl.subtitles.language = self.sub_lang.currentData() or ""
        self.changed.emit("subtitles")
        if self.tl.subtitles.cues:
            self.subs_info.setText("Langue changée : cliquez sur « Générer » pour refaire les sous-titres.")

    def _load_cues(self) -> None:
        self._fill_sub_langs()
        st = self.tl.subtitles
        cur = self.cue_list.currentItem().data(Qt.UserRole) if self.cue_list.currentItem() else None
        self.cue_list.blockSignals(True)
        self.cue_list.clear()
        for c in sorted(st.cues, key=lambda x: x.start):
            it = QListWidgetItem(f"{fmt_timecode(c.start, True)}  {c.text}")
            it.setData(Qt.UserRole, c.id)
            self.cue_list.addItem(it)
            if c.id == cur:
                self.cue_list.setCurrentItem(it)
        self.cue_list.blockSignals(False)
        self.subs_info.setText(f"{len(st.cues)} sous-titre(s)." if st.cues else
                               "Aucun sous-titre. Transcrivez les médias (panneau « Transcription » à gauche), "
                               "puis cliquez sur « Générer ».")
        c = self._cue()
        for wdg in (self.cue_text, self.cue_start, self.cue_end):
            wdg.setEnabled(c is not None)
            wdg.blockSignals(True)
        self.cue_text.setText(c.text if c else "")
        for sp, v in ((self.cue_start, c.start if c else 0.0), (self.cue_end, c.end if c else 0.0)):
            sp.setRange(0.0, max(self.tl.duration, v))
            sp.setValue(v)
        for wdg in (self.cue_text, self.cue_start, self.cue_end):
            wdg.blockSignals(False)

    def _cue_pick(self, it, _prev=None) -> None:
        if self._loading or not it:
            return
        self._loading = True
        self._load_cues()
        self._loading = False
        c = self._cue()
        if c:
            self.cue_selected.emit(c.start + 0.01)

    def _cue_text(self) -> None:
        c = self._cue()
        if c and not self._loading and self.cue_text.text().strip() != c.text:
            c.text = " ".join(self.cue_text.text().split())
            c.words = []          # horodatage des mots recalculé uniformément
            self.cue_list.currentItem().setText(f"{fmt_timecode(c.start, True)}  {c.text}")
            self.changed.emit("subtitles")

    def _cue_times(self) -> None:
        c = self._cue()
        if not c or self._loading:
            return
        a, b = self.cue_start.value(), self.cue_end.value()
        if b - a < 0.1:
            return
        if c.words:
            k = (b - a) / max(1e-6, c.end - c.start)
            c.words = [[a + (w[0] - c.start) * k, a + (w[1] - c.start) * k, w[2]] for w in c.words]
        c.start, c.end = a, b
        self.tl.subtitles.sort()
        self.changed.emit("subtitles")
        self._loading = True
        self._load_cues()
        self._loading = False

    def _cue_delete(self) -> None:
        c = self._cue()
        if c:
            self.tl.subtitles.cues = [x for x in self.tl.subtitles.cues if x.id != c.id]
            self._loading = True
            self._load_cues()
            self._loading = False
            self.changed.emit("subtitles")

    def _cue_add(self) -> None:
        from ..edit.subtitles import Cue
        t = self.playhead
        nxt = min((c.start for c in self.tl.subtitles.cues if c.start > t), default=t + 2.0)
        c = Cue(t, max(t + 0.3, min(t + 2.0, nxt)), "Nouveau sous-titre")
        self.tl.subtitles.cues.append(c)
        self.tl.subtitles.sort()
        self.tl.subtitles.enabled = True
        self.refresh()
        for i in range(self.cue_list.count()):
            if self.cue_list.item(i).data(Qt.UserRole) == c.id:
                self.cue_list.setCurrentRow(i)
        self.changed.emit("subtitles")
        self.cue_text.setFocus()
        self.cue_text.selectAll()

    # -- Audio --------------------------------------------------------------
    def _audio_tab(self) -> QWidget:
        w, f = _form()
        b = self.b
        self.opt_btn = button("Optimiser l'audio", "primary",
                              tooltip="Analyse les extraits et règle débruitage, équilibre des voix, musique et crêtes",
                              on_click=self.optimize_requested.emit)
        f.addRow(self.opt_btn)
        f.addRow(label("Les traitements choisis apparaissent ci-dessous et restent modifiables (Ctrl+Z pour annuler).",
                       "faint", wrap=True))
        f.addRow(label("Voix", "h2"))
        self.voice_combo = QComboBox()
        self.voice_combo.currentIndexChanged.connect(self._voice_pick)
        f.addRow("Piste de voix", self.voice_combo)
        f.addRow("Voix d'origine", b.combo("voice.original_level_db",
                                           [(-60.0, "Remplacée par le doublage"),
                                            (-18.0, "Gardée en fond, très bas"),
                                            (-10.0, "Gardée en fond, audible")]))
        f.addRow("Fond du doublage", b.combo("voice.background",
                                             [("original", "Son d'origine (niveau ci-dessus)"),
                                              ("ambience", "Musique et ambiance d'origine, sans la voix")]))
        f.addRow("Gain du doublage", b.spin("voice.dub_gain_db", -20, 20, 0.5, 1, " dB"))
        self.voice_note = label("", "faint", wrap=True)
        f.addRow(self.voice_note)
        iso = b.check("voice.isolate", "Voix seule (sans musique ni ambiance)")
        iso.setToolTip("Après « Séparer voix et musique » : la voix seule reste sur l'extrait, la musique et "
                       "l'ambiance vont sur la piste Musique, dont vous réglez le volume.")
        f.addRow(iso)
        sp = QHBoxLayout()
        sp.addWidget(button("Séparer voix et musique", tooltip="Calcule, sur ce PC, une piste voix seule et une "
                            "piste musique et ambiance pour chaque média du montage",
                            on_click=self.separate_requested.emit))
        sp.addStretch(1)
        f.addRow(sp)
        self.stems_note = label("", "faint", wrap=True)
        f.addRow(self.stems_note)
        f.addRow(label("Voix et son des extraits", "h2"))
        f.addRow("Gain", b.spin("audio.gain_db", -20, 20, 0.5, 1, " dB"))
        f.addRow(b.check("audio.highpass", "Couper les graves parasites (< 80 Hz)"))
        dn = QHBoxLayout()
        dn.addWidget(b.check("audio.denoise", "Réduire le bruit de fond"))
        dn.addWidget(b.spin("audio.denoise_amount", 1, 40, 1, 0, " dB"))
        f.addRow(dn)
        f.addRow(b.check("audio.level_speakers", "Équilibrer les intervenants (nivellement dynamique)"))
        f.addRow(b.check("audio.compress", "Compression légère de la voix"))
        f.addRow(b.check("audio.presence", "Accentuer l'intelligibilité (2-4 kHz)"))
        f.addRow(b.check("audio.deess", "Atténuer les sifflantes (« s », « ch »)"))
        f.addRow(b.check("audio.normalize", "Normaliser le volume (EBU R128)"))
        f.addRow("Volume cible", b.combo("audio.target_lufs", LOUDNESS))
        f.addRow("Crête max.", b.spin("audio.true_peak", -6, -0.1, 0.1, 1, " dBTP"))
        fd = QHBoxLayout()
        fd.addWidget(b.spin("audio.fade_in", 0, 10, 0.1, 1, " s"))
        fd.addWidget(b.spin("audio.fade_out", 0, 10, 0.1, 1, " s"))
        f.addRow("Fondu début / fin", fd)
        f.addRow(label("Musique de fond", "h2"))
        self.music_combo = QComboBox()
        self.music_combo.currentIndexChanged.connect(self._music_pick)
        f.addRow("Musique", self.music_combo)
        f.addRow("Volume", b.spin("music.volume_db", -40, 6, 1, 0, " dB"))
        mf = QHBoxLayout()
        mf.addWidget(b.spin("music.fade_in", 0, 10, 0.1, 1, " s"))
        mf.addWidget(b.spin("music.fade_out", 0, 10, 0.1, 1, " s"))
        f.addRow("Fondu début / fin", mf)
        f.addRow("Démarre à", b.spin("music.start", 0, 1e5, 0.5, 1, " s"))
        f.addRow("Départ dans le fichier", b.spin("music.src_offset", 0, 1e5, 0.5, 1, " s"))
        f.addRow(b.check("music.loop", "Boucler si la musique est trop courte"))
        dk = QHBoxLayout()
        dk.addWidget(b.check("music.duck", "Baisser la musique quand on parle"))
        dk.addWidget(b.spin("music.duck_amount", 2, 20, 1, 0, " : 1"))
        f.addRow(dk)
        f.addRow(label("Contrôle", "h2"))
        f.addRow(button("Analyser le son", tooltip="Mesure le volume, les crêtes et les silences du mixage",
                        on_click=self.analyze_requested.emit))
        self.analysis = label("", "muted", wrap=True)
        self.analysis.setTextInteractionFlags(Qt.TextSelectableByMouse)
        f.addRow(self.analysis)
        f.addRow(label("Un limiteur de sécurité empêche toute saturation introduite par le logiciel.", "faint",
                       wrap=True))
        return w

    def _load_voice_choices(self) -> None:
        from .dialogs import LANGUAGES
        names = dict(LANGUAGES)
        ids = {sg.asset_id for sg in self.tl.segments}
        langs: dict[str, int] = {}
        for aid in ids:
            for lg in (self.app.dubs.languages(aid) if self.app.dubs else []):
                langs[lg] = langs.get(lg, 0) + 1
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        self.voice_combo.addItem("Voix d'origine", "")
        for lg in sorted(set(langs) | ({self.tl.voice.language} if self.tl.voice.mode == "dub" else set())):
            if not lg:
                continue
            partial = langs.get(lg, 0) < len(ids)
            self.voice_combo.addItem(f"Doublage : {names.get(lg, lg)}" + (" (incomplet)" if partial else ""), lg)
        cur = self.tl.voice.language if self.tl.voice.mode == "dub" else ""
        self.voice_combo.setCurrentIndex(max(0, self.voice_combo.findData(cur)))
        self.voice_combo.blockSignals(False)
        if self.voice_combo.count() == 1:
            self.voice_note.setText("Pour doubler : transcription → Plus ▾ → Traduire, puis « Doubler cette "
                                    "traduction ».")
        elif cur and langs.get(cur, 0) < len(ids):
            self.voice_note.setText("⚠ Certains médias du montage n'ont pas de doublage dans cette langue : "
                                    "l'export sera refusé tant qu'ils ne sont pas doublés.")
        else:
            self.voice_note.setText("Astuce : affichez les sous-titres dans la même langue (onglet Sous-titres).")
        stems = self.app.stems
        sep = [a for a in ids if stems and stems.has(a)]
        if not ids:
            self.stems_note.setText("")
        elif len(sep) == len(ids):
            self.stems_note.setText("✓ Voix et musique séparées pour tous les médias du montage.")
        else:
            self.stems_note.setText(f"Séparation : {len(sep)}/{len(ids)} média(s). Nécessaire pour isoler la voix "
                                    "ou garder l'ambiance sous un doublage.")

    def reload_voice(self) -> None:
        self._loading = True
        try:
            self._load_voice_choices()
        finally:
            self._loading = False

    def _voice_pick(self) -> None:
        if self._loading:
            return
        lg = self.voice_combo.currentData() or ""
        self.tl.voice.mode, self.tl.voice.language = ("dub", lg) if lg else ("original", "")
        self._loading = True
        try:
            self._load_voice_choices()
        finally:
            self._loading = False
        self.changed.emit("structure")

    def _load_music_choices(self) -> None:
        self.music_combo.blockSignals(True)
        self.music_combo.clear()
        self.music_combo.addItem("Aucune", None)
        for r in self.app.db.query("SELECT id, file_name FROM source_asset WHERE kind IN ('audio','video') "
                                   "AND audio_tracks > 0 AND missing = 0 ORDER BY kind, file_name COLLATE NOCASE"):
            self.music_combo.addItem(r["file_name"], r["id"])
        self.music_combo.setCurrentIndex(max(0, self.music_combo.findData(self.tl.music.asset_id)))
        self.music_combo.blockSignals(False)

    def _music_pick(self) -> None:
        if not self._loading:
            self.tl.music.asset_id = self.music_combo.currentData()
            self.changed.emit("structure")

    def show_analysis(self, rep: dict) -> None:
        lines = []
        if rep.get("lufs") is not None and rep["lufs"] != float("-inf"):
            lines.append(f"Volume intégré : {rep['lufs']:.1f} LUFS · crête vraie : {rep.get('true_peak', 0):.1f} dBTP")
        issues = rep.get("issues") or []
        lines += [f"⚠ {i}" for i in issues] or ["Aucun problème détecté."]
        self.analysis.setText("\n".join(lines))

    # -- Export -------------------------------------------------------------
    def _export_tab(self) -> QWidget:
        w, f = _form()
        b = self.b
        self.out_info = label("", "muted")
        f.addRow(self.out_info)
        f.addRow("Codec", b.combo("export.codec", [("h264", "H.264 (compatible partout)"),
                                                   ("h265", "H.265 (plus léger, si disponible)")]))
        f.addRow("Qualité", b.combo("export.crf", QUALITY))
        f.addRow("Vitesse", b.combo("export.preset", SPEED))
        f.addRow("Son", b.combo("export.audio_bitrate", [(128, "128 kb/s"), (192, "192 kb/s"), (256, "256 kb/s")]))
        f.addRow(b.check("export.burn_texts", "Incruster les textes"))
        dr = QHBoxLayout()
        self.dir_lbl = label("", "faint")
        dr.addWidget(self.dir_lbl, 1)
        dr.addWidget(button("Choisir…", on_click=self._pick_dir))
        f.addRow("Dossier", dr)
        self.pattern = QLineEdit()
        self.pattern.setToolTip("Variables : {project} {aspect} {date} {w} {h}")
        self.pattern.editingFinished.connect(self._pattern)
        f.addRow("Nom du fichier", self.pattern)
        self.export_btn = button("Exporter la vidéo", "primary", on_click=self.export_requested.emit)
        f.addRow(self.export_btn)
        f.addRow(label("Réglages du concept", "h2"))
        self.save_concept_btn = button("", on_click=self.save_concept_requested.emit)
        self.save_concept_btn.setToolTip("Format, cadrage, image, audio, export et style du premier texte")
        self.apply_concept_btn = button("", "ghost", on_click=self.apply_concept_requested.emit)
        f.addRow(self.save_concept_btn)
        f.addRow(self.apply_concept_btn)
        f.addRow(label("Exports de cette vidéo", "h2"))
        self.history = QListWidget()
        self.history.setMaximumHeight(140)
        self.history.itemDoubleClicked.connect(self._open_export)
        f.addRow(self.history)
        return w

    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Dossier d'export", self.tl.export.directory or str(Path.home()))
        if d:
            self.tl.export.directory = d
            self.refresh()
            self.changed.emit("export")

    def _pattern(self) -> None:
        txt = self.pattern.text().strip() or "{project}_{aspect}_{date}"
        try:
            txt.format(project="p", aspect="a", date="d", w=1, h=1)
        except (KeyError, IndexError, ValueError):
            self.pattern.setText(self.tl.export.file_pattern)
            return
        self.tl.export.file_pattern = txt
        self.changed.emit("export")

    def _load_history(self) -> None:
        self.pattern.setText(self.tl.export.file_pattern)
        self.history.clear()
        if not self.project_id:
            return
        for r in self.app.montage.exports(self.project_id, 20):
            rep = json.loads(r["settings_json"] or "{}").get("report", {})
            exists = Path(r["output_path"]).exists()
            txt = f"{Path(r['output_path']).name}  ·  {r['aspect']}"
            if rep.get("lufs") is not None:
                txt += f"  ·  {rep['lufs']:.1f} LUFS"
            if not exists:
                txt += "  (fichier déplacé ou supprimé)"
            it = QListWidgetItem(txt)
            it.setData(Qt.UserRole, r["output_path"])
            it.setToolTip(r["output_path"])
            self.history.addItem(it)

    def _open_export(self, it) -> None:
        from .pages.library import reveal
        reveal(it.data(Qt.UserRole))
