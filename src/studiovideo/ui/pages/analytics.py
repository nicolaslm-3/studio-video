"""Analytiques (V4, §20) : comparer vidéos, comptes, concepts et plateformes.

Un indicateur non fourni s'affiche « — » (jamais 0). La source de chaque chiffre est visible :
« API » (relevé automatique) ou « saisi » (recopié à la main depuis la plateforme).
"""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QMessageBox, QStyledItemDelegate, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..scrub import int_spin
from ...core import AppContext
from ...publish.rules import RULES
from ...services.analytics import LABELS, METRICS
from ..widgets import Card, PageHeader, button, label, local_time

NA = "—"


def _fmt(v) -> str:
    if v is None:
        return NA
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f} M".replace(".", ",")
    if v >= 10_000:
        return f"{v / 1000:.0f} k"
    return f"{v:,}".replace(",", " ")


class BarDelegate(QStyledItemDelegate):
    """Barre proportionnelle derrière la valeur (comparaison visuelle)."""

    def paint(self, p: QPainter, opt, idx) -> None:
        frac = idx.data(Qt.UserRole + 1)
        if isinstance(frac, float) and frac > 0:
            p.save()
            r = opt.rect.adjusted(2, 6, -2, -6)
            p.fillRect(QRect(r.left(), r.top(), int(r.width() * min(1.0, frac)), r.height()),
                       QColor(idx.data(Qt.UserRole + 2) or "#5B8DEF").darker(170))
            p.restore()
        super().paint(p, opt, idx)


