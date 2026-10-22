# Connecter un compte TikTok (publication automatique)

Studio Vidéo publie avec l'API officielle de TikTok (« Content Posting API »). TikTok exige que chaque
utilisateur crée **sa propre application développeur** (gratuit, une fois).

## 1. Créer l'application

1. <https://developers.tiktok.com/> → connectez-vous → **Manage apps → Connect an app**.
2. Plateforme : **Desktop**. Renseignez nom, icône, catégorie, conditions d'utilisation et politique de
   confidentialité (TikTok les demande ; une page simple suffit pour un usage personnel).
3. **Add products** : **Login Kit** et **Content Posting API**.
   - Login Kit → *Redirect URI* : `http://127.0.0.1:*/callback/` (le port est choisi au moment de la connexion).
   - Content Posting API → activez **Direct Post**.
4. **Scopes** : `user.info.basic`, `video.publish`, `video.list`.
5. Soumettez l'application. Copiez la **Client key** et le **Client secret**.

## 2. Dans Studio Vidéo

1. **Paramètres → Concepts et comptes → TikTok** : collez la clé et le code secret, choisissez la visibilité,
   **Enregistrer les réglages TikTok** (le code secret va dans le coffre Windows).
2. Sélectionnez le compte TikTok → **Connecter**, puis autorisez dans la page TikTok.

## Utilisation

- Publication → **Actions ▾ → Publier sur TikTok maintenant**.
- TikTok ne permet pas de programmer par son API : une publication datée part **automatiquement à l'heure
  prévue si Studio Vidéo est ouvert** (option « Mettre en ligne automatiquement… »).
- Analytiques → « Relever les statistiques » lit vues, j'aime, commentaires et partages des vidéos publiques.

## Limites imposées par TikTok

- **Application non auditée** : les vidéos sont publiées en **privé** (« Moi uniquement »). Studio Vidéo le
  détecte et l'indique (« Action manuelle requise ») au lieu de la marquer publiée. L'audit se demande
  depuis la page de l'application (Content Posting API → *Apply for audit*).
- Jeton de connexion valable 365 jours ; ensuite, cliquez à nouveau sur **Connecter**.
- TikTok limite le nombre de publications par jour et par compte ; un refus est affiché clairement.
