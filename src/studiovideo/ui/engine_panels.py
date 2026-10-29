"""Réglages des moteurs, ouverts depuis les roues crantées de l'onglet Traduction : voix hors ligne (Piper) et
modèle de langage. Les téléchargements passent par les tâches (`download_model`) ; le panneau suit leur état."""
from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFormLayout, QHBoxLayout, QHeaderView, QLineEdit,
                               QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import AppContext
from ..services import secrets
from .widgets import button, label
from .widgets import ask

QUALITY_ORDER = {"high": 0, "medium": 1, "low": 2, "x_low": 3}
QUALITY_NAMES = {"x_low": "très basse", "low": "basse", "medium": "moyenne", "high": "haute"}


def _cell(w: QWidget) -> QWidget:
    box = QWidget()
    h = QHBoxLayout(box)
    h.setContentsMargins(4, 2, 4, 2)
    h.addWidget(w)
    return box


class _JobWatcher:
    """Suit des tâches (téléchargements) et rafraîchit le panneau quand l'une d'elles change."""

    def _watch_init(self, app: AppContext) -> None:
        self._jobs: dict[int, str] = {}
        self._poll = QTimer(self, interval=700)
        self._poll.timeout.connect(self._check_jobs)

    def _submit(self, kind: str, title: str, params: dict, key: str) -> None:
        self._jobs[self.app.jobs.submit(kind, title, params)] = key
        self._poll.start()
        self.fill()

    def running(self) -> set[str]:
        return set(self._jobs.values())

    def _check_jobs(self) -> None:
        changed = False
        for jid in list(self._jobs):
            j = self.app.jobs.get(jid)
            if j is None or j.status in ("succeeded", "failed", "cancelled"):
                self._jobs.pop(jid, None)
                changed = True
                if j is not None and j.status == "failed":
                    self.failed(j.error or "")
        if not self._jobs:
            self._poll.stop()
        if changed:
            self.fill()
            self.changed.emit()

    def failed(self, error: str) -> None:
        pass