class RetentionChart(QWidget):
    """Courbe de rétention : part des spectateurs encore présents à chaque instant de la vidéo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.points: list = []
        self.title = ""

    def set_curve(self, points: list, title: str) -> None:
        self.points, self.title = points, title
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        fg = pal.color(pal.ColorRole.WindowText)
        muted = QColor(fg)
        muted.setAlpha(110)
        r = self.rect().adjusted(44, 34, -12, -22)
        p.setPen(fg)
        p.drawText(self.rect().adjusted(8, 2, -8, 0), Qt.AlignLeft | Qt.AlignTop,
                   f"Rétention — {self.title}" if self.title else "Rétention")
        if not self.points:
            p.setPen(muted)
            p.drawText(r, Qt.AlignCenter, "Sélectionnez une vidéo. La courbe vient de YouTube Analytics "
                       "(chaîne connectée, 2 à 3 jours après la publication) ; « — » ailleurs.")
            return
        top = max(1.0, max(b for _, b in self.points))
        p.setPen(muted)
        for frac, lbl in ((0.0, "0 %"), (0.5, f"{top * 50:.0f} %"), (1.0, f"{top * 100:.0f} %")):
            y = r.bottom() - frac * r.height()
            p.drawLine(r.left(), int(y), r.right(), int(y))
            p.drawText(2, int(y) + 4, lbl)
        for frac, lbl in ((0.0, "début"), (0.5, "milieu"), (1.0, "fin")):
            x = r.left() + frac * r.width()
            p.drawText(int(x) - 16, r.bottom() + 16, lbl)
        pen = p.pen()
        pen.setColor(QColor("#5B8DEF"))
        pen.setWidth(2)
        p.setPen(pen)
        pts = [(r.left() + a * r.width(), r.bottom() - (b / top) * r.height()) for a, b in self.points]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            p.drawLine(int(x0), int(y0), int(x1), int(y1))


class ManualStatsDialog(QDialog):
    def __init__(self, title: str, current: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Saisir les chiffres")
        lay = QVBoxLayout(self)
        lay.addWidget(label(f"« {title} » — recopiez les chiffres affichés par la plateforme. Décochez ce qui "
                            "n'est pas affiché : il restera « non fourni ».", "muted", wrap=True))
        f = QFormLayout()
        self.fields = {}
        for m in METRICS:
            row = QHBoxLayout()
            cb = QCheckBox("fourni")
            sp = int_spin()
            sp.setRange(0, 2_000_000_000)
            sp.setGroupSeparatorShown(True)
            cb.setChecked(m in current)
            sp.setValue(int(current.get(m, 0)))
            sp.setEnabled(cb.isChecked())
            cb.toggled.connect(sp.setEnabled)
            row.addWidget(sp, 1)
            row.addWidget(cb)
            f.addRow(LABELS[m], row)
            self.fields[m] = (cb, sp)
        lay.addLayout(f)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self) -> dict:
        return {m: sp.value() for m, (cb, sp) in self.fields.items() if cb.isChecked()}


class AnalyticsPage(QWidget):
    def __init__(self, app: AppContext, parent=None):
        super().__init__(parent)
        self.app = app
        self.job_id = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 16)
        hdr = PageHeader("Analytiques", "Comparer les vidéos, comptes, concepts et plateformes")
        self.concept = QComboBox()
        self.platform = QComboBox()
        self.platform.addItem("Toutes les plateformes", None)
        for k, r in RULES.items():
            self.platform.addItem(r.label, k)
        for w in (self.concept, self.platform):
            w.currentIndexChanged.connect(self.refresh)
            hdr.add_action(w)
        self.refresh_btn = button("Relever les statistiques (YouTube, TikTok)", "primary", on_click=self._refresh_stats)
        hdr.add_action(self.refresh_btn)
        lay.addWidget(hdr)
        self.status = label("", "muted", wrap=True)
        lay.addWidget(self.status)
        self.cards = QHBoxLayout()
        self.cards.setSpacing(12)
        lay.addLayout(self.cards)
        self.tabs = QTabWidget()
        self.groups: dict[str, QTableWidget] = {}
        for key, name in (("concept", "Par concept"), ("platform", "Par plateforme"), ("account", "Par compte")):
            t = self._table(["", "Publications", "Vues", "J'aime", "Commentaires", "Partages", "Engagement"])
            t.setItemDelegateForColumn(2, BarDelegate(t))
            t.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
            t.setColumnWidth(2, 260)
            self.groups[key] = t
            self.tabs.addTab(t, name)
        self.pubs = self._table(["Titre", "Concept", "Plateforme · compte", "Vues", "J'aime", "Commentaires",
                                 "Partages", "Durée moy. vue", "% moyen vu", "Source", "Relevé"])
        self.pubs.setItemDelegateForColumn(3, BarDelegate(self.pubs))
        self.pubs.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self.pubs.setColumnWidth(3, 200)
        hh = self.pubs.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        self.pubs.setColumnWidth(0, 240)
        self.pubs.setColumnWidth(2, 170)
        self.pubs.itemDoubleClicked.connect(self._open_link)
        vid = QWidget()
        vl = QVBoxLayout(vid)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.addWidget(self.pubs, 3)
        self.curve = RetentionChart()
        vl.addWidget(self.curve, 2)
        self.pubs.currentCellChanged.connect(lambda r, *_: self._show_curve(r))
        self.tabs.addTab(vid, "Par vidéo")
        lay.addWidget(self.tabs, 1)
        row = QHBoxLayout()
        row.addWidget(button("Saisir des chiffres…", on_click=self._manual,
                             tooltip="Pour les plateformes sans relevé automatique : recopiez les chiffres"))
        row.addStretch(1)
        row.addWidget(label("Engagement = (j'aime + commentaires + partages) / vues, sur les chiffres fournis. "
                            "« — » = non fourni par la plateforme. Double-clic sur une vidéo : ouvrir son lien.",
                            "faint", wrap=True), 3)
        lay.addLayout(row)

    def _table(self, headers: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().hide()
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        t.setShowGrid(False)
        t.setSortingEnabled(True)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        return t

    def _fill_concepts(self) -> None:
        cur = self.concept.currentData()
        self.concept.blockSignals(True)
        self.concept.clear()
        self.concept.addItem("Tous les concepts", None)
        for c in self.app.concepts.list_concepts():
            self.concept.addItem(c.name, c.id)
        self.concept.setCurrentIndex(max(0, self.concept.findData(cur)))
        self.concept.blockSignals(False)

    @staticmethod
    def _num(v, frac: float | None = None, color: str | None = None, tip: str = "") -> QTableWidgetItem:
        it = QTableWidgetItem(_fmt(v))
        it.setData(Qt.EditRole, v if v is not None else -1)        # tri numérique ; « — » en dernier
        it.setText(_fmt(v))
        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if frac is not None:
            it.setData(Qt.UserRole + 1, frac)
            it.setData(Qt.UserRole + 2, color)
        if v is None:
            it.setToolTip(tip or "Non fourni")
        return it

    def refresh(self) -> None:
        self._fill_concepts()
        rows = self.app.analytics.latest(self.concept.currentData(), self.platform.currentData())
        self.rows = rows
        for r in rows:
            r["platform_label"] = RULES[r["platform"]].label
            r["account_label"] = f"{r['platform_label']} · {r['account']}"
        while self.cards.count():
            w = self.cards.takeAt(0).widget()
            if w:
                w.hide()                  # sinon son affichage différé en fait une fenêtre
                w.setParent(None)
                w.deleteLater()
        measured = [r for r in rows if r["metrics"]]
        total_views = sum(r["metrics"].get("views", 0) for r in measured)
        for title, value, sub in (
                ("Vidéos publiées", _fmt(len(rows)), f"{len(rows) - len(measured)} sans chiffres"),
                ("Vues", _fmt(total_views) if any("views" in r["metrics"] for r in rows) else NA,
                 "sur les vidéos mesurées"),
                ("Meilleure vidéo", (max(measured, key=lambda r: r["metrics"].get("views", -1))["title"] or "—")[:28]
                 if measured else NA, "par vues")):
            c = Card(padding=12, spacing=2)
            c.lay.addWidget(label(title, "faint"))
            c.lay.addWidget(label(value, "h1"))
            c.lay.addWidget(label(sub, "muted"))
            self.cards.addWidget(c, 1)
        for key, src in (("concept", "concept"), ("platform", "platform_label"), ("account", "account_label")):
            groups = self.app.analytics.aggregate(rows, src)
            t = self.groups[key]
            t.setSortingEnabled(False)
            t.setRowCount(len(groups))
            top = max((g.get("views", 0) for g in groups), default=0) or 1
            for i, g in enumerate(groups):
                name = QTableWidgetItem(g["key"])
                t.setItem(i, 0, name)
                t.setItem(i, 1, self._num(g["publications"]))
                t.setItem(i, 2, self._num(g.get("views"), g.get("views", 0) / top, g.get("color") or "#5B8DEF"))
                for j, m in enumerate(("likes", "comments", "shares"), start=3):
                    tip = f"Fourni pour {g.get('n_' + m, 0)}/{g['publications']} vidéo(s)"
                    it = self._num(g.get(m), tip=tip)
                    if g.get(m) is not None:
                        it.setToolTip(tip)
                    t.setItem(i, j, it)
                eng = g.get("engagement")
                e = QTableWidgetItem(f"{eng * 100:.1f} %".replace(".", ",") if eng is not None else NA)
                e.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                t.setItem(i, 6, e)
            t.setSortingEnabled(True)
        t = self.pubs
        t.setSortingEnabled(False)
        t.setRowCount(len(rows))
        top = max((r["metrics"].get("views", 0) for r in rows), default=0) or 1
        for i, r in enumerate(rows):
            m = r["metrics"]
            it = QTableWidgetItem(r["title"] or "Sans titre")
            it.setData(Qt.UserRole, r["id"])
            it.setToolTip(r["public_url"] or "")
            t.setItem(i, 0, it)
            t.setItem(i, 1, QTableWidgetItem(r["concept"]))
            t.setItem(i, 2, QTableWidgetItem(r["account_label"]))
            t.setItem(i, 3, self._num(m.get("views"), m.get("views", 0) / top, r["color"]))
            for j, k in enumerate(("likes", "comments", "shares"), start=4):
                t.setItem(i, j, self._num(m.get(k)))
            av = m.get("avg_view_s")
            d = QTableWidgetItem(f"{int(av) // 60}:{int(av) % 60:02d}" if av is not None else NA)
            d.setData(Qt.EditRole, av if av is not None else -1)
            d.setText(f"{int(av) // 60}:{int(av) % 60:02d}" if av is not None else NA)
            d.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if av is None:
                d.setToolTip("Disponible pour les vidéos d'une chaîne YouTube connectée (2-3 jours de délai)")
            t.setItem(i, 7, d)
            pc = m.get("avg_view_pct")
            e = QTableWidgetItem(f"{pc:.0f} %" if pc is not None else NA)
            e.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            t.setItem(i, 8, e)
            src = {"api": "API", "manuel": "saisi"}.get(m.get("_source"), NA)
            t.setItem(i, 9, QTableWidgetItem(src))
            t.setItem(i, 10, QTableWidgetItem(local_time(r["captured_at"]) or "jamais"))
        t.setSortingEnabled(True)
        if not rows:
            self.status.setText("Aucune vidéo publiée pour l'instant. Les publications confirmées (lien public) ou "
                                "mises en ligne par le logiciel apparaissent ici.")
        elif not self.job_id:
            self.status.setText("")

    def _show_curve(self, row: int) -> None:
        if row < 0 or self.pubs.item(row, 0) is None:
            self.curve.set_curve([], "")
            return
        pid = self.pubs.item(row, 0).data(Qt.UserRole)
        r = next((x for x in self.rows if x["id"] == pid), None)
        self.curve.set_curve((r or {}).get("metrics", {}).get("retention") or [], (r or {}).get("title") or "")

    def _selected_pub(self) -> int | None:
        self.tabs.setCurrentWidget(self.pubs)
        row = self.pubs.currentRow()
        if row < 0:
            QMessageBox.information(self, "Analytiques", "Sélectionnez une vidéo dans l'onglet « Par vidéo ».")
            return None
        return self.pubs.item(row, 0).data(Qt.UserRole)

    def _manual(self) -> None:
        pid = self._selected_pub()
        if pid is None:
            return
        r = next(x for x in self.rows if x["id"] == pid)
        dlg = ManualStatsDialog(r["title"] or "Sans titre", r["metrics"], self)
        if dlg.exec():
            self.app.analytics.record(pid, dlg.values(), "manuel")
            self.refresh()

    def _open_link(self, it: QTableWidgetItem) -> None:
        url = self.pubs.item(it.row(), 0).toolTip()
        if url.startswith("http"):
            QDesktopServices.openUrl(QUrl(url))

    def _refresh_stats(self) -> None:
        self.job_id = self.app.jobs.submit("refresh_stats", "Statistiques", {})
        self.refresh_btn.setEnabled(False)
        self.status.setText("Relevé des statistiques en cours…")

    def on_job(self, job) -> None:
        if job.id != self.job_id or job.status in ("pending", "running"):
            return
        self.job_id = None
        self.refresh_btn.setEnabled(True)
        if job.status == "succeeded":
            r = job.result or {}
            msg = f"{r.get('updated', 0)} vidéo(s) relevée(s)"
            if r.get("detailed"):
                msg += f", dont {r['detailed']} avec durée de visionnage et rétention"
            msg += "."
            if r.get("note"):
                msg += " " + r["note"]
            if r.get("errors"):
                msg += " ⚠ " + " ; ".join(r["errors"][:3])
            self.status.setText(msg)
        else:
            self.status.setText(f"⚠ {(job.error or 'Annulé').splitlines()[-1][:300]}")
        self.refresh()
