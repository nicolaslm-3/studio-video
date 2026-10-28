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
│   ├── publications.py publication maître, destinations, héritage, statuts, liens retrouvés à confirmer
│   ├── projects.py     projets et sauvegarde automatique de leur état
│   ├── library.py      recherche dans la bibliothèque
│   ├── montage.py      timeline du projet, réglages par concept, historique des exports
│   ├── settings.py     paramètres
│   ├── transcripts.py  transcriptions (original conservé + corrections)
│   ├── history.py      historique des transcriptions, traductions et voix (on revient à une ancienne)
│   ├── candidates.py   propositions d'extraits (décisions, conversion en clips)
│   └── secrets.py      coffre d'identifiants Windows (keyring)
├── ai/
│   ├── highlights.py   extraits : analyse locale (texte + son : rire, polarisant, émotion, politique), moteur LLM
│   │                   (Ollama / OpenAI), titre en 3 à 8 mots, protection du contexte
│   ├── lexicon.py      indices lexicaux FR / EN par critère
│   ├── elevenlabs.py   ElevenLabs : transcription Scribe v2, doublage automatique à partir du son
│   └── translate.py    traduction : DeepL, modèle de langage, Google, Microsoft, LibreTranslate, gratuits
│                       (Google Traduction, MyMemory) ; « Automatique » avec reprise ; glossaire
├── edit/
│   ├── model.py        timeline : extraits, textes, musique, format, image, audio, export
│   ├── render.py       graphe FFmpeg, export 2 passes, aperçu exact, analyse audio, vérification
│   ├── compose.py      mise en page : double cadrage d'une même source, deux vidéos, incrustation
│   ├── textrender.py   rendu des textes par Qt (identique en aperçu et à l'export)
│   ├── emoji.py        emojis des textes en couleur, drapeaux en images (Windows n'en a pas)
│   ├── subtitles.py    sous-titres : génération depuis la transcription, découpage, états d'animation
│   ├── subrender.py    dessin des sous-titres et incrustation en une passe (concat d'images)
│   ├── audiofix.py     « Optimiser l'audio » : mesures par extrait, choix des traitements, rapport
│   └── batch.py        export des clips : clips × formats × langues (espace Clipping)
├── transcribe/
│   ├── model.py        transcription horodatée, corrections, exports TXT/SRT/VTT/JSON/ASS, import
│   └── engines.py      moteurs interchangeables (faster-whisper), modèles, extraction audio
├── jobs/manager.py     file de tâches persistante, threads de travail, annulation, tâches critiques
├── media/
│   ├── ffmpeg.py       localisation de FFmpeg, ffprobe, miniatures
│   ├── importer.py     import non destructif, empreinte, doublons, fichiers manquants
│   ├── downloader.py   import par URL (yt-dlp)
│   ├── proxy.py        aperçus légers des médias lourds
│   └── waveform.py     forme d'onde (pics à 50/s, cache disque) pour la timeline
├── update/
│   ├── updater.py      WinSparkle (ctypes), journal, coordination de l'arrêt avant installation
│   ├── config.py       clé publique et URL du flux (injectées à la compilation)
│   └── rollback.py     retour à la version précédente
└── ui/                 PySide6 — ne contient pas de logique métier
    ├── main_window.py  navigation latérale, 6 espaces, panneau des tâches, mises à jour
    ├── pages/          studio, médias, calendrier, analytics, paramètres (pas de page d'accueil)
    ├── player.py       lecteur source + points d'entrée/sortie
    ├── montage_player.py lecteur du montage, aperçu en direct du cadrage (coins arrondis, sans bandes noires)
    ├── project_sidebar.py barre des projets (Clipping, Traduction, Textes), ordre stable, versions en retrait
    ├── scrub.py        valeurs réglables à la souris (temps, nombres) : remplacent les champs numériques
    ├── effects.py      bouton robot : effets de l'image (miroir, zoom, mouvement, filtre, rotation, vitesse)
    ├── audio_out.py    le son suit la sortie audio de Windows (écouteurs Bluetooth)
    ├── account_setup.py ajouter / régler un compte : navigateur dédié, puis (avancé) connexion par API
    ├── browser_login.py « Se connecter à ce compte » : navigateur dédié du compte, session vérifiée à la fermeture
    ├── publish_workshop.py Atelier de publication (sans API) : texte copié, page d'envoi, vidéo à glisser, lien
    ├── engine_panels.py voix hors ligne (Piper) et modèle de langage, dans les roues crantées
    ├── flags/          drapeaux emoji en images (Noto 3D, Apache 2.0) : langues, emojis des textes
    ├── timeline_view.py  timeline (pistes vidéo, textes, musique, sous-titres, voix doublée)
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
| `edit/dubbing.py`, `services/dubs.py` | Doublage phrase par phrase, calage temporel (accélération ≤ ×1,35, ×1,5 pour rattraper un retard ; jamais de coupure : une phrase trop longue décale les suivantes, recalées dans les silences), assemblage, reprise d'une phrase |
| `ai/tts.py` | Voix : ElevenLabs, OpenAI (clé du modèle de langage), Piper hors ligne (une voix par langue, choisie selon la langue de la traduction) |
| `edit/separate.py` | Séparation voix / musique MDX-Net (ONNX), STFT numpy, par blocs de 60 s |
| `ai/diarize.py` | Intervenants : empreintes WeSpeaker + regroupement hiérarchique |
| `edit/textdetect.py` | Repérage des logos (contours permanents) et sous-titres incrustés (bandes intermittentes) |
| `edit/compose.py` | Mise en page (`VideoSettings.layout`) : zones et recadrage de chaque vue, géométrie partagée par l'export FFmpeg et l'aperçu en direct ; une même source n'est jamais entendue deux fois |
| `edit/reframe.py` | Recadrage intelligent : visages UltraFace, choix de la personne qui parle (mouvement de la bouche, image recalée), trajectoire lissée par plan, expression `crop` FFmpeg |
| `edit/silence.py` | Retrait des silences (silencedetect ou mots transcrits), alternance de zoom à chaque coupe |
| `publish/copywriter.py` | Titre, description, hashtags et chapitres YouTube proposés depuis la transcription |
| `services/archive.py` | Archive de projet `.studioproj` (export / import, renumérotation, médias reconnus par empreinte) |
| `edit/original.py` | Création originale : script en scènes, voix off, fabrication de la vidéo |
| `publish/rules.py` | Limites de texte par plateforme, composition des légendes, contrôle des liens |
| `publish/browser.py` | Navigateur dédié à chaque compte (Edge de Windows, sinon Chrome ; rien n'est téléchargé) : profil `browser/<id du compte>`, lancement normal sans automatisation, session reconnue au nom et à l'expiration de son cookie (jamais sa valeur) une fois le navigateur fermé |
| `publish/reconcile.py` | Rapprochement du lien d'une vidéo publiée à la main : dernières vidéos de la page publique du compte (yt-dlp, YouTube et TikTok), comparées par légende, durée et date ; sûr → confirmé (tâche `match_published`, au démarrage puis toutes les heures, pauses de 5 à 20 s), doute → proposé (table `publication_match`) |
| `publish/youtube.py`, `tiktok.py`, `meta.py`, `xapi.py` | Connexions OAuth (redirection locale, PKCE), envois reprenables, statistiques ; brouillons : YouTube en privé, boîte de réception TikTok (`video.upload`), brouillon de Page Facebook (Instagram n'en propose pas) |
| `services/analytics.py` | Relevés horodatés ; un indicateur absent n'est jamais remplacé par zéro |
| `ui/guide.py` | Barre d'étapes des parcours guidés (Clipping, Traduction, Création originale) |
| `ui/export_view.py` | Onglet Export : un cadre (aperçu compact — clic : lecture / pause avec icône 1,5 s —, nom du fichier daté « AAAA-MM-JJ Nom.mp4 », dossier, titre et description | que faire de la vidéo) ; une colonne par réseau (aperçu téléphone, compte avec sa photo, « Ajouter un compte » / « Connecter ce compte… », texte propre) ; publier / programmer (calendrier épuré, heure par 10 min) / brouillon sur les réseaux / sur le PC seulement ; emojis (icône grise dans chaque champ, recherche français-anglais, `ui/assets/emojis.json`) |
| `ui/simple_views.py` | Écrans simplifiés Clipping (« Vidéo complète », extraits à hauteur fixe à cocher, « + » pour en ajouter un, critères dans une bulle, début et fin à la souris, suivi de la tête sur bouton-icône, un projet par extrait coché) et Traduction (drapeaux, un outil par ligne avec interrupteur, moteur, état et roue crantée ; tâche en cours et résultat outil par outil ; « Préparer » crée une version par langue sous le projet) |
| `ui/engines.py` | Moteurs de traduction et de voix : alternatives (qualité et coût en une ligne), pastille du moteur utilisé, saisie et test des clés, voix hors ligne et modèle de langage intégrés (roues crantées de Traduction) |
| `ui/account_setup.py` | Ajouter / régler un compte : nom, adresse publique, « Se connecter à ce compte » (navigateur dédié) ; repliée dans « Avancé : connexion par API » : application développeur du réseau (identifiants, code secret dans le coffre, visibilité) et connexion OAuth. Les modules API restent, masqués |
| `ui/publish_workshop.py` | Atelier de publication (remplace la publication assistée) : un onglet par réseau, texte copié d'office (YouTube : le titre puis la description), vidéo glissable vers la page d'envoi, « Afficher dans l'Explorateur », page d'envoi dans le profil du compte, liens retrouvés à confirmer (« Est-ce cette vidéo ? »), lien collé en dernier recours. Le clic « Publier » reste à l'utilisateur |
| `ui/shell.py` | Dock des onglets, bulles et infobulles aux couleurs du thème (`TipFilter`), bouton des tâches |
| `edit/translation.py` | Vidéo traduite clé en main : styles de sous-titres, voix doublée, texte du haut ; utilisé par la tâche `translate_video` |
| `ui/color_picker.py` | Choix de couleur intégré (panneau flottant aux couleurs du thème) |
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