class OfflineVoicesPanel(QWidget, _JobWatcher):
    """Voix hors ligne (Piper, gratuites, sur ce PC) : moteur, catalogue par langue, voix de la meilleure qualité
    en haut, téléchargement voix par voix ou « Tout télécharger »."""
    changed = Signal()

    def __init__(self, app: AppContext, language: str = "fr", parent=None):
        super().__init__(parent)
        from .dialogs import LANGUAGES
        self.app = app
        self._watch_init(app)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        row = QHBoxLayout()
        self.state = label("", "muted")
        row.addWidget(self.state, 1)
        self.bin_btn = button("Installer le moteur", "soft", icon="download",
                              on_click=lambda: self._submit("download_model", "Installation du moteur de voix",
                                                            {"model": "piper:bin"}, "piper:bin"))
        row.addWidget(self.bin_btn)
        v.addLayout(row)
        row2 = QHBoxLayout()
        self.lang = QComboBox()
        for c, n in LANGUAGES:
            self.lang.addItem(n, c)
        self.lang.setCurrentIndex(max(0, self.lang.findData(language)))
        self.lang.currentIndexChanged.connect(lambda _i: self.fill())
        row2.addWidget(self.lang)
        self.lang_btn = button("Télécharger cette langue", "soft", icon="download", on_click=self._download_lang,
                               tooltip="Toutes les voix de la langue choisie, de la meilleure qualité à la plus basse")
        row2.addWidget(self.lang_btn)
        self.all_btn = button("Tout télécharger", "ghost", icon="download", on_click=self._download_everything,
                              tooltip="Les voix de toutes les langues")
        row2.addWidget(self.all_btn)
        row2.addWidget(button("Actualiser le catalogue", "ghost",
                              on_click=lambda: self._submit("download_model", "Catalogue des voix",
                                                            {"model": "piper:catalog"}, "piper:catalog")))
        row2.addStretch(1)
        v.addLayout(row2)
        t = self.table = QTableWidget(0, 4)
        t.setHorizontalHeaderLabels(["Voix", "Qualité", "Taille", ""])
        t.verticalHeader().hide()
        t.setShowGrid(False)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        t.verticalHeader().setDefaultSectionSize(38)
        t.setMinimumHeight(200)
        v.addWidget(t, 1)
        self.fill()

    def _catalog(self, every: bool = False):
        """Voix du catalogue (de la langue choisie, ou de toutes), les meilleures d'abord."""
        from ..ai import piper
        models = self.app.paths.models
        try:
            if not (piper.root(models) / "voices.json").exists():
                return None
            cat = [x for x in piper.catalog(models) if every or x.family == self.lang.currentData()]
        except Exception:
            return None
        return sorted(cat, key=lambda x: (QUALITY_ORDER.get(x.quality, 4), x.language, x.name))

    def fill(self) -> None:
        from ..ai import piper
        models = self.app.paths.models
        exe = piper.exe_path(models)
        inst = piper.installed_voices(models)
        running = self.running()
        self.state.setText(("✓ Moteur installé" if exe else "Moteur non installé") +
                           (f" · {len(inst)} voix installée(s)" if inst else ""))
        self.bin_btn.setVisible(exe is None)
        self.bin_btn.setEnabled("piper:bin" not in running)
        t = self.table
        cat = self._catalog()
        if cat is None:
            t.setRowCount(1)
            t.setItem(0, 0, QTableWidgetItem("Catalogue non chargé : « Actualiser le catalogue »."))
            for j in (1, 2):
                t.setItem(0, j, QTableWidgetItem(""))
            t.removeCellWidget(0, 3)
            self.lang_btn.setEnabled(False)
            self.all_btn.setEnabled(False)
            return
        todo = [x for x in cat if x.key not in inst and "piper:" + x.key not in running]
        self.lang_btn.setEnabled(bool(todo))
        self.lang_btn.setText(f"Télécharger cette langue ({len(todo)})" if todo else "Langue téléchargée")
        rest = [x for x in self._catalog(True) or [] if x.key not in inst and "piper:" + x.key not in running]
        self.all_btn.setEnabled(bool(rest))
        self.all_btn.setText(f"Tout télécharger ({len(rest)})" if rest else "Tout est téléchargé")
        t.setRowCount(len(cat))
        for i, x in enumerate(cat):
            t.setItem(i, 0, QTableWidgetItem(f"{x.name} ({x.language})" + (f" · {x.speakers} voix" if x.speakers > 1
                                                                              else "")))
            t.setItem(i, 1, QTableWidgetItem(QUALITY_NAMES.get(x.quality, x.quality)))
            t.setItem(i, 2, QTableWidgetItem(f"{x.size_mb:.0f} Mo"))
            key = "piper:" + x.key
            if key in running:
                b = button("Téléchargement…", "ghost")
                b.setEnabled(False)
            elif x.key in inst:
                b = button("Supprimer", "ghost", on_click=lambda _=False, k=x.key: self._delete(k))
            else:
                b = button("Télécharger", "soft", icon="download",
                           on_click=lambda _=False, k=x.key: self._submit("download_model", f"Voix {k}",
                                                                          {"model": "piper:" + k}, "piper:" + k))
            t.setCellWidget(i, 3, _cell(b))
        if not cat:
            t.setRowCount(1)
            t.setItem(0, 0, QTableWidgetItem("Aucune voix hors ligne pour cette langue."))

    def _download_lang(self) -> None:
        self._download_all(self._catalog() or [])

    def _download_everything(self) -> None:
        """Toutes les langues : beaucoup de voix et de place, demandé d'abord."""
        from ..ai import piper
        inst = set(piper.installed_voices(self.app.paths.models))
        rest = [x for x in self._catalog(True) or [] if x.key not in inst]
        if not rest:
            return
        size = sum(x.size_mb for x in rest)
        if ask(self, "Voix hors ligne", f"Télécharger {len(rest)} voix, toutes langues confondues "
                                f"(environ {size / 1024:.1f} Go) ?"):
            self._download_all(rest)

    def _download_all(self, voices: list) -> None:
        from ..ai import piper
        inst = set(piper.installed_voices(self.app.paths.models))
        if piper.exe_path(self.app.paths.models) is None and "piper:bin" not in self.running():
            self._jobs[self.app.jobs.submit("download_model", "Installation du moteur de voix",
                                            {"model": "piper:bin"})] = "piper:bin"
        for x in voices:                                      # de la meilleure qualité à la plus basse
            if x.key not in inst and "piper:" + x.key not in self.running():
                self._jobs[self.app.jobs.submit("download_model", f"Voix {x.key}", {"model": "piper:" + x.key})] = \
                    "piper:" + x.key
        self._poll.start()
        self.fill()

    def _delete(self, key: str) -> None:
        from ..ai import piper
        if ask(self, "Voix", f"Supprimer la voix « {key} » ?"):
            piper.delete_voice(self.app.paths.models, key)
            self.fill()
            self.changed.emit()

    def failed(self, error: str) -> None:
        self.state.setText("⚠ Téléchargement impossible : " + (error.strip().splitlines() or [""])[-1][:200])


