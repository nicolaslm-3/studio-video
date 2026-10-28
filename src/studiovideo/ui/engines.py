"""Moteurs de traduction et de voix : choix proposés, alternatives et conseils (roues crantées de Traduction).

Chaque moteur tient en peu de lignes : nom, état, qualité et coût (ce qu'il demande au survol) ; une clé se saisit
ici même (coffre Windows) et « Tester » fait un vrai essai (quelques mots), dont le résultat est affiché tel quel.
Une pastille choisit le moteur utilisé : en cocher une décoche les autres (un moteur pas encore prêt ne peut pas
être choisi). Les voix hors ligne et le modèle de langage se règlent aussi ici (`engine_panels.py`)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLineEdit, QRadioButton,
                               QScrollArea, QVBoxLayout, QWidget)

from ..ai import translate as trm
from ..ai import tts
from ..core import AppContext
from ..services import secrets
from .widgets import button, label

# clé secrète de chaque moteur qui en demande une
TR_KEYS = {"deepl": "deepl_api_key", "google": "google_api_key", "microsoft": "azure_translator_key"}


def _cfg(app: AppContext, engine: str | None = None) -> dict:
    return trm.settings_cfg(app.settings.get, engine)


def translation_choices(app: AppContext) -> list[tuple[str, str]]:
    """(clé, libellé) du menu « Traduction par » : Automatique d'abord, puis chaque moteur prêt."""
    cfg = _cfg(app)
    ready = [k for k in trm.AUTO_ORDER if trm.configured(k, cfg, secrets.get_secret)]
    first = trm.ENGINES[ready[0]][0] if ready else "aucun"
    out = [("auto", f"Automatique (recommandé) · {first}")]
    for k in ready:
        name = trm.ENGINES[k][0]
        if k == "llm":
            name = f"Modèle de langage ({cfg['llm'].get('model', '')})"
        out.append((k, name))
    return out


def _ready_text(ok: bool | str, missing: str = "À régler") -> QWidget:
    """Pastille d'état : « Prêt », « À régler », ou un texte (« Clé à corriger »)."""
    if isinstance(ok, str):
        return label(ok, "badge")
    return label("Prêt" if ok else missing, "ready" if ok else "badge")


class _EngineRow(QFrame):
    """Un moteur : nom, état, qualité, coût, ce qu'il faut ; clé à saisir et test."""
    saved = Signal()

    def __init__(self, title: str, quality: str, cost: str, needs: str, url: str, ok: bool | str,
                 recommended: bool = False, key_name: str | None = None, extra: QWidget | None = None,
                 test=None, parent=None, compact: bool = False):
        super().__init__(parent)
        self.setProperty("role", "card")
        self.key_name, self.test = key_name, test
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 7, 14, 7) if compact else v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(3 if compact else 4)
        head = QHBoxLayout()
        self.pick = QRadioButton()                    # pastille : ce moteur pour la vidéo
        self.pick.setToolTip("Utiliser ce moteur pour la vidéo")
        self.pick.setEnabled(ok is True)
        if ok is not True:
            self.pick.setToolTip("À régler d'abord (clé ou installation ci-dessous)")
        head.addWidget(self.pick)
        t = label(title)
        t.setStyleSheet("font-weight: 700; font-size: 14px;")
        t.setToolTip(f"Il faut : {needs}")
        head.addWidget(t)
        if recommended:
            head.addWidget(label("Recommandé", "badge"))
        head.addStretch(1)
        self.state = _ready_text(ok)
        head.addWidget(self.state)
        v.addLayout(head)
        t.setToolTip(f"Qualité : {quality}\nCoût : {cost}\nIl faut : {needs}")
        if not compact:                                   # compact : qualité et coût au survol du nom
            info = label(f"{quality} · {cost}", "muted", wrap=True, hint=False)
            info.setToolTip(t.toolTip())
            v.addWidget(info)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.key = None
        if key_name:
            self.key = QLineEdit()
            self.key.setEchoMode(QLineEdit.Password)
            self.key.setEnabled(secrets.available())
            self.key.setPlaceholderText("Clé enregistrée (en coller une autre pour la remplacer)"
                                        if secrets.get_secret(key_name) else "Coller la clé ici")
            row.addWidget(self.key, 1)
            row.addWidget(button("Enregistrer", on_click=self._save))
        if extra is not None:
            row.addWidget(extra, 1)
        if test is not None:
            row.addWidget(button("Tester", "ghost", tooltip="Vrai essai, sur quelques mots", icon="play",
                                 on_click=self._test))
        if url:
            row.addWidget(button("Obtenir", "ghost", tooltip=url, icon="link",
                                 on_click=lambda: QDesktopServices.openUrl(QUrl(url))))
        if row.count():
            v.addLayout(row)
        self.result = label("", "faint", wrap=True, hint=False)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result.hide()
        v.addWidget(self.result)

    def say(self, text: str, ok: bool | None = None) -> None:
        self.result.setText(("✓ " if ok else "⚠ " if ok is False else "") + text)
        self.result.show()

    def _save(self) -> None:
        txt = self.key.text().strip()
        if not txt:
            return
        if self.key_name == "elevenlabs_api_key" and not txt.startswith("sk_"):
            self.say("Ceci ressemble à l'identifiant de la clé, pas à la clé : une clé ElevenLabs commence par "
                     "« sk_ » et n'est affichée qu'au moment de sa création (ElevenLabs → Developers → API Keys → "
                     "Create). Elle n'a pas été enregistrée.", False)
            return
        try:
            secrets.set_secret(self.key_name, txt)
        except secrets.SecretStoreUnavailable as exc:
            self.say(str(exc), False)
            return
        self.key.clear()
        self.key.setPlaceholderText("Clé enregistrée (en coller une autre pour la remplacer)")
        self.state.setText("Prêt")
        self.state.setProperty("role", "ready")
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)
        self.pick.setEnabled(True)
        self.pick.setToolTip("Utiliser ce moteur pour la vidéo")
        self.say("Clé enregistrée dans le coffre Windows.", True)
        self.saved.emit()

    def _test(self) -> None:
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ok, text = self.test()
        except Exception as exc:                      # le message du service, tel quel
            ok, text = False, str(exc) or repr(exc)
        finally:
            QGuiApplication.restoreOverrideCursor()
        self.say(text, ok)


