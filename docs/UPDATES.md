# Mises à jour automatiques (§28)

## Chaîne complète

```
tag vX.Y.Z ─► GitHub Actions (Windows)
               ├ tests
               ├ PyInstaller (onedir) + FFmpeg + WinSparkle.dll
               ├ Inno Setup ─► StudioVideo-X.Y.Z-Setup.exe  (+ portable .zip, SHA256SUMS)
               ├ signature ed25519 de l'installateur (secret UPDATE_PRIVATE_KEY)
               ├ GitHub Release (installateurs versionnés)
               └ gh-pages : appcast.xml (stable) ou appcast-beta.xml (tags -beta.N)

Application ─► WinSparkle vérifie le flux (au démarrage puis toutes les 24 h, ou sur demande)
               ├ notes de version ; « Installer » / « Me le rappeler plus tard » / « Ignorer cette version »
               ├ téléchargement + vérification de la signature EdDSA (refus si absente ou invalide)
               ├ « puis-je fermer ? » → non tant qu'un export / une publication est en cours
               ├ fermeture : annulation des tâches non critiques, sauvegarde SQLite, journal
               └ installateur (silencieux) : copie de la version actuelle dans rollback\, remplacement
                 des fichiers du programme, relance de l'application
Relance ─► le journal confirme « mise à jour réussie X → Y » ou signale l'échec
```

## Mise en place (une seule fois)

1. **Créer le dépôt GitHub** et y pousser ce code.
2. **Générer la paire de clés** sur votre PC :
   ```
   python tools/update_keys.py generate --out update-private.key
   ```
   Gardez `update-private.key` **hors du dépôt** et sauvegardez-le. Sans cette clé, les
   installations existantes ne pourront plus recevoir de mises à jour.
3. **Créer le secret** GitHub `UPDATE_PRIVATE_KEY`, contenant le texte du fichier (Settings →
   Secrets and variables → Actions).
4. **Activer GitHub Pages** sur la branche `gh-pages` (Settings → Pages). Cette branche est créée
   automatiquement à la première publication.
5. *(Facultatif mais recommandé)* **Signature Authenticode.** Un certificat de signature de code
   évite l'avertissement « Windows a protégé votre ordinateur ». Il faut créer les secrets
   `WINDOWS_CERT_PFX_BASE64` et `WINDOWS_CERT_PASSWORD`. Sans certificat, tout fonctionne, mais
   SmartScreen avertira à la première installation.

## Publier une version

1. Mettre à jour `__version__` dans `src/studiovideo/__init__.py`.
2. Écrire les notes dans `docs/releases/X.Y.Z.md`.
3. Committer, puis `git tag vX.Y.Z && git push --tags`. Utiliser `vX.Y.Z-beta.N` pour le canal bêta.

Le workflow refuse un tag qui ne correspond pas à `__version__`.

## Garanties

- **Données préservées.** Elles sont dans `%LOCALAPPDATA%\StudioVideo`, jamais dans
  `%LOCALAPPDATA%\Programs\StudioVideo`. Ni la mise à jour ni la désinstallation n'y touchent.
- **Pas d'élévation administrateur.** L'installation se fait par utilisateur
  (`PrivilegesRequired=lowest`).
- **Aucune source non vérifiée.** Un installateur dont la signature EdDSA ne correspond pas à la
  clé publique intégrée au programme est rejeté.
- **Aucune interruption brutale.** La mise à jour attend la fin des exports et des publications.
- **Retour arrière possible** :
  - depuis Paramètres → À propos → « Revenir à la version … » ;
  - ou, si le programme ne démarre plus, depuis le menu Démarrer → « Studio Vidéo — restaurer la
    version précédente ».

  Le script remet les fichiers de la version précédente et la base sauvegardée juste avant la
  mise à jour.
- **Journal.** Tout est consigné dans `%LOCALAPPDATA%\StudioVideo\logs\updates.log`.

## Points à vérifier lors de la première vraie mise à jour

- La prise en compte par WinSparkle 0.9.x de l'attribut `sparkle:installerArguments`
  (installation silencieuse). S'il est ignoré, l'installateur s'ouvre normalement et
  l'utilisateur clique sur « Suivant » ; le reste est identique.
- Le nom exact de l'archive WinSparkle dans ses releases GitHub, utilisé par le workflow.
