# Connecter un compte X (publication automatique)

1. <https://developer.x.com/> → **Projects & Apps** → créez une application.
2. **User authentication settings** : OAuth 2.0 activé, permissions **Read and write**, type **Web App**
   (avec code secret) ou **Native App** (sans), *Callback URI* : `http://127.0.0.1:8768/callback` (exacte).
3. Copiez le **Client ID** (et le **Client Secret** si Web App).
4. Studio Vidéo : **Paramètres → Publication → X** : collez-les, **Enregistrer**, puis **Paramètres → Concepts
   et comptes** → sélectionnez le compte X → **Connecter**. Le port local 8768 doit être libre.

Utilisation : publication → **Actions ▾ → Publier sur X maintenant** ; une publication datée part à l'heure
prévue si le logiciel est ouvert. Analytiques : impressions, j'aime, réponses, reposts.

Limites : l'accès en écriture et l'envoi de vidéos dépendent de l'offre développeur de votre compte X
(éventuellement payante) ; un refus de X est affiché tel quel. Texte limité à 280 caractères (contrôlé avant
l'envoi). La durée maximale d'une vidéo dépend du compte (un refus est signalé).
