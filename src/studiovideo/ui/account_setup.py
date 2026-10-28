"""Ajouter, régler et connecter un compte d'un réseau (onglet Export, « Ajouter un compte » ; Concepts et projets,
« Connecter »). Remplace l'ancien onglet Paramètres → Publication : chaque réseau demande votre propre application
développeur (gratuite) ; ses identifiants et la visibilité par défaut se règlent ici, les codes secrets vont dans
le coffre Windows, puis la connexion officielle (OAuth) s'ouvre dans le navigateur. Aucun mot de passe n'est demandé."""
from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QLineEdit,
                               QMessageBox, QVBoxLayout)

from ..core import AppContext
from ..domain.models import Platform
from ..services import secrets
from ..services.concepts import ConceptError
from .dialogs import language_combo
from .widgets import button, label

# Application développeur de chaque réseau : réglages (clé de réglage, libellé, secret ?), visibilité, guide
APPS = {
    "youtube": {
        "fields": [("youtube_client_id", "ID client", False, "xxxx.apps.googleusercontent.com"),
                   ("youtube_client_secret", "Code secret", True, "Code secret")],
        "privacy": ("youtube_privacy", "public", [("public", "Publique"), ("unlisted", "Non répertoriée"),
                                                   ("private", "Privée")]),
        "guide": "console.cloud.google.com → nouveau projet → activer « YouTube Data API v3 » → écran de "
                 "consentement (Externe, votre adresse en utilisateur test) → Identifiants → « ID client OAuth », "
                 "type « Application de bureau ».",
        "url": "https://console.cloud.google.com/apis/credentials"},
    "tiktok": {
        "fields": [("tiktok_client_key", "Clé client", False, "Clé client"),
                   ("tiktok_client_secret", "Code secret", True, "Code secret")],
        "privacy": ("tiktok_privacy", "PUBLIC_TO_EVERYONE", [("PUBLIC_TO_EVERYONE", "Tout le monde"),
                                                             ("MUTUAL_FOLLOW_FRIENDS", "Amis"),
                                                             ("FOLLOWER_OF_CREATOR", "Abonnés"),
                                                             ("SELF_ONLY", "Moi uniquement")]),
        "guide": "developers.tiktok.com → application (ordinateur), « Login Kit » avec l'adresse de retour "
                 "http://127.0.0.1:*/callback/ et « Content Posting API ». Tant que TikTok n'a pas audité "
                 "l'application, les vidéos sont publiées en privé.",
        "url": "https://developers.tiktok.com/apps"},
    "meta": {
        "fields": [("meta_app_id", "Identifiant de l'application", False, "Identifiant"),
                   ("meta_app_secret", "Clé secrète", True, "Clé secrète")],
        "guide": "developers.facebook.com → application (Entreprise), « Facebook Login for Business » avec la "
                 "redirection http://localhost:8767/callback/ et le produit Instagram. Le compte Instagram doit "
                 "être professionnel et relié à une Page Facebook. Une même application sert à Facebook et "
                 "Instagram.",
        "url": "https://developers.facebook.com/apps"},
    "x": {
        "fields": [("x_client_id", "Client ID", False, "Client ID"),
                   ("x_client_secret", "Code secret", True, "Facultatif (Native App)")],
        "guide": "developer.x.com → projet et application, OAuth 2.0 (Web App ou Native App), lecture et écriture, "
                 "rappel http://127.0.0.1:8768/callback. L'écriture dépend de votre offre X.",
        "url": "https://developer.x.com/en/portal/dashboard"},
}
REQUIRED = {"youtube": ("youtube_client_id", "youtube_client_secret"),
            "tiktok": ("tiktok_client_key", "tiktok_client_secret"),
            "meta": ("meta_app_id", "meta_app_secret"), "x": ("x_client_id",)}


def app_key(platform: str) -> str:
    return "meta" if platform in ("instagram", "facebook") else platform


def _value(app: AppContext, key: str, secret: bool) -> str:
    return (secrets.get_secret(key) or "") if secret else (app.settings.get(key, "") or "")


def app_configured(app: AppContext, platform: str) -> bool:
    """Application développeur du réseau réglée (identifiant et code secret enregistrés)."""
    k = app_key(platform)
    secret_keys = {f[0] for f in APPS.get(k, {}).get("fields", []) if f[2]}
    return k in REQUIRED and all(_value(app, f, f in secret_keys) for f in REQUIRED[k])


