"""Ajouter, régler et connecter un compte d'un réseau (onglet Export, « Ajouter un compte » ; Concepts et projets).

D'abord le navigateur dédié du compte (« Se connecter à ce compte », `browser_login.py`) : sans API ni application
développeur, il sert à l'Atelier de publication. La connexion par API (application développeur du réseau, OAuth)
reste possible, repliée dans « Avancé : connexion par API » ; les codes secrets vont dans le coffre Windows. Aucun mot
de passe n'est demandé."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QLineEdit,
                               QMessageBox, QToolButton, QVBoxLayout, QWidget)

from ..core import AppContext
from ..domain.models import Platform
from ..services import secrets
from ..services.concepts import ConceptError
from . import browser_login
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
# adresse publique attendue (sert à retrouver tout seul le lien des vidéos publiées)
URL_HINT = {"youtube": "https://www.youtube.com/@chaine", "tiktok": "https://www.tiktok.com/@nom",
            "instagram": "https://www.instagram.com/nom", "facebook": "https://www.facebook.com/page",
            "x": "https://x.com/nom"}
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
    """Nouveau compte (nom, adresse, langue) ou compte existant : navigateur dédié, puis (avancé) application du
    réseau et connexion par API. Après `exec()` : `account_id`, et `connect_job` si la connexion API a été lancée."""

    def __init__(self, app: AppContext, platform: str, account_id: int | None = None, parent=None,
                 advanced: bool = False):
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
        self.url = QLineEdit(acc.public_url if acc else "")
        self.url.setPlaceholderText(URL_HINT.get(platform, "https://…"))
        self.url.setToolTip("Adresse de la page publique du compte : le lien des vidéos publiées y est retrouvé tout "
                            "seul")
        self.lang = language_combo()
        if acc is None:
            form.addRow("Nom", self.name)
        form.addRow("Adresse publique", self.url)
        if acc is None:
            form.addRow("Langue principale", self.lang)
        lay.addLayout(form)
        # -- navigateur dédié (sans API)
        self.login = None
        nav = QFrame()
        nav.setProperty("role", "card")
        nl = QVBoxLayout(nav)
        nl.setContentsMargins(16, 12, 16, 12)
        nl.setSpacing(8)
        from .simple_views import _row
        self.nav_state = label("", "badge")
        nl.addLayout(_row(label("Navigateur dédié", "h2"), "stretch", self.nav_state))
        nl.addWidget(label("Le compte reste connecté dans son propre profil de ton navigateur : la page d'envoi de l'Atelier "
                           "de publication s'y ouvre déjà connectée. Tu te connectes toi-même, puis tu fermes la "
                           "fenêtre ; le logiciel ne voit jamais ton mot de passe.", "muted", wrap=True, hint=False))
        self.login_btn = button("Se connecter à ce compte", "primary", icon="log-in", on_click=self._browser_login)
        nl.addLayout(_row(self.login_btn, "stretch"))
        self.login_note = label("", "muted", wrap=True, hint=False)
        self.login_note.hide()
        nl.addWidget(self.login_note)
        lay.addWidget(nav)
        self._show_nav()
        # -- avancé : connexion par API (repliée)
        self.adv_btn = QToolButton()
        self.adv_btn.setText("Avancé : connexion par API")
        self.adv_btn.setCheckable(True)
        self.adv_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.adv_btn.setStyleSheet("QToolButton { border: none; font-weight: 600; }")
        self.adv = QWidget()
        adv_lay = QVBoxLayout(self.adv)
        adv_lay.setContentsMargins(0, 0, 0, 0)
        adv_lay.setSpacing(8)
        lay.addWidget(self.adv_btn)
        lay.addWidget(self.adv)
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
            adv_lay.addWidget(box)
        self.connect = QCheckBox("Connecter le compte par API maintenant (page de connexion dans le navigateur)")
        self.connect.setChecked(bool(spec) and app_configured(app, platform))
        self.connect.setVisible(bool(spec))
        adv_lay.addWidget(self.connect)
        if not spec:
            adv_lay.addWidget(label(f"Pas de connexion par API pour {name} : les vidéos y passent par l'Atelier de "
                                    "publication.", "muted", wrap=True, hint=False))
        open_adv = advanced or bool(acc and acc.connection_status == "connected")
        self.adv_btn.toggled.connect(self._fold)
        self.adv_btn.setChecked(open_adv)
        self._fold(open_adv)
        self.note = label("", "error", wrap=True, hint=False)
        self.note.hide()
        lay.addWidget(self.note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Enregistrer" if acc else "Ajouter")
        bb.button(QDialogButtonBox.Cancel).setText("Annuler")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _fold(self, on: bool) -> None:
        from . import theme
        self.adv_btn.setIcon(theme.icon("chevron-down" if on else "chevron-right", theme.color("muted"), 16))
        self.adv.setVisible(on)
        self.adjustSize()

    def _show_nav(self) -> None:
        if self.account_id is None:
            self.nav_state.setText("Non connecté")
            return
        st = browser_login.state_text(self.app, self.account_id)
        self.nav_state.setText("Non connecté" if st == "Connecté (API)" else st)

    def _create(self) -> bool:
        """Compte enregistré (nouveau compte : avec son nom, son adresse et sa langue)."""
        if self.account_id is not None:
            return True
        if not self.name.text().strip():
            self._say("Donnez un nom au compte.")
            return False
        try:
            acc = self.app.concepts.create_account(self.platform, self.name.text(), self.url.text().strip(),
                                                   self.lang.currentData())
        except ConceptError as exc:
            QMessageBox.warning(self, "Compte", str(exc))
            return False
        self.account_id = acc.id
        return True

    def _browser_login(self) -> None:
        if not self._create():
            return
        self.login = browser_login.BrowserLogin(self.app, self.account_id, self)
        err = self.login.start()
        self.login_note.show()
        if err:
            self.login_note.setText(err)
            return
        self.login_btn.setEnabled(False)
        self.login_note.setText("Connecte-toi dans la fenêtre ouverte, puis ferme-la.")
        self.login.finished.connect(self._logged)

    def _logged(self, found) -> None:
        self.login_btn.setEnabled(True)
        self.login_note.setText("✓ Compte connecté dans son navigateur." if found else
                                "Aucune session trouvée : la connexion n'a pas abouti." if found is False else
                                "Impossible de vérifier la connexion (navigateur encore ouvert ?).")
        self._show_nav()

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
        if not self._save_app() or not self._create():
            return
        self.app.concepts.update_account(self.account_id, public_url=self.url.text().strip())
        if self.adv_btn.isChecked() and self.connect.isVisibleTo(self) and self.connect.isChecked():
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
