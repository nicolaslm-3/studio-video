"""Moteurs de traduction et de voix : choix proposés, alternatives et conseils (icône réglages de Traduction).

Chaque moteur est présenté avec sa qualité, son coût et ce qu'il demande ; une clé se saisit ici même (coffre
Windows) et « Tester » fait un vrai essai (quelques mots), dont le résultat est affiché tel quel."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLineEdit, QScrollArea, QVBoxLayout,
                               QWidget)

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
                 test=None, parent=None):
        super().__init__(parent)
        self.setProperty("role", "card")
        self.key_name, self.test = key_name, test
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(4)
        head = QHBoxLayout()
        t = label(title)
        t.setStyleSheet("font-weight: 700; font-size: 14px;")
        head.addWidget(t)
        if recommended:
            head.addWidget(label("Recommandé", "badge"))
        head.addStretch(1)
        self.state = _ready_text(ok)
        head.addWidget(self.state)
        v.addLayout(head)
        for k, txt in (("Qualité", quality), ("Coût", cost), ("Il faut", needs)):
            v.addWidget(label(f"{k} : {txt}", "muted", wrap=True, hint=False))
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

    def __init__(self, title: str, intro: str, tip: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 720)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        h = label(title, "h2")
        lay.addWidget(h)
        lay.addWidget(label(intro, "muted", wrap=True, hint=False))
        box = QFrame()
        box.setProperty("role", "card")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(14, 10, 14, 10)
        t = label("💡 " + tip, wrap=True, hint=False)
        bl.addWidget(t)
        lay.addWidget(box)
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

    def add(self, row: _EngineRow) -> _EngineRow:
        row.saved.connect(self.changed.emit)
        self.rows.addWidget(row)
        return row


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
        super().__init__("Moteurs de traduction",
                         "« Automatique » prend le meilleur moteur prêt, dans l'ordre ci-dessous. S'il échoue (crédit "
                         "épuisé, service en panne), le suivant reprend aussitôt là où il s'était arrêté. Le texte "
                         "à traduire est envoyé au service choisi (sauf avec Ollama, sur ce PC).", tip, parent)
        self.app = app
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
                extra = button("Régler le modèle", "ghost", icon="settings", on_click=self._open_settings)
            self.add(_EngineRow(name, quality, cost, needs, url, k in ready, recommended=k == "deepl",
                                key_name=TR_KEYS.get(k) or ("libretranslate_api_key" if k == "libre" else None),
                                extra=extra, test=lambda k=k: self._test(k)))

    def _save_libre(self) -> None:
        url = self.libre.text().strip()
        self.app.settings.set("libretranslate", {"url": url} if url else {})
        self.changed.emit()

    def _open_settings(self) -> None:
        self.done(2)                                  # le parent ouvre Réglages → Modèle de langage

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

    def __init__(self, app: AppContext, parent=None):
        from ..ai import piper
        key = secrets.get_secret("elevenlabs_api_key") or ""
        has_el = bool(key) if not key or key.startswith("sk_") else "Clé à corriger"
        llm = app.settings.get("llm", None) or {}
        has_oa = llm.get("kind") == "openai" and bool(secrets.get_secret("llm_api_key"))
        voices = piper.installed_voices(app.paths.models)
        tip = ("Pour une voix crédible dans toutes les langues, ElevenLabs reste la référence (une même voix parle "
               "toutes les langues). Gratuit et sans Internet : Piper, à condition d'avoir téléchargé une voix dans "
               "la langue de la traduction.")
        super().__init__("Voix du doublage", "La voix lit la traduction à la place de la voix d'origine. Sans voix, "
                         "la vidéo garde sa voix d'origine avec les sous-titres traduits.", tip, parent)
        self.app = app
        n, q, c, needs, url = tts.ENGINES["elevenlabs"]
        row = self.add(_EngineRow(n, q, c, needs, url, has_el, recommended=True, key_name="elevenlabs_api_key",
                                  test=self._test_eleven))
        if isinstance(has_el, str):
            row.say("La clé enregistrée ne commence pas par « sk_ » : c'est sans doute l'identifiant de la clé, pas "
                    "la clé elle-même. Créez une clé (ElevenLabs → Developers → API Keys → Create), copiez-la dès "
                    "qu'elle s'affiche et collez-la ici.", False)
        n, q, c, needs, url = tts.ENGINES["openai"]
        self.add(_EngineRow(n, q, c, needs, url, has_oa, test=self._test_openai))
        n, q, c, needs, url = tts.ENGINES["piper"]
        langs = sorted({v.split("_")[0] for v in voices})
        names = trm.LANG_NAMES
        have = ("Voix installées : " + ", ".join(names.get(lg, lg) for lg in langs)) if voices else \
            "Aucune voix installée"
        more = button("Télécharger des voix", "ghost", icon="download", on_click=self._voices)
        row = self.add(_EngineRow(n, q, c, needs, url, bool(voices), extra=more))
        row.say(have)

    def _voices(self) -> None:
        self.done(3)                                  # le parent ouvre Réglages → voix hors ligne

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
