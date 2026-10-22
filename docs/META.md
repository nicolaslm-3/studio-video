# Connecter Instagram et Facebook (publication automatique)

Studio Vidéo publie des **Reels** avec l'API Graph officielle de Meta. Meta exige votre propre
application (gratuite, une fois) et, pour Instagram, un **compte professionnel** (Créateur ou Entreprise)
**relié à une Page Facebook**.

## 1. Créer l'application Meta

1. <https://developers.facebook.com/apps> → **Créer une app** → type **Entreprise**.
2. Ajoutez le produit **Facebook Login for Business** :
   *URI de redirection OAuth valides* : `http://localhost:8767/callback/` (adresse exacte).
3. Ajoutez le produit **Instagram** (API Graph Instagram).
4. Autorisations utilisées : `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`,
   `business_management`, `instagram_basic`, `instagram_content_publish`.
5. **Paramètres → Général** : copiez l'**ID de l'app** et la **clé secrète**.

Tant que l'application est en mode « Développement », seuls les comptes ayant un rôle sur l'application
(administrateur, testeur) peuvent se connecter : c'est suffisant pour vos propres comptes.

## 2. Dans Studio Vidéo

1. **Paramètres → Publication → Instagram et Facebook** : collez l'identifiant et la clé, **Enregistrer**.
2. **Paramètres → Concepts et comptes** : sélectionnez le compte Instagram ou Facebook → **Connecter**.
   Le compte Instagram est retrouvé par son nom d'utilisateur, la Page par son nom (sinon la première).
   Le port local 8767 doit être libre pendant la connexion.

## Utilisation

- Publication → **Actions ▾ → Publier sur Instagram / Facebook maintenant** (Reel).
- Programmée : la vidéo part automatiquement à l'heure prévue si Studio Vidéo est ouvert.
- Analytiques : j'aime, commentaires et vues des Reels Instagram (quand Meta les fournit).

## Limites

- Instagram limite le nombre de publications par l'API sur 24 h ; un refus est affiché clairement.
- Formats acceptés par Instagram pour un Reel : vertical 9:16 conseillé, jusqu'à 15 minutes environ
  (règles susceptibles d'évoluer : Meta refuse la vidéo avec un message si elle ne convient pas).