class _Dialog(QDialog):
    changed = Signal()
    picked = Signal(str)                              # moteur choisi (pastille)

    def __init__(self, title: str, intro: str, tip: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 720)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        h = label(title, "h2")
        lay.addWidget(h)
        if intro:
            lay.addWidget(label(intro, "muted", wrap=True, hint=False))
        if tip:
            lay.addWidget(label("💡 " + tip, "muted", wrap=True, hint=False))
        host = QWidget()
        self.rows = QVBoxLayout(host)
        self.rows.setContentsMargins(0, 0, 6, 0)
        self.rows.setSpacing(8)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setWidget(host)
        lay.addWidget(sc, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Close).setText("Fermer")
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.choice = QButtonGroup(self)                  # une seule pastille cochée
        self.choice.setExclusive(True)
        self.picks: dict[str, QRadioButton] = {}
        self.chosen: str | None = None

    def add(self, row: _EngineRow, key: str | None = None) -> _EngineRow:
        row.saved.connect(self.changed.emit)
        self.rows.addWidget(row)
        if key:
            self._pick(row.pick, key)
        else:
            row.pick.hide()
        return row

    def _pick(self, radio: QRadioButton, key: str) -> None:
        self.choice.addButton(radio)
        self.picks[key] = radio
        radio.toggled.connect(lambda on, k=key: on and self._picked(k))

    def _picked(self, key: str) -> None:
        self.chosen = key
        self.picked.emit(key)

    def select(self, key: str | None) -> None:
        b = self.picks.get(key or "")
        if b is not None and b.isEnabled():
            b.setChecked(True)