class LlmPanel(QWidget):
    """Modèle de langage (facultatif) : Ollama sur ce PC ou une API compatible OpenAI. Il peut traduire (moteur
    « Modèle de langage ») et choisir les extraits de Clipping."""
    changed = Signal()

    def __init__(self, app: AppContext, parent=None):
        super().__init__(parent)
        self.app = app
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        cfg = app.settings.get("llm", None) or {}
        form = QFormLayout()
        self.kind = QComboBox()
        for k, n in (("none", "Aucun"), ("ollama", "Ollama (local, sur ce PC)"),
                     ("openai", "API compatible OpenAI (OpenAI, Mistral, Groq, LM Studio…)")):
            self.kind.addItem(n, k)
        self.kind.setCurrentIndex(max(0, self.kind.findData(cfg.get("kind", "none"))))
        self.url = QLineEdit(cfg.get("base_url", ""))
        self.url.setPlaceholderText("Adresse (vide = valeur par défaut)")
        self.model = _ModelCombo(self.load_models)          # liste des modèles du service, rien à taper
        if cfg.get("model"):
            self.model.addItem(cfg["model"])
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("Clé API (enregistrée dans le coffre Windows)" if secrets.available()
                                    else "Coffre d'identifiants indisponible")
        form.addRow("Type", self.kind)
        form.addRow("Adresse", self.url)
        form.addRow("Modèle", self.model)
        form.addRow("Clé API", self.key)
        v.addLayout(form)
        row = QHBoxLayout()
        row.addWidget(button("Actualiser la liste des modèles", on_click=self.load_models))
        row.addWidget(button("Enregistrer", "primary", on_click=self.save))
        row.addStretch(1)
        v.addLayout(row)
        self.status = label("", "faint", wrap=True, hint=False)
        v.addWidget(self.status)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.kind.currentIndexChanged.connect(lambda _i: self.load_models())
        self.url.editingFinished.connect(self.load_models)
        self.key.editingFinished.connect(self.load_models)
        self._kind_changed()

    def _kind_changed(self) -> None:
        k = self.kind.currentData()
        for w in (self.url, self.model):
            w.setEnabled(k != "none")
        self.key.setEnabled(k == "openai" and secrets.available())

    def load_models(self, quiet: bool = False) -> bool:
        """Remplit la liste des modèles en interrogeant le service (Ollama ou API compatible OpenAI)."""
        from PySide6.QtWidgets import QApplication

        from ..ai.highlights import LLMError, chat_models, check_llm
        k = self.kind.currentData()
        if k == "none":
            return False
        if not quiet:
            self.status.setText("Recherche des modèles disponibles…")
            QApplication.processEvents()
        key = self.key.text().strip() or (secrets.get_secret("llm_api_key") or "")
        try:
            models = chat_models(check_llm(k, self.url.text().strip(), key if k == "openai" else ""),
                                 self.url.text().strip(), k)
        except LLMError as exc:
            self.status.setText(f"⚠ {exc}")
            return False
        cur = self.model.currentText()
        self.model.clear()
        self.model.addItems(models)
        if cur in models:
            self.model.setCurrentText(cur)
        self.model.loaded = True
        self.status.setText(f"{len(models)} modèle(s) disponible(s) : choisissez-en un, puis Enregistrer."
                            if models else "⚠ Aucun modèle disponible chez ce service.")
        return True

    def save(self) -> None:
        k = self.kind.currentData()
        cfg = {"kind": k, "base_url": self.url.text().strip(), "model": self.model.currentText().strip()}
        if k != "none" and not cfg["model"]:
            self.status.setText("⚠ Choisissez un modèle dans la liste.")
            return
        if k == "openai" and self.key.text().strip():
            try:
                secrets.set_secret("llm_api_key", self.key.text().strip())
            except secrets.SecretStoreUnavailable as exc:
                self.status.setText(f"⚠ {exc}")
                return
            self.key.clear()
        note = "Enregistré."
        if k != "none":
            # nom de modèle vérifié auprès du service : une faute de frappe ferait échouer chaque traduction
            from ..ai.highlights import LLMError, check_llm
            try:
                models = check_llm(k, cfg["base_url"], (secrets.get_secret("llm_api_key") or "") if k == "openai"
                                   else "")
            except LLMError as exc:
                models, note = None, f"Enregistré, mais le service n'a pas pu être vérifié : {exc}"
            if models is not None and cfg["model"] not in models:
                self.load_models(quiet=True)
                self.status.setText(f"⚠ Le modèle « {cfg['model']} » n'existe pas chez ce service : choisissez-en "
                                    "un dans la liste.")
                return
        self.app.settings.set("llm", cfg)
        self.status.setText(note)
        self.changed.emit()


class _ModelCombo(QComboBox):
    """Liste des modèles, chargée auprès du service à la première ouverture."""

    def __init__(self, loader):
        super().__init__()
        self.loader, self.loaded = loader, False

    def showPopup(self) -> None:                         # noqa: N802
        if not self.loaded:
            self.loader(quiet=True)
        super().showPopup()

