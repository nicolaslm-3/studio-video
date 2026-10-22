"""Brief de campagne et rapport de conformité (§4 mode 5)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLineEdit, QPlainTextEdit,
                               QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..core import AppContext
from ..edit import campaign as cp
from ..edit.model import ASPECTS
from ..publish.rules import RULES
from .widgets import button, label


class BriefDialog(QDialog):
    def __init__(self, app: AppContext, project_id: int, parent=None):
        super().__init__(parent)
        self.app, self.pid = app, project_id
        b = app.campaigns.brief(project_id)
        self.setWindowTitle("Brief de campagne")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        lay.addWidget(label("Les exigences du client. Chaque vidéo exportée et chaque texte de publication sont "
                            "contrôlés automatiquement d'après ce brief.", "muted", wrap=True))
        f = QFormLayout()
        self.name = QLineEdit(b.name)
        self.name.setPlaceholderText("Nom de la campagne (obligatoire)")
        self.client = QLineEdit(b.client)
        self.deadline = QDateEdit()
        self.deadline.setCalendarPopup(True)
        self.deadline.setDisplayFormat("dd/MM/yyyy")
        self.deadline.setDate(QDate.fromString(b.deadline, "yyyy-MM-dd") if b.deadline else QDate.currentDate()
                              .addDays(14))
        f.addRow("Campagne", self.name)
        f.addRow("Client / marque", self.client)
        f.addRow("Date limite", self.deadline)
        pr = QHBoxLayout()
        self.platforms = {}
        for k, r in RULES.items():
            cb = QCheckBox(r.label)
            cb.setChecked(k in b.platforms)
            self.platforms[k] = cb
            pr.addWidget(cb)
        f.addRow("Plateformes", pr)
        ar = QHBoxLayout()
        self.aspects = {}
        for a in ASPECTS:
            cb = QCheckBox(a)
            cb.setChecked(a in b.aspects)
            self.aspects[a] = cb
            ar.addWidget(cb)
        f.addRow("Formats acceptés", ar)
        dr = QHBoxLayout()
        self.min_s, self.max_s = QDoubleSpinBox(), QDoubleSpinBox()
        for w, v in ((self.min_s, b.min_s), (self.max_s, b.max_s)):
            w.setRange(0, 3600)
            w.setSuffix(" s")
            w.setValue(v)
            dr.addWidget(w)
        dr.addWidget(label("(0 = sans limite)", "faint"))
        f.addRow("Durée min / max", dr)
        self.count = QSpinBox()
        self.count.setRange(0, 1000)
        self.count.setValue(b.target_count)
        f.addRow("Vidéos attendues", self.count)
        self.subs = QCheckBox("Sous-titres incrustés obligatoires")
        self.subs.setChecked(b.require_subtitles)
        f.addRow("", self.subs)
        self.req = QLineEdit(", ".join(b.required_words))
        self.req.setPlaceholderText("Marque, produit… (prononcé ou affiché)")
        self.forb = QLineEdit(", ".join(b.forbidden_words))
        self.forb.setPlaceholderText("Concurrents, termes interdits…")
        self.tags = QLineEdit(" ".join("#" + t.lstrip("#") for t in b.required_hashtags))
        self.ment = QLineEdit(" ".join("@" + m.lstrip("@") for m in b.required_mentions))
        self.disc = QLineEdit(b.disclosure)
        self.disc.setPlaceholderText("#pub, #partenariat, « Collaboration commerciale »…")
        f.addRow("Mots requis", self.req)
        f.addRow("Mots interdits", self.forb)
        f.addRow("Hashtags obligatoires", self.tags)
        f.addRow("Mentions obligatoires", self.ment)
        f.addRow("Mention de partenariat", self.disc)
        self.notes = QPlainTextEdit(b.notes)
        self.notes.setMaximumHeight(80)
        f.addRow("Notes", self.notes)
        lay.addLayout(f)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Enregistrer le brief")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def brief(self) -> cp.Brief:
        def words(raw):
            return [w.lstrip("#@") for w in cp.split_list(raw.replace(" #", ",").replace(" @", ","))]
        return cp.Brief(name=self.name.text().strip(), client=self.client.text().strip(),
                        deadline=self.deadline.date().toString("yyyy-MM-dd"),
                        platforms=[k for k, cb in self.platforms.items() if cb.isChecked()],
                        aspects=[k for k, cb in self.aspects.items() if cb.isChecked()],
                        min_s=self.min_s.value(), max_s=self.max_s.value(), target_count=self.count.value(),
                        require_subtitles=self.subs.isChecked(), required_words=cp.split_list(self.req.text()),
                        forbidden_words=cp.split_list(self.forb.text()), required_hashtags=words(self.tags.text()),
                        required_mentions=words(self.ment.text()), disclosure=self.disc.text().strip(),
                        notes=self.notes.toPlainText().strip())

    def _save(self) -> None:
        b = self.brief()
        if not b.name:
            self.name.setFocus()
            self.name.setPlaceholderText("Indiquez le nom de la campagne")
            return
        self.app.campaigns.save_brief(self.pid, b)
        self.accept()


class ComplianceDialog(QDialog):
    def __init__(self, app: AppContext, project_id: int, parent=None):
        super().__init__(parent)
        self.app, self.pid = app, project_id
        self.b = app.campaigns.brief(project_id)
        self.rows = app.campaigns.report(project_id)
        self.setWindowTitle(f"Conformité — {self.b.name or 'campagne'}")
        self.resize(900, 560)
        lay = QVBoxLayout(self)
        n_ok = sum(r["summary"].startswith("✓") for r in self.rows)
        head = f"{n_ok}/{len(self.rows)} vidéo(s) conforme(s)"
        if self.b.target_count:
            head += f" · objectif : {self.b.target_count}"
        if self.b.deadline:
            head += f" · date limite : {self.b.deadline[8:10]}/{self.b.deadline[5:7]}/{self.b.deadline[:4]}"
        lay.addWidget(label(head, "h2"))
        lay.addWidget(label("« ? » : non vérifiable en l'état (ex. vidéo non transcrite). Les textes de publication "
                            "(hashtags, mentions, partenariat) sont contrôlés dans chaque publication.", "faint",
                            wrap=True))
        t = QTableWidget(0, 3)
        t.setHorizontalHeaderLabels(["Vidéo", "Résultat", "Détail"])
        t.verticalHeader().hide()
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        rows = []
        for r in self.rows:
            bad = [c for c in r["checks"] if c.ok is not True]
            detail = " · ".join(f"{'⚠' if c.ok is False else '?'} {c.rule} : {c.detail}".rstrip(" :") for c in bad)
            rows.append((Path(r["path"]).name, r["summary"], detail or "Toutes les règles vérifiées"))
        t.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, v in enumerate(row):
                it = QTableWidgetItem(v)
                it.setToolTip(v)
                t.setItem(i, j, it)
        t.resizeColumnToContents(0)
        t.resizeColumnToContents(1)
        self.table = t
        lay.addWidget(t, 1)
        if not rows:
            lay.addWidget(label("Aucune vidéo exportée dans ce projet pour l'instant.", "muted"))
        bb = QHBoxLayout()
        bb.addWidget(button("Enregistrer le rapport…", on_click=self._save))
        bb.addStretch(1)
        bb.addWidget(button("Fermer", on_click=self.accept))
        lay.addLayout(bb)
        self.rows_text = rows

    def text_report(self) -> str:
        b = self.b
        lines = [f"Rapport de conformité — {b.name}", f"Client : {b.client}" if b.client else "",
                 f"Date limite : {b.deadline}" if b.deadline else "", ""]
        for name, summ, detail in self.rows_text:
            lines += [f"{name} : {summ}", f"   {detail}", ""]
        return "\n".join(x for x in lines if x is not None)

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Rapport", f"Conformité {self.b.name}.txt", "*.txt")
        if path:
            Path(path).write_text(self.text_report(), encoding="utf-8")