class TranslationEnginesDialog(_Dialog):
    """Les moteurs de traduction, leurs alternatives et le conseil du moment."""

    def __init__(self, app: AppContext, parent=None):
        cfg = _cfg(app)
        ready = [k for k in trm.AUTO_ORDER if trm.configured(k, cfg, secrets.get_secret)]
        tip = ("Sans rien régler, la traduction marche déjà avec Google Traduction (gratuit). Pour la meilleure "
               "qualité, créez une clé DeepL API Free : gratuite jusqu'à 500 000 caractères par mois (environ "
               "8 heures de vidéo parlée), elle passe alors en premier.")
        if "deepl" in ready:
            tip = "DeepL est prêt : c'est lui que l'automatique utilise d'abord (la meilleure qualité)."
        super().__init__("Traduction des sous-titres",
                         "« Automatique » prend le meilleur moteur prêt, puis le suivant en cas d'échec. La même "
                         "traduction sert à la voix.", tip, parent)
        self.app = app
        auto = QFrame()
        auto.setProperty("role", "card")
        al = QHBoxLayout(auto)
        al.setContentsMargins(16, 12, 16, 12)
        self.auto = QRadioButton("Automatique (recommandé) : le meilleur moteur prêt, puis le suivant en cas d'échec")
        self.auto.setStyleSheet("font-weight: 700;")
        al.addWidget(self.auto)
        self.rows.addWidget(auto)
        self._pick(self.auto, "auto")
        for k in trm.AUTO_ORDER:
            name, quality, cost, needs, url = trm.ENGINES[k]
            extra = None
            if k == "microsoft":
                self.region = QLineEdit(app.settings.get("azure_translator_region", "") or "")
                self.region.setPlaceholderText("Région (ex. westeurope)")
                self.region.setFixedWidth(170)
                self.region.editingFinished.connect(
                    lambda: app.settings.set("azure_translator_region", self.region.text().strip()))
                extra = self.region
            elif k == "libre":
                self.libre = QLineEdit((app.settings.get("libretranslate", None) or {}).get("url", ""))
                self.libre.setPlaceholderText("Adresse du serveur, ex. http://localhost:5000")
                self.libre.editingFinished.connect(self._save_libre)
                extra = self.libre
            elif k == "llm":
                llm = cfg["llm"]
                if llm.get("model"):
                    name = f"{name} · {llm['model']}"
            self.add(_EngineRow(name, quality, cost, needs, url, k in ready, recommended=k == "deepl",
                                key_name=TR_KEYS.get(k) or ("libretranslate_api_key" if k == "libre" else None),
                                extra=extra, test=lambda k=k: self._test(k)), k)
        self.select(app.settings.get("translate_engine", None) or "auto")
        if self.chosen is None:
            self.auto.setChecked(True)
        self.picked.connect(lambda k: (app.settings.set("translate_engine", k), self.changed.emit()))
        from .engine_panels import LlmPanel
        llm_box = QFrame()                                # modèle de langage : il peut traduire (et Clipping)
        llm_box.setProperty("role", "card")
        ll = QVBoxLayout(llm_box)
        ll.setContentsMargins(16, 12, 16, 12)
        ll.addWidget(label("Modèle de langage", "h2"))
        self.llm = LlmPanel(app)
        self.llm.changed.connect(self.changed.emit)
        ll.addWidget(self.llm)
        self.rows.addWidget(llm_box)

    def _save_libre(self) -> None:
        url = self.libre.text().strip()
        self.app.settings.set("libretranslate", {"url": url} if url else {})
        self.changed.emit()

    def _test(self, kind: str) -> tuple[bool, str]:
        cfg = _cfg(self.app, kind)
        if not trm.configured(kind, cfg, secrets.get_secret):
            return False, "Pas encore réglé : " + trm.ENGINES[kind][3] + "."
        out = trm.make_translator(cfg, secrets.get_secret).translate(
            ["Bonjour à tous, merci d'avoir regardé cette vidéo."], "fr", "en")
        return True, f"Fonctionne : « {out[0]} »"


class VoiceEnginesDialog(_Dialog):
    """Les moteurs de voix du doublage, leurs alternatives et le conseil du moment."""
    open_voices = Signal()

    def __init__(self, app: AppContext, parent=None, current: str | None = None, language: str = "fr"):
        from ..ai import piper
        key = secrets.get_secret("elevenlabs_api_key") or ""
        has_el = bool(key) if not key or key.startswith("sk_") else "Clé à corriger"
        llm = app.settings.get("llm", None) or {}
        has_oa = llm.get("kind") == "openai" and bool(secrets.get_secret("llm_api_key"))
        voices = piper.installed_voices(app.paths.models)
        tip = ("Pour une voix crédible dans toutes les langues, ElevenLabs reste la référence (une même voix parle "
               "toutes les langues). Gratuit et sans Internet : Piper, à condition d'avoir téléchargé une voix dans "
               "la langue de la traduction.")
        super().__init__("Voix du doublage", "La voix lit la traduction à la place de la voix d'origine.", tip,
                         parent)
        self.app = app
        n, q, c, needs, url = tts.ENGINES["elevenlabs"]
        row = self.add(_EngineRow(n, q, c, needs, url, has_el, recommended=True, key_name="elevenlabs_api_key",
                                  test=self._test_eleven), "elevenlabs")
        if isinstance(has_el, str):
            row.say("La clé enregistrée ne commence pas par « sk_ » : c'est sans doute l'identifiant de la clé, pas "
                    "la clé elle-même. Créez une clé (ElevenLabs → Developers → API Keys → Create), copiez-la dès "
                    "qu'elle s'affiche et collez-la ici.", False)
        self.add(_EngineRow("ElevenLabs · doublage de la vidéo",
                            "La voix d'origine reproduite dans l'autre langue, à partir du son de la vidéo (ElevenLabs "
                            "fait sa propre transcription et sa traduction ; les sous-titres restent ceux du logiciel)",
                            "Décompté du crédit ElevenLabs (plus cher qu'une voix lue)", needs, url, has_el,
                            test=self._test_eleven), "eleven_dub")
        n, q, c, needs, url = tts.ENGINES["openai"]
        self.add(_EngineRow(n, q, c, needs, url, has_oa, test=self._test_openai), "openai")
        n, q, c, needs, url = tts.ENGINES["piper"]
        langs = sorted({v.split("_")[0] for v in voices})
        names = trm.LANG_NAMES
        have = ("Voix installées : " + ", ".join(names.get(lg, lg) for lg in langs)) if voices else \
            "Aucune voix installée"
        row = self.add(_EngineRow(n, q, c, needs, url, bool(voices)), "piper")
        row.say(have)
        from .engine_panels import OfflineVoicesPanel
        self.offline = OfflineVoicesPanel(app, language)  # voix hors ligne : téléchargées ici même
        self.offline.changed.connect(lambda: self._offline_changed(row))
        row.layout().addWidget(self.offline)
        self.select(current or app.settings.get("voice_engine", None))
        self.picked.connect(lambda k: app.settings.set("voice_engine", k))

    def _offline_changed(self, row: _EngineRow) -> None:
        from ..ai import piper
        ok = bool(piper.installed_voices(self.app.paths.models))
        row.pick.setEnabled(ok)
        row.state.setText("Prêt" if ok else "À régler")
        row.state.setProperty("role", "ready" if ok else "badge")
        row.state.style().unpolish(row.state)
        row.state.style().polish(row.state)
        self.changed.emit()

    def _test_eleven(self) -> tuple[bool, str]:
        eng = tts.make_engine("elevenlabs", secrets.get_secret, self.app.paths.models)
        n = len(eng.voices())
        cr = eng.credits()
        left = f" · crédit du mois : {max(0, cr[1] - cr[0]):,} caractères restants sur {cr[1]:,}".replace(",", " ") \
            if cr else ""
        return True, f"Clé valide : {n} voix disponibles{left}."

    def _test_openai(self) -> tuple[bool, str]:
        import tempfile
        from pathlib import Path
        eng = tts.make_engine("openai", secrets.get_secret, self.app.paths.models,
                              self.app.settings.get("llm", None) or {})
        with tempfile.TemporaryDirectory() as d:
            p = eng.synth("Bonjour.", "alloy", "fr", Path(d) / "t.wav")
            ok = p.exists() and p.stat().st_size > 200
        return ok, "Voix OpenAI disponible." if ok else "Pas de son reçu."


