"""Inspecteur du montage : extrait, cadrage, image, textes, audio, export."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog, QFontComboBox,
                               QFormLayout, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QScrollArea, QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from ..core import AppContext
from ..edit.model import ASPECTS, Timeline
from ..media.ffmpeg import fmt_timecode
from .widgets import button, label

FIT_MODES = [("fill", "Remplir (recadré)"), ("blur", "Image entière + fond flou"), ("fit", "Image entière + bandes noires")]
POSITIONS = [("top", "En haut"), ("center", "Au centre"), ("bottom", "En bas"), ("custom", "Libre (X / Y)")]
LOUDNESS = [(-14.0, "Réseaux sociaux (-14 LUFS)"), (-16.0, "Podcast / voix (-16 LUFS)"),
            (-23.0, "Diffusion TV (-23 LUFS)"), (-11.0, "Très fort (-11 LUFS)")]
QUALITY = [(18, "Très haute (fichier lourd)"), (20, "Haute (recommandé)"), (23, "Standard"), (27, "Légère")]
SPEED = [("veryfast", "Rapide"), ("medium", "Équilibré"), ("slow", "Lent (meilleure compression)")]


def _get(obj, path: str):
    for p in path.split("."):
        obj = getattr(obj, p)
    return obj


def _set(obj, path: str, value) -> None:
    *head, last = path.split(".")
    for p in head:
        obj = getattr(obj, p)
    setattr(obj, last, value)


class ColorButton(QPushButton):
    picked = Signal(str)

    def __init__(self):
        super().__init__()
        self.setFixedSize(40, 24)
        self.value = "#FFFFFF"
        self.clicked.connect(self._pick)

    def set(self, v: str) -> None:
        self.value = v
        self.setStyleSheet(f"background:{v}; border:1px solid rgba(128,128,128,.6); border-radius:5px;")

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self.value), self, "Couleur")
        if c.isValid():
            self.set(c.name())
            self.picked.emit(c.name())


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

    def spin(self, path, lo, hi, step=0.1, decimals=1, suffix="") -> QDoubleSpinBox:
        w = QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setSingleStep(step)
        w.setDecimals(decimals)
        w.setSuffix(suffix)
        w.setKeyboardTracking(False)
        w.valueChanged.connect(lambda v: self._commit(path, round(v, decimals)))
        self.items.append(("spin", path, w))
        return w

    def ispin(self, path, lo, hi, suffix="") -> QSpinBox:
        w = QSpinBox()
        w.setRange(lo, hi)
        w.setSuffix(suffix)
        w.setKeyboardTracking(False)
        w.valueChanged.connect(lambda v: self._commit(path, int(v)))
        self.items.append(("spin", path, w))
        return w

    def slider(self, path, lo, hi, scale=100.0) -> QSlider:
        w = QSlider(Qt.Horizontal)
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

    def font(self, path) -> QFontComboBox:
        w = QFontComboBox()
        w.currentFontChanged.connect(lambda f: self._commit(path, f.family()))
        self.items.append(("font", path, w))
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
                w.setCurrentFont(QFont(v))
            w.blockSignals(False)


def _scroll(inner: QWidget) -> QScrollArea:
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
        self.setMinimumWidth(390)
        self.setUsesScrollButtons(False)
        self.setDocumentMode(True)
        self.b = Binder(self)
        self.bt = Binder(self, "text")
        self.addTab(_scroll(self._segment_tab()), "Extrait")
        self.addTab(_scroll(self._frame_tab()), "Cadrage")
        self.addTab(_scroll(self._texts_tab()), "Textes")
        self.addTab(_scroll(self._subs_tab()), "Sous-titres")
        self.addTab(_scroll(self._audio_tab()), "Audio")
        self.addTab(_scroll(self._export_tab()), "Export")

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
        self.seg_in = QDoubleSpinBox()
        self.seg_out = QDoubleSpinBox()
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
        self.seg_vol = QDoubleSpinBox()
        self.seg_vol.setRange(-40, 20)
        self.seg_vol.setSuffix(" dB")
        self.seg_vol.setKeyboardTracking(False)
        self.seg_vol.valueChanged.connect(lambda v: self._seg_set("volume_db", round(v, 1)))
        f.addRow("Volume de l'extrait", self.seg_vol)
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

    def _seg(self):
        try:
            return self.tl.segments[self.tl.index_of(self.sel_segment)] if self.sel_segment else None
        except KeyError:
            return None

    def _load_segment(self) -> None:
        s = self._seg()
        for wdg in (self.seg_in, self.seg_out, self.seg_vol, self.seg_mute, self.seg_left, self.seg_right,
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
            n.volume_db, n.muted = s.volume_db, s.muted
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
        f.addRow(label("Cadrage", "h2"))
        f.addRow("Zoom", b.slider("video.zoom", 1.0, 3.0))
        f.addRow("Position horizontale", b.slider("video.offset_x", -1.0, 1.0))
        f.addRow("Position verticale", b.slider("video.offset_y", -1.0, 1.0))
        f.addRow("Rotation", b.combo("video.rotation", [(0, "Aucune"), (90, "90° horaire"), (180, "180°"),
                                                        (270, "90° anti-horaire")]))
        f.addRow(b.check("video.mirror", "Effet miroir (horizontal)"))
        f.addRow(b.check("video.flip", "Retourner verticalement"))
        f.addRow(button("Recentrer", on_click=self._recenter))
        f.addRow(label("Image", "h2"))
        f.addRow("Luminosité", b.slider("video.brightness", -0.5, 0.5))
        f.addRow("Contraste", b.slider("video.contrast", 0.5, 1.8))
        f.addRow("Saturation", b.slider("video.saturation", 0.0, 2.0))
        f.addRow("Température", b.ispin("video.temperature", 2500, 10000, " K"))
        f.addRow("Netteté", b.slider("video.sharpen", 0.0, 1.0))
        f.addRow(button("Réinitialiser l'image", on_click=self._reset_look))
        f.addRow(label("Le miroir ou un filtre ne rendent pas un contenu « original » : vérifiez vos droits "
                       "sur les sources.", "faint", wrap=True))
        return w

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
        for pair in ((("title", "+ Titre"), ("hook", "+ Accroche")), (("cta", "+ Call to action"), ("lower", "+ Nom"))):
            row = QHBoxLayout()
            for kind, name in pair:
                row.addWidget(button(name, on_click=lambda _=False, k=kind: self._add_text(k)))
            lay.addLayout(row)
        fw, f = _form()
        bt = self.bt
        self.text_edit = QPlainTextEdit()
        self.text_edit.setMaximumHeight(70)
        self.text_edit.textChanged.connect(self._text_changed)
        f.addRow("Texte", self.text_edit)
        f.addRow("Début", bt.spin("start", 0, 1e5, 0.1, 2, " s"))
        f.addRow("Fin", bt.spin("end", 0, 1e5, 0.1, 2, " s"))
        tr = QHBoxLayout()
        tr.addWidget(button("Début ici", tooltip="Début = tête de lecture", on_click=lambda: self._text_time("start")))
        tr.addWidget(button("Fin ici", tooltip="Fin = tête de lecture", on_click=lambda: self._text_time("end")))
        tr.addWidget(button("Tout", tooltip="Pendant toute la vidéo", on_click=lambda: self._text_time("all")))
        f.addRow(tr)
        f.addRow("Position", bt.combo("position", POSITIONS))
        f.addRow("X (libre)", bt.slider("x", 0.0, 1.0))
        f.addRow("Y (libre)", bt.slider("y", 0.0, 1.0))
        f.addRow("Police", bt.font("font"))
        f.addRow("Taille", bt.ispin("size", 16, 240, " px"))
        st = QHBoxLayout()
        st.addWidget(bt.check("bold", "Gras"))
        st.addWidget(bt.check("italic", "Italique"))
        st.addWidget(bt.combo("align", [("center", "Centré"), ("left", "Gauche"), ("right", "Droite")]))
        f.addRow("Style", st)
        cl = QHBoxLayout()
        cl.addWidget(bt.color("color"))
        for name, col in (("Blanc", "#FFFFFF"), ("Jaune", "#FFD54A"), ("Noir", "#000000")):
            cl.addWidget(button(name, "ghost", on_click=lambda _=False, c=col: self._text_color(c)))
        cl.addStretch(1)
        f.addRow("Couleur", cl)
        ol = QHBoxLayout()
        ol.addWidget(bt.color("outline_color"))
        ol.addWidget(bt.ispin("outline", 0, 30, " px"))
        f.addRow("Contour", ol)
        bx = QHBoxLayout()
        bx.addWidget(bt.check("box", "Rectangle"))
        bx.addWidget(bt.color("box_color"))
        bx.addWidget(bt.slider("box_opacity", 0.0, 1.0))
        f.addRow("Fond", bx)
        f.addRow("Largeur max.", bt.slider("max_width", 0.3, 1.0))
        f.addRow("Fondu", bt.spin("fade", 0, 3, 0.1, 1, " s"))
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

    def _text_pick(self, it, _prev=None) -> None:
        if it and not self._loading:
            self.sel_text = it.data(Qt.UserRole)
            self._loading = True
            self._load_texts()
            self._loading = False
            self.changed.emit("text-select")

    def _add_text(self, kind: str) -> None:
        style = self.app.montage.text_style_for(self.project_id) if self.project_id else {}
        t0 = self.playhead
        presets = {
            "title": dict(text="Titre", position="top", size=80, box=False),
            "hook": dict(text="Accroche", position="center", size=96, box=True, box_color="#000000"),
            "cta": dict(text="Abonnez-vous !", position="bottom", size=70, color="#FFD54A"),
            "lower": dict(text="Nom de l'intervenant", position="custom", x=0.5, y=0.7, size=56, box=True),
        }[kind]
        params = {**style, **presets} if kind != "title" else {**presets, **style, "text": "Titre"}
        text = params.pop("text")
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

    def _text_color(self, c: str) -> None:
        t = self.current_text()
        if t:
            t.color = c
            self.refresh()
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
        f.addRow("Police", b.font("subtitles.style.font"))
        f.addRow("Taille", b.ispin("subtitles.style.size", 20, 200, " px"))
        st = QHBoxLayout()
        st.addWidget(b.check("subtitles.style.bold", "Gras"))
        st.addWidget(b.check("subtitles.style.italic", "Italique"))
        st.addWidget(b.check("subtitles.style.uppercase", "MAJUSCULES"))
        f.addRow("Style", st)
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
        tm = QHBoxLayout()
        self.cue_start = QDoubleSpinBox()
        self.cue_end = QDoubleSpinBox()
        for sp in (self.cue_start, self.cue_end):
            sp.setDecimals(2)
            sp.setRange(0, 1e6)
            sp.setSingleStep(0.05)
            sp.setSuffix(" s")
            sp.setKeyboardTracking(False)
            sp.valueChanged.connect(self._cue_times)
            tm.addWidget(sp)
        f.addRow("Début / fin", tm)
        cb = QHBoxLayout()
        cb.addWidget(button("Début ici", on_click=lambda: self._cue_here("start")))
        cb.addWidget(button("Fin ici", on_click=lambda: self._cue_here("end")))
        cb.addWidget(button("Supprimer", "danger", on_click=self._cue_delete))
        f.addRow(cb)
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

    def _load_cues(self) -> None:
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
        self.cue_start.setValue(c.start if c else 0)
        self.cue_end.setValue(c.end if c else 0)
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

    def _cue_here(self, which: str) -> None:
        c = self._cue()
        if not c:
            return
        if which == "start" and self.playhead < c.end - 0.1:
            self.cue_start.setValue(self.playhead)
        elif which == "end" and self.playhead > c.start + 0.1:
            self.cue_end.setValue(self.playhead)

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
        f.addRow(label("Exports de ce projet", "h2"))
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
