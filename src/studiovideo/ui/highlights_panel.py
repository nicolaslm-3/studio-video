"""Panneau « Extraits » : clipping intelligent (§4 mode 1, §7)."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QGridLayout, QHBoxLayout,
                               QHeaderView, QLineEdit, QMessageBox, QSplitter, QTableWidget,
                               QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from .scrub import double_spin, int_spin
from ..ai.highlights import Request, context_flags
from ..ai.lexicon import CATEGORIES
from ..core import AppContext
from ..media.ffmpeg import fmt_timecode
from .widgets import button, label

STATE_LABELS = {"proposed": "", "accepted": "✓", "rejected": "✕"}


class HighlightsPanel(QWidget):
    play_range = Signal(float, float)          # temps source
    clips_created = Signal()
    add_to_timeline = Signal(list)             # [(début, fin), …]
    open_settings = Signal()

    def __init__(self, app: AppContext, parent=None):
        super().__init__(parent)
        self.app = app
        self.project_id: int | None = None
        self.asset_id: int | None = None
        self.job_id: int | None = None
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(6)
        self.status = label("", "muted", wrap=True)
        lay.addWidget(self.status)

        # -- critères (repliables)
        self.toggle = QToolButton()
        self.toggle.setText("▾ Critères")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.setStyleSheet("QToolButton{border:none; font-weight:600;}")
        lay.addWidget(self.toggle)
        self.form = QWidget()
        g = QGridLayout(self.form)
        g.setContentsMargins(0, 0, 0, 0)
        g.setVerticalSpacing(4)
        self.cats: dict[str, QCheckBox] = {}
        for i, (k, name) in enumerate(CATEGORIES.items()):
            cb = QCheckBox(name)
            self.cats[k] = cb
            g.addWidget(cb, i // 2, i % 2)
        r = len(CATEGORIES) // 2 + 1
        self.query = QLineEdit()
        self.query.setPlaceholderText("Mots-clés ou demande (ex. : désaccords sur les retraites)")
        g.addWidget(self.query, r, 0, 1, 2)
        row = QHBoxLayout()
        self.count = int_spin()
        self.count.setRange(1, 50)
        self.count.setValue(10)
        self.count.setSuffix(" extraits")
        self.dmin, self.dmax, self.dtarget = double_spin(), double_spin(), double_spin()
        for sp, v, tip in ((self.dmin, 20, "Durée minimale"), (self.dmax, 60, "Durée maximale"),
                           (self.dtarget, 40, "Durée cible")):
            sp.setRange(3, 600)
            sp.setDecimals(0)
            sp.setSuffix(" s")
            sp.setValue(v)
            sp.setToolTip(tip)
        row.addWidget(self.count)
        row.addWidget(label("de", "faint"))
        row.addWidget(self.dmin)
        row.addWidget(label("à", "faint"))
        row.addWidget(self.dmax)
        g.addLayout(row, r + 1, 0, 1, 2)
        row2 = QHBoxLayout()
        row2.addWidget(label("Cible", "faint"))
        row2.addWidget(self.dtarget)
        self.intensity = QComboBox()
        for v, n in ((1, "Calme"), (2, "Normal"), (3, "Intense")):
            self.intensity.addItem(n, v)
        self.intensity.setCurrentIndex(1)
        self.intensity.setToolTip("Niveau d'intensité recherché (énergie de la voix)")
        row2.addWidget(self.intensity)
        g.addLayout(row2, r + 2, 0, 1, 2)
        self.speakers = QLineEdit()
        self.speakers.setPlaceholderText("Intervenants à privilégier (séparés par des virgules)")
        g.addWidget(self.speakers, r + 3, 0, 1, 2)
        self.engine = QComboBox()
        g.addWidget(self.engine, r + 4, 0, 1, 2)
        lay.addWidget(self.form)
        self.toggle.toggled.connect(lambda on: (self.form.setVisible(on),
                                                self.toggle.setText(("▾" if on else "▸") + " Critères")))
        self.go = button("Trouver les extraits", "primary", on_click=self._find)
        lay.addWidget(self.go)

        split = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["", "★", "Début", "Durée", "Score", "Sujet", "⚠"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        for c in (0, 1, 2, 3, 4, 6):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._show)
        self.table.cellDoubleClicked.connect(lambda *_: self._play())
        split.addWidget(self.table)
        det = QWidget()
        dl = QVBoxLayout(det)
        dl.setContentsMargins(0, 4, 0, 0)
        self.detail = label("", wrap=True)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        dl.addWidget(self.detail)
        tr = QHBoxLayout()
        self.k_start, self.k_end = double_spin(), double_spin()
        for sp in (self.k_start, self.k_end):
            sp.setRange(0, 1e6)
            sp.setDecimals(1)
            sp.setSingleStep(0.5)
            sp.setSuffix(" s")
            sp.setKeyboardTracking(False)
            sp.valueChanged.connect(self._times)
            tr.addWidget(sp)
        dl.addLayout(tr)
        ab = QHBoxLayout()
        ab.addWidget(button("▶ Lire", on_click=self._play))
        ab.addWidget(button("Accepter", on_click=lambda: self._state("accepted")))
        ab.addWidget(button("Rejeter", "ghost", on_click=lambda: self._state("rejected")))
        ab.addWidget(button("★", "ghost", tooltip="Favori", on_click=self._fav))
        dl.addLayout(ab)
        split.addWidget(det)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        self.table.setMinimumHeight(170)
        lay.addWidget(split, 1)
        bot = QHBoxLayout()
        bot.addWidget(button("Créer les clips acceptés", on_click=self._make_clips))
        bot.addWidget(button("→ Timeline", tooltip="Ajoute les extraits acceptés à la timeline",
                             on_click=self._to_timeline))
        lay.addLayout(bot)
        lay.addWidget(label("Le score est une estimation pour classer les propositions, pas une prédiction de vues.",
                            "faint", wrap=True))
        self.refresh_engines()

    # -- état -------------------------------------------------------------
    def refresh_engines(self) -> None:
        self.engine.clear()
        self.engine.addItem("Analyse locale (hors ligne)", {"kind": "local"})
        cfg = self.app.settings.get("llm", None) or {}
        if cfg.get("kind") in ("ollama", "openai") and cfg.get("model"):
            self.engine.addItem(f"Modèle de langage : {cfg['model']}", cfg)
            if self.app.settings.get("highlights_engine", "local") == "llm":
                self.engine.setCurrentIndex(1)
        else:
            self.engine.addItem("Modèle de langage… (à configurer)", {"kind": "setup"})

    def set_context(self, project_id: int | None, asset_id: int | None) -> None:
        self.project_id, self.asset_id = project_id, asset_id
        self.refresh()

    def refresh(self) -> None:
        a = self.app.library.get(self.asset_id) if self.asset_id else None
        tr = self.app.transcripts.get(self.asset_id) if self.asset_id else None
        if self.job_id is not None:
            pass
        elif not a:
            self.status.setText("Sélectionnez un média (vidéo longue : podcast, débat, interview…).")
        elif tr is None:
            self.status.setText("Ce média n'est pas transcrit : l'analyse s'appuie sur la transcription "
                                "(onglet « Transcription »).")
        else:
            self.status.setText(f"{a['file_name']} · {fmt_timecode(a['duration_s'])} · {len(tr.segments)} phrases")
            spk = tr.speakers()
            if spk:
                self.speakers.setToolTip("Intervenants : " + ", ".join(spk))
        self.go.setEnabled(bool(a and tr) or self.job_id is not None)
        self.go.setText("Annuler l'analyse" if self.job_id is not None else "Trouver les extraits")
        self._fill()

    def _fill(self) -> None:
        sel = self._current_id()
        self._loading = True
        self.table.setRowCount(0)
        rows = self.app.candidates.list(self.project_id, self.asset_id) if (self.project_id and self.asset_id) else []
        for i, r in enumerate(rows):
            self.table.insertRow(i)
            st = QTableWidgetItem(STATE_LABELS[r["state"]])
            st.setData(Qt.UserRole, r["id"])
            self.table.setItem(i, 0, st)
            self.table.setItem(i, 1, QTableWidgetItem("★" if r["favorite"] else ""))
            self.table.setItem(i, 2, QTableWidgetItem(fmt_timecode(r["start_s"])))
            self.table.setItem(i, 3, QTableWidgetItem(f"{r['end_s'] - r['start_s']:.0f} s"))
            self.table.setItem(i, 4, QTableWidgetItem(f"{r['score']:.0f}" if r["score"] is not None else ""))
            subj = QTableWidgetItem(r["subject"] or CATEGORIES.get(r["category"], r["category"]))
            subj.setToolTip(f"{CATEGORIES.get(r['category'], r['category'])}\n{r['hook']}")
            self.table.setItem(i, 5, subj)
            flags = json.loads(r["context_flags"] or "[]")
            fl = QTableWidgetItem(str(len(flags)) if flags else "")
            fl.setToolTip("\n".join(flags))
            self.table.setItem(i, 6, fl)
            if r["state"] == "rejected":
                for c in range(7):
                    it = self.table.item(i, c)
                    if it:
                        it.setForeground(QColor(128, 128, 128))
            if r["id"] == sel:
                self.table.selectRow(i)
        self._loading = False
        if not rows:
            self.detail.setText("Aucune proposition. Choisissez vos critères puis « Trouver les extraits »."
                                if self.asset_id else "")
        self._show()

    def _current_id(self) -> int | None:
        r = self.table.currentRow()
        it = self.table.item(r, 0) if r >= 0 else None
        return it.data(Qt.UserRole) if it else None

    def _show(self) -> None:
        if self._loading:
            return
        cid = self._current_id()
        k = self.app.candidates.get(cid) if cid else None
        for w in (self.k_start, self.k_end):
            w.setEnabled(k is not None)
        if not k:
            return
        flags = json.loads(k["context_flags"] or "[]")
        html = (f"<b>{CATEGORIES.get(k['category'], k['category'])}</b> · score {k['score']:.0f} "
                f"<span style='opacity:.6'>(estimation)</span><br>"
                f"{fmt_timecode(k['start_s'], True)} → {fmt_timecode(k['end_s'], True)} "
                f"({k['end_s'] - k['start_s']:.1f} s)" + (f" · {k['speakers']}" if k["speakers"] else "") +
                f"<br><br><b>Accroche proposée :</b> {k['hook']}<br><b>Pourquoi :</b> {k['rationale']}")
        if flags:
            html += "<br><br><b style='color:#D07A72'>À vérifier :</b><br>" + "<br>".join(f"⚠ {f}" for f in flags)
        self.detail.setText(html)
        for sp, v in ((self.k_start, k["start_s"]), (self.k_end, k["end_s"])):
            sp.blockSignals(True)
            sp.setValue(v)
            sp.blockSignals(False)

    # -- actions ------------------------------------------------------------
    def _request(self) -> Request:
        q = self.query.text().strip()
        parts = [p.strip() for p in q.split(",")] if "," in q else []
        return Request(count=self.count.value(), min_duration=self.dmin.value(),
                       max_duration=max(self.dmax.value(), self.dmin.value() + 1),
                       target_duration=min(max(self.dtarget.value(), self.dmin.value()), self.dmax.value()),
                       categories=[k for k, cb in self.cats.items() if cb.isChecked()],
                       keywords=parts, query="" if parts else q,
                       speakers_include=[s.strip() for s in self.speakers.text().split(",") if s.strip()],
                       intensity=self.intensity.currentData())

    def _find(self) -> None:
        if self.job_id is not None:
            self.app.jobs.cancel(self.job_id)
            return
        eng = self.engine.currentData()
        if eng.get("kind") == "setup":
            self.open_settings.emit()
            return
        self.app.settings.set("highlights_engine", "llm" if eng.get("kind") != "local" else "local")
        a = self.app.library.get(self.asset_id)
        self.job_id = self.app.jobs.submit("find_highlights", f"Extraits · {a['file_name']}",
                                           {"asset_id": self.asset_id, "project_id": self.project_id,
                                            "request": self._request().__dict__, "engine": eng})
        self.status.setText("Analyse en cours…")
        self.refresh()

    def on_job(self, job) -> None:
        if job.id != self.job_id:
            return
        if job.status == "running":
            self.status.setText(f"{job.message or 'Analyse'} — {int(job.progress * 100)} %")
            return
        if job.status in ("succeeded", "failed", "cancelled"):
            self.job_id = None
            if job.status == "failed":
                QMessageBox.warning(self, "Extraits", job.error[:800])
            elif job.status == "succeeded" and not job.result.get("count"):
                QMessageBox.information(self, "Extraits", "Aucun passage ne correspond à ces critères. "
                                        "Élargissez les durées ou retirez des filtres.")
            self.refresh()

    def _play(self) -> None:
        cid = self._current_id()
        k = self.app.candidates.get(cid) if cid else None
        if k:
            self.play_range.emit(k["start_s"], k["end_s"])

    def _state(self, st: str) -> None:
        for cid in self._selected_ids():
            self.app.candidates.set_state(cid, st)
        self._fill()

    def _fav(self) -> None:
        cid = self._current_id()
        if cid:
            self.app.candidates.toggle_favorite(cid)
            self._fill()

    def _times(self) -> None:
        cid = self._current_id()
        if not cid or self._loading:
            return
        a, b = self.k_start.value(), self.k_end.value()
        tr = self.app.transcripts.get(self.asset_id)
        flags = []
        if tr and tr.segments:
            first = max((i for i, s in enumerate(tr.segments) if s.start <= a + 0.01), default=0)
            last = min((i for i, s in enumerate(tr.segments) if s.end >= b - 0.01), default=len(tr.segments) - 1)
            flags = context_flags(tr, first, max(first, last), a, b)
        try:
            self.app.candidates.set_times(cid, a, b, flags)
        except ValueError as exc:
            QMessageBox.warning(self, "Extrait", str(exc))
        self._fill()

    def _selected_ids(self) -> list[int]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(r, 0).data(Qt.UserRole) for r in rows if self.table.item(r, 0)]

    def _accepted(self) -> list:
        return [r for r in self.app.candidates.list(self.project_id, self.asset_id, ("accepted",))]

    def _make_clips(self) -> None:
        rows = self._accepted()
        if not rows:
            QMessageBox.information(self, "Clips", "Acceptez d'abord une ou plusieurs propositions.")
            return
        self.app.candidates.to_clips([r["id"] for r in rows])
        self.clips_created.emit()
        self.status.setText(f"{len(rows)} clip(s) créé(s) dans la liste « Clips » (onglet Médias).")

    def _to_timeline(self) -> None:
        rows = sorted(self._accepted(), key=lambda r: r["start_s"])
        if not rows:
            QMessageBox.information(self, "Timeline", "Acceptez d'abord une ou plusieurs propositions.")
            return
        self.add_to_timeline.emit([(r["start_s"], r["end_s"]) for r in rows])