class TranscriptionEnginesDialog(_Dialog):
    """Les moteurs de transcription (roue crantée du bouton « Transcription ») : sur ce PC ou ElevenLabs Scribe v2."""
    open_models = Signal()                            # télécharger des modèles (Paramètres → Matériel et moteurs)

    def __init__(self, app: AppContext, parent=None):
        from PySide6.QtWidgets import QComboBox
        from ..transcribe import engines as asr
        key = secrets.get_secret("elevenlabs_api_key") or ""
        has_el = bool(key) if not key or key.startswith("sk_") else "Clé à corriger"
        have = asr.installed_models(app.paths.models) if asr.available() else []
        super().__init__("Transcription", "", "", parent)
        self.resize(600, 300)
        self.app = app
        n, q, c, needs, url = asr.TRANSCRIBERS["local"]
        self.model = QComboBox()
        for m in asr.MODELS:
            if m.key in have:
                self.model.addItem(m.label, m.key)
        if not have:
            self.model.addItem("Aucun modèle téléchargé", None)
        i = self.model.findData(app.settings.get("whisper_model", None))
        self.model.setCurrentIndex(max(0, i))
        self.model.currentIndexChanged.connect(
            lambda _i: self.model.currentData() and app.settings.set("whisper_model", self.model.currentData()))
        extra = QWidget()
        eh = QHBoxLayout(extra)
        eh.setContentsMargins(0, 0, 0, 0)
        eh.addWidget(self.model, 1)
        eh.addWidget(button("Télécharger des modèles", "ghost", icon="download",
                            on_click=lambda: (self.open_models.emit(), self.accept())))
        self.add(_EngineRow(n, q, c, needs, url, bool(have), extra=extra, compact=True), "local")
        n, q, c, needs, url = asr.TRANSCRIBERS["elevenlabs"]
        self.add(_EngineRow(n, q, c, needs, url, has_el, recommended=True, key_name="elevenlabs_api_key",
                            test=self._test_eleven, compact=True), "elevenlabs")
        self.rows.addStretch(1)
        self.select(app.settings.get("transcribe_engine", None) or "local")
        self.picked.connect(lambda k: app.settings.set("transcribe_engine", k))

    def _test_eleven(self) -> tuple[bool, str]:
        eng = tts.make_engine("elevenlabs", secrets.get_secret, self.app.paths.models)
        cr = eng.credits()
        left = f" · crédit du mois : {max(0, cr[1] - cr[0]):,} restants sur {cr[1]:,}".replace(",", " ") if cr else ""
        eng.voices()
        return True, f"Clé valide{left}."
