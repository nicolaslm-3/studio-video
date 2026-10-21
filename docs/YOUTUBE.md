# Connecter une chaîne YouTube (mise en ligne automatique)

Studio Vidéo envoie les vidéos avec l'API officielle de YouTube. Google exige que chaque utilisateur
utilise **son propre identifiant d'application** : c'est gratuit et se fait une seule fois (≈ 10 minutes).

## 1. Créer le projet Google

1. Ouvrez <https://console.cloud.google.com/> avec le compte Google de la chaîne.
2. En haut : **Sélectionner un projet → Nouveau projet**, nom « Studio Vidéo », **Créer**.
3. Menu **API et services → Bibliothèque** : cherchez **YouTube Data API v3** → **Activer**.

## 2. Écran de consentement

1. **API et services → Écran de consentement OAuth** (ou « Google Auth Platform »).
2. Type d'utilisateur : **Externe**. Nom de l'application : « Studio Vidéo », votre adresse e-mail.
3. Dans **Audience / Utilisateurs test**, ajoutez l'adresse Gmail de la chaîne.

## 3. Identifiant client

1. **API et services → Identifiants → Créer des identifiants → ID client OAuth**.
2. Type d'application : **Application de bureau**. Nom : « Studio Vidéo ». **Créer**.
3. Copiez l'**ID client** et le **code secret du client**.

## 4. Dans Studio Vidéo

1. **Paramètres → Concepts et comptes → YouTube — projet Google Cloud** : collez l'ID et le code secret,
   choisissez la visibilité, **Enregistrer les réglages YouTube**. Le code secret est rangé dans le coffre
   d'identifiants Windows.
2. Sélectionnez le compte YouTube dans la liste → **Connecter**. Une page Google s'ouvre : choisissez la
   chaîne et acceptez. (Google affiche « application non validée » : c'est votre propre projet, cliquez
   **Continuer**.)
3. Le compte passe à « Connecté ».

## Utilisation

- Dans une publication, **Actions ▾ → Mettre en ligne sur YouTube** :
  - sans horaire : mise en ligne immédiate ;
  - avec un horaire futur : la vidéo est envoyée tout de suite en privé et **YouTube la publie lui-même à
    l'heure prévue**, même si votre PC est éteint.
- Avec « Mettre en ligne automatiquement… » coché, une publication programmée localement est envoyée à
  l'heure prévue si le logiciel est ouvert.
- L'envoi reprend tout seul après une coupure réseau. Une vidéo déjà envoyée ne l'est jamais deux fois.

## Limites imposées par Google

- **Quota** : les envois ont leur propre réserve, 100 vidéos par jour et par projet (vérifié en
  octobre 2026 sur la documentation de l'API). Au-delà, l'envoi échoue avec un message clair : réessayez le
  lendemain.
- Si YouTube laisse malgré tout une vidéo en privé (règle de la plateforme, vérification du compte…),
  Studio Vidéo le détecte et l'indique (« Action manuelle requise ») au lieu de la marquer publiée.
- En mode « Test » de l'écran de consentement, l'autorisation expire au bout de 7 jours : il suffit de
  cliquer à nouveau sur **Connecter**. Passer l'application « En production » supprime cette limite.