class AccountSetupDialog(QDialog):
    """Nouveau compte (nom, adresse, langue) ou compte existant, réglages de l'application du réseau, puis
    connexion. Après `exec()` : `account_id`, et `connect_job` si la connexion a été lancée."""

    def __init__(self, app: AppContext, platform: str, account_id: int | None = None, parent=None):
        super().__init__(parent)
        self.app, self.platform = app, platform
        self.account_id, self.connect_job = account_id, None
        acc = app.concepts.get_account(account_id) if account_id else None
        name = Platform(platform).label
        self.setWindowTitle(f"Connecter {acc.display}" if acc else f"Ajouter un compte {name}")
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        head = label(f"Compte {name}", "h2")
        lay.addWidget(head)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Nom affiché du compte")
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://…  (facultatif)")
        self.lang = language_combo()
        if acc is None:
            form.addRow("Nom", self.name)
            form.addRow("Adresse publique", self.url)
            form.addRow("Langue principale", self.lang)
        lay.addLayout(form)
        # -- application développeur du réseau
        self.fields: dict[str, tuple[QLineEdit, bool]] = {}
        self.privacy: QComboBox | None = None
        spec = APPS.get(app_key(platform))
        if spec:
            box = QFrame()
            box.setProperty("role", "card")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(16, 12, 16, 12)
            bl.setSpacing(8)
            ok = app_configured(app, platform)
            title = "Application Meta (Facebook et Instagram)" if app_key(platform) == "meta" else \
                f"Application {name}"
            st = label("✓ Réglée" if ok else "À régler", "ready" if ok else "badge")
            from .simple_views import _row
            bl.addLayout(_row(label(title, "h2"), "stretch", st))
            bl.addWidget(label("Pour publier automatiquement, le réseau demande votre propre application "
                               "développeur (gratuite). " + spec["guide"], "muted", wrap=True, hint=False))
            af = QFormLayout()
            af.setHorizontalSpacing(12)
            for key, lbl, secret, ph in spec["fields"]:
                w = QLineEdit()
                if secret:
                    w.setEchoMode(QLineEdit.Password)
                    w.setEnabled(secrets.available())
                    w.setPlaceholderText("Enregistré (en coller un autre pour le remplacer)"
                                         if secrets.get_secret(key) else ph)
                else:
                    w.setText(app.settings.get(key, "") or "")
                    w.setPlaceholderText(ph)
                self.fields[key] = (w, secret)
                af.addRow(lbl, w)
            if "privacy" in spec:
                pkey, default, opts = spec["privacy"]
                self.privacy = QComboBox()
                for k, n in opts:
                    self.privacy.addItem(n, k)
                self.privacy.setCurrentIndex(max(0, self.privacy.findData(app.settings.get(pkey, default))))
                af.addRow("Visibilité par défaut", self.privacy)
            bl.addLayout(af)
            bl.addLayout(_row(button("Ouvrir la console du réseau", "ghost", icon="link", tooltip=spec["url"],
                                     on_click=lambda: QDesktopServices.openUrl(QUrl(spec["url"]))), "stretch"))
            lay.addWidget(box)
        self.connect = QCheckBox("Connecter le compte maintenant (page de connexion dans le navigateur)")
        self.connect.setChecked(bool(spec))
        self.connect.setVisible(bool(spec))
        lay.addWidget(self.connect)
        if not spec:
            lay.addWidget(label(f"La connexion {name} n'est pas encore disponible : les vidéos y seront à publier à "
                                "la main.", "muted", wrap=True, hint=False))
        self.note = label("", "error", wrap=True, hint=False)
        self.note.hide()
        lay.addWidget(self.note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Connecter" if acc else "Ajouter")
        bb.button(QDialogButtonBox.Cancel).setText("Annuler")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _say(self, text: str) -> None:
        self.note.setText(text)
        self.note.show()

    def _save_app(self) -> bool:
        """Réglages de l'application : identifiants (réglages), codes secrets (coffre Windows), visibilité."""
        for key, (w, secret) in self.fields.items():
            txt = w.text().strip()
            if secret:
                if txt:
                    try:
                        secrets.set_secret(key, txt)
                    except secrets.SecretStoreUnavailable as exc:
                        self._say(str(exc))
                        return False
            else:
                self.app.settings.set(key, txt)
        if self.privacy is not None:
            self.app.settings.set(APPS[app_key(self.platform)]["privacy"][0], self.privacy.currentData())
        return True

    def _ok(self) -> None:
        if self.account_id is None and not self.name.text().strip():
            self._say("Donnez un nom au compte.")
            return
        if not self._save_app():
            return
        if self.account_id is None:
            try:
                acc = self.app.concepts.create_account(self.platform, self.name.text(), self.url.text().strip(),
                                                       self.lang.currentData())
            except ConceptError as exc:
                QMessageBox.warning(self, "Compte", str(exc))
                return
            self.account_id = acc.id
        if self.connect.isVisible() and self.connect.isChecked():
            if not app_configured(self.app, self.platform):
                if QMessageBox.question(self, "Connexion", "L'application du réseau n'est pas encore réglée "
                                        "(identifiant et code secret) : le compte est enregistré, mais il ne "
                                        "peut pas encore être connecté. Les vidéos y seront à publier à la main "
                                        "d'ici là.\n\nFermer quand même ?") != QMessageBox.Yes:
                    return
            else:
                acc = self.app.concepts.get_account(self.account_id)
                self.connect_job = self.app.jobs.submit("connect_account", f"Connexion · {acc.display}",
                                                        {"account_id": acc.id})
        self.accept()
