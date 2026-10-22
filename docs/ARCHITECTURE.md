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
│   ├── montage.py      timeline du projet, réglages par concept, historique des exports
│   ├── settings.py     paramètres
│   ├── transcripts.py  transcriptions (original conservé + corrections)
│   ├── candidates.py   propositions d'extraits (décisions, conversion en clips)
│   └── secrets.py      coffre d'identifiants Windows (keyring)
├── ai/
│   ├── highlights.py   moments forts : analyse locale, moteur LLM (Ollama / OpenAI), protection du contexte
│   ├── lexicon.py      indices lexicaux FR / EN par catégorie
│   └── translate.py    traduction : modèle de langage, DeepL, Google ; glossaire
├── edit/
│   ├── model.py        timeline : extraits, textes, musique, format, image, audio, export
│   ├── render.py       graphe FFmpeg, export 2 passes, aperçu exact, analyse audio, vérification
│   ├── textrender.py   rendu des textes par Qt (identique en aperçu et à l'export)
│   ├── subtitles.py    sous-titres : génération depuis la transcription, découpage, états d'animation
│   ├── subrender.py    dessin des sous-titres et incrustation en une passe (concat d'images)
│   ├── audiofix.py     « Optimiser l'audio » : mesures par extrait, choix des traitements, rapport
│   └── batch.py        production en série : clips × formats × langues
├── transcribe/
│   ├── model.py        transcription horodatée, corrections, exports TXT/SRT/VTT/JSON/ASS, import
│   └── engines.py      moteurs interchangeables (faster-whisper), modèles, extraction audio
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
    ├── player.py       lecteur source + points d'entrée/sortie
    ├── montage_player.py lecteur du montage, aperçu en direct du cadrage
    ├── timeline_view.py  timeline (pistes vidéo, textes, musique)
    ├── montage_panel.py  inspecteur : extrait, cadrage, textes, audio, export
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

## Modules ajoutés depuis la 0.7

| Module | Rôle |
|---|---|
| `ai/tts.py`, `ai/piper.py` | Voix de synthèse : ElevenLabs (clé de l'utilisateur), Piper hors ligne (programme MIT téléchargé, voix vérifiées par MD5) |
| `edit/dubbing.py`, `services/dubs.py` | Doublage phrase par phrase, calage temporel (accélération ≤ ×1,35), assemblage, reprise d'une phrase |
| `edit/separate.py` | Séparation voix / musique MDX-Net (ONNX), STFT numpy, par blocs de 60 s |
| `ai/diarize.py` | Intervenants : empreintes WeSpeaker + regroupement hiérarchique |
| `edit/textdetect.py` | Repérage des logos (contours permanents) et sous-titres incrustés (bandes intermittentes) |
| `edit/reframe.py` | Recadrage intelligent : visages UltraFace, trajectoire lissée par plan, expression `crop` FFmpeg |
| `edit/campaign.py`, `services/campaigns.py` | Brief de campagne, contrôle de conformité des exports et des textes |
| `edit/original.py` | Création originale : script en scènes, voix off, fabrication de la vidéo |
| `publish/rules.py` | Limites de texte par plateforme, composition des légendes, contrôle des liens |
| `publish/youtube.py`, `tiktok.py`, `meta.py`, `xapi.py` | Connexions OAuth (redirection locale, PKCE), envois reprenables, statistiques |
| `services/analytics.py` | Relevés horodatés ; un indicateur absent n'est jamais remplacé par zéro |
| `ui/guide.py` | Barre d'étapes des parcours guidés (Traduction, Campagne, Création originale) |
| `ui/pages/calendar.py`, `ui/pages/analytics.py` | Calendrier éditorial, comparaisons et rétention |

Tous les modèles d'IA tournent sur le PC (onnxruntime, CTranslate2) et sont téléchargés à la demande avec
vérification d'empreinte. Les services distants (ElevenLabs, DeepL, Google, plateformes sociales) ne sont
utilisés qu'avec les clés et applications de l'utilisateur, rangées dans le coffre d'identifiants Windows.

## Vérifications automatiques à chaque version

- Tests unitaires et d'interface (Windows et Linux), serveurs simulés pour chaque API distante.
- Tests réels sur Linux : transcription Whisper, séparation (gain de SDR mesuré), intervenants, voix Piper
  retranscrite par Whisper, recadrage vérifié par le détecteur de visages sur l'export.
- Auto-test de l'exécutable Windows distribué : base, FFmpeg, transcription réelle, séparation, export sous-titré,
  PDF et création originale, voix Piper Windows entendue par Whisper.
