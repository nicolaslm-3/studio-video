# Architecture

```
src/studiovideo/
├── app.py              point d'entrée (journal, thème, contexte, fenêtre)
├── core.py             AppContext : assemble base, services, tâches — sans Qt
├── paths.py            dossiers persistants (%LOCALAPPDATA%\StudioVideo) et dossiers du programme
├── logging_setup.py    journal d'erreurs tournant
├── db/
│   ├── database.py     connexions SQLite par thread, transactions, migrations, sauvegardes
│   └── migrations/     NNNN_nom.sql, appliquées dans l'ordre (PRAGMA user_version)
├── domain/models.py    entités et énumérations métier (statuts, plateformes, parcours)
├── services/           logique métier sans interface
│   ├── concepts.py     concepts, comptes, associations, destinations proposées
│   ├── publications.py publication maître, destinations, héritage, statuts
│   ├── projects.py     projets et sauvegarde automatique de leur état
│   ├── library.py      recherche dans la bibliothèque
│   ├── settings.py     paramètres
│   └── secrets.py      coffre d'identifiants Windows (keyring)
├── jobs/manager.py     file de tâches persistante, threads de travail, annulation, tâches critiques
├── media/
│   ├── ffmpeg.py       localisation de FFmpeg, ffprobe, miniatures
│   ├── importer.py     import non destructif, empreinte, doublons, fichiers manquants
│   └── downloader.py   import par URL (yt-dlp)
├── update/
│   ├── updater.py      WinSparkle (ctypes), journal, coordination de l'arrêt avant installation
│   ├── config.py       clé publique et URL du flux (injectées à la compilation)
│   └── rollback.py     retour à la version précédente
└── ui/                 PySide6 — ne contient pas de logique métier
    ├── main_window.py  navigation latérale, 6 espaces, panneau des tâches, mises à jour
    ├── pages/          tableau de bord, studio, bibliothèque, paramètres
    ├── player.py       lecteur + points d'entrée/sortie
    ├── dialogs.py      concept, compte, nouveau projet
    ├── importing.py    import partagé (fichiers, glisser-déposer, URL)
    ├── jobs_panel.py   gestionnaire de tâches visible
    ├── theme.py        thèmes sombre/clair
    └── widgets.py      composants (cartes, états vides, « non disponible »)
```

## Principes

- **Séparation stricte.** `services`, `jobs`, `media` et `update` n'importent pas Qt. Ils sont
  testés sans interface, et un autre front (CLI, service distant de planification en V4) peut les
  réutiliser.
- **Moteurs remplaçables.** Chaque moteur (transcription, traduction, voix, publication) aura une
  interface commune. L'éditeur ne connaît que l'interface. Remplacer Argos par DeepL ne touche
  pas l'interface vidéo.
- **Non destructif.** Un média source n'est jamais copié ni modifié. Seuls son chemin, son
  empreinte et ses métadonnées sont stockés. Le montage est une description (`clip.edit_json`)
  rendue à l'export.
- **Traitements lourds hors du thread graphique.** Tout passe par `JobManager`. L'interface
  reçoit les notifications via un signal Qt en file d'attente (`JobBridge`).

## Modèle de données (§3.4)

`workspace` → `concept` ⟷ `social_account` (via `concept_account`, avec `is_default`) →
`project` → `source_asset` → `transcript`, `clip_candidate`, `clip` → `video_variant` →
`master_publication` → `publication` (une par compte) → `publication_attempt`,
`analytics_snapshot`.

### Règles imposées par le code

- Une publication est créée uniquement pour un compte explicitement choisi.
- `UNIQUE(master_id, social_account_id)` empêche les doublons.
- Un compte hors concept lève `ExternalAccountNotConfirmed` tant qu'il n'est pas confirmé. Il est
  alors marqué `is_exceptional`.
- Les champs `title`, `description` et `hashtags` à `NULL` héritent de la publication maître.
  Modifier la maître se propage aux destinations ; une modification locale reste locale.
- Le statut « publiée » est refusé sans identifiant distant confirmé par la plateforme.
- `schedule_mode` distingue la programmation sur la plateforme, locale ou distante (§19.1).

## Migrations

1. Ajouter `NNNN_description.sql`, avec la numérotation continue (vérifiée au démarrage).
2. Au démarrage, la base est sauvegardée, puis chaque migration s'applique dans une
   transaction. En cas d'échec, la base reste à la version précédente.
3. Une base plus récente que le programme est refusée avec un message explicite. Cela arrive
   après un retour à une version antérieure ; le script de restauration remet alors la base
   sauvegardée avant la mise à jour.
