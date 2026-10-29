# Studio Vidéo — contexte pour Claude

Logiciel de bureau Windows (Python ≥ 3.11, PySide6) de production et de distribution vidéo assistée par IA.
Le propriétaire du projet n'est pas développeur : réponses en français, simples, sans jargon inutile.

## Règles du produit
- **Aucun bouton simulé, aucun faux succès.** Une fonction non opérationnelle est signalée comme telle
  (`widgets.py` : « non disponible »). Ne jamais annoncer un résultat qui n'a pas été vérifié.
- **Non destructif** : un média source n'est jamais copié ni modifié ; le montage est une description
  (`Timeline`, `edit/model.py`) rendue par FFmpeg à l'export.
- **Données hors du dossier du programme** : `%LOCALAPPDATA%\StudioVideo` (base SQLite, sauvegardes, modèles,
  journaux). Migrations versionnées dans `src/studiovideo/db/migrations/NNNN_nom.sql`, jamais modifiées après
  publication.
- **Secrets** (clés API, jetons OAuth) uniquement dans le coffre Windows via `services/secrets.py` (keyring).
- Traitements lourds hors du thread graphique : tout passe par `JobManager` (`jobs/manager.py`).
  Exports et publications sont des tâches « critiques » : jamais interrompues brutalement.

## Architecture
Voir `docs/ARCHITECTURE.md`. `services`, `jobs`, `media`, `update` n'importent pas Qt (testés sans interface) ;
`ui/` ne contient pas de logique métier.

Interface : dock en bas (`ui/shell.py`) avec Montage libre, Clipping, Traduction, Création originale (icône grisée,
toujours utilisable), **Textes** (mode `texts`, migration 0007) puis l'onglet **Export** (Campagne et Production en
série ont été retirés : ne pas les réintroduire). Clipping, Traduction et Textes ont à gauche, sur toute la hauteur,
la **barre des projets** (`ui/project_sidebar.py`, même fond que le logiciel : nom de l'onglet et « + », chaque
projet avec en gris 3 ou 4 mots-clés de sa transcription, « … » si trop long, en retrait ses versions traduites
puis ses extraits devenus des vidéos (`clip_projects`) ; aussi dans Export, où l'on reste en changeant de projet ;
un projet au nom donné d'office prend le nom du média importé — titre de la vidéo pour un lien —,
`ProjectService.name_after_media`) ; ailleurs, le menu des projets est en haut (« Tous les projets »). **Ordre stable** : un
projet ne remonte que s'il a vraiment été modifié (`ProjectService.save_data` compare, `NAV_KEYS` ne comptent pas) ;
un rafraîchissement (tâche finie, import) ne met pas la lecture en pause (`StudioPage._apply_layout`). Le son suit
la sortie audio de Windows (écouteurs Bluetooth connectés en cours de route : `ui/audio_out.py`).
**Versions traduites** : « Préparer » en Traduction crée un projet par langue sous le projet d'origine
(`ProjectService.save_version`, données `parent` / `version_lang`, nom « Projet · V-EN »), avec ses sous-titres
et sa voix doublée : on le retrouve dans tous les onglets (sous-titres dans Textes, piste Voix dans Montage libre). **Un projet = une vidéo, qui traverse tous les onglets** : même
montage (`Timeline`), même format ; la vidéo importée est placée d'office dans la timeline (`StudioPage.
_sync_project_video`, `project_video`). Un clic sur un parcours affiche le projet ouvert dans cette interface
(`project.mode` = dernière interface, `ProjectService.set_mode`) ; sans projet ouvert, le dernier, sinon un projet
vierge conservé seulement à la première modification (`ProjectService.is_untouched`). Clic droit sur un projet :
renommer, supprimer (un projet supprimé emporte ses versions). L'en-tête du Studio est commun à tous les onglets :
Médias (rectangle volant : la liste des vidéos, moitié de la hauteur de l'écran), Importer, lien (icônes grises, à
gauche), **Transcription** (`ui/transcript_dialog.py` : relire et corriger la transcription, enregistré de lui-même ;
la version corrigée sert partout, `TranscriptService.save_edit`, et les sous-titres déjà faits de cette vidéo sont
refaits dans tous les projets, `StudioPage.transcript_saved` ; horloge : historique des transcriptions ; roue crantée
dans la fenêtre : Whisper sur ce PC ou ElevenLabs Scribe v2, `core.asr_params`, réglage `transcribe_engine`), tâches
(pas de
bouton Exporter ni de « Suivant » : on passe d'un onglet à l'autre par le dock ; import masqué dans Export).
« Enregistré » et les messages brefs s'affichent en haut de la liste des tâches ; chaque tâche y est décrite en
colonnes — média, action en cours, outil / modèle (`jobs_panel.describe_parts`) —, son état en emoji (mot au survol),
et un bouton carré l'**arrête à tout instant** (`JobManager.cancel` : arrêtée tout de suite, un travail qui ne
s'interrompt pas finit en arrière-plan et son résultat est ignoré ; export ou publication : après confirmation).
Changer d'extrait, de version ou de projet remet la lecture au début (`MontagePlayer.rewind`).
**Historique** (migration 0008, `services/history.py`) : chaque transcription, traduction et voix générée est
gardée et appliquée d'office ; on revient à une ancienne par l'horloge (`HistoryPopover` : moteur, puis en gris le
début du texte) ; une voix a son propre dossier (`DubService.new_directory`). Les dates sont enregistrées en temps
universel et affichées à l'heure du PC (`widgets.local_time`). Infobulles dans la bulle du thème
(`TipFilter`). Timecodes au dixième de seconde, le dixième plus petit et grisé (`widgets.tc_html`, `TENTH_PX` : Qt ignore les %).
Sous chaque aperçu, le **bouton robot** (`ui/effects.py`) : miroir, zoom et recadrage, mouvement subtil, filtre
colorimétrique et son intensité (`render.LOOKS`), légère rotation, vitesse — sur la vidéo du projet
(`Timeline.video`), jamais sur les textes ni les sous-titres ; la vitesse change la durée exportée
(`render.output_duration`), le montage reste en temps d'origine. Barre de lecture et outils : sous l'image, à sa
largeur s'ils y tiennent, sinon sur toute la largeur (jamais les uns sur les autres) : petit bouton lecture, timecode,
outils collés (robot, puis le **format** — `montage_player.FormatPicker`, menu 16:9, 1:1, 9:16 et, séparées, les
zones masquées par TikTok / Reels), volume ; pas de boutons image avant / arrière (flèches du clavier). Pas de page
d'accueil : le logiciel s'ouvre dans Montage libre. Emojis des textes et sous-titres en couleur, drapeaux en images
(`edit/emoji.py`, `ui/flags/<pays>.png`, emojis 3D Noto, Apache 2.0 : Windows n'a pas de drapeaux emoji, il écrit
« FR »). Une vidéo chargée à l'arrêt montre l'image du timecode (`MontagePlayer._peek`). Aperçus
sans bandes noires, barre de lecture sous l'image. **Toute valeur numérique** se règle à la souris (`ui/scrub.py` :
clic-glisser, molette, + et − superposés à droite, double-clic pour saisir ; `ScrubNumber` remplace les champs
numériques de Qt). Curseurs de réglage courts (`SLIDER_W`), pastilles de couleur carrées.
Chaque onglet fait sa tâche : Montage libre, Création originale et Textes dans le Studio (`LAYOUTS` ; Textes :
onglets Textes / Sous-titres seulement, petits boutons, début / fin et taille réglables à la souris, position
Haut / Centre / Bas puis X / Y libres, B / I et alignement, aperçu sans cadre autour, réglages sans libellés —
icône grise et infobulle :
« A » gras pour la couleur, « A » en contour, « A » en négatif pour le fond, réglé par sa seule opacité (0 = pas de
fond) —, sans « Tous les outils » ni annuler / rétablir) ;
Clipping et Traduction dans `ui/simple_views.py` (inspirés de ClipFlow et Dubber) ; Export dans `ui/export_view.py`.
Clipping (découpage seulement, sans outils de texte) : quatre critères (`ai/lexicon.CATEGORIES` : Rire, Polarisant,
Émotion, Politique ; politique par défaut, explication au survol) mesurés sur la transcription et le son
(`highlights.AudioProfile` : volume, saturation, éclats hors parole, voix plus aiguë, débit), demandés pendant la
transcription puis dans la bulle de « Proposer d'autres extraits » (avec durée et nombre, ouverte au-dessus du
bouton) ; pas d'étapes ni de phrases d'état (seulement une action ou une progression) : en haut « Vidéo complète »,
puis « Extraits » et les extraits à cocher (colonne étroite, hauteur fixe : titre de 3 à 8 mots,
`highlights.summary`, et en gris le début de la transcription), « + » pour en ajouter un à la main (petite bulle
début / durée) ; les deux colonnes au centre (de l'espace sur les bords), l'aperçu en haut de la sienne
(`MontagePlayer.fit_top`) ; sans vidéo, seulement une petite zone de dépôt au centre ; début et fin à la souris
(± 1 s), suivi de la
tête sur bouton-icône (`edit/reframe.py`) ; chaque extrait coché devient une vidéo (le premier celle du projet, les
autres un projet chacun). Traduction prépare les langues (`translate_video` avec `export=False`, une version par
langue) sur le montage du projet ; le projet d'origine montre la vidéo originale, chaque version (en retrait
dans la barre) sa langue ; sans « Tous les outils » : langues en drapeaux emoji (`ui/flags/`, pays au survol), un
outil par ligne (Sous-titres avec « Traduire » — le texte seul, puis « Corriger » par langue, la traduction
corrigée servant aux sous-titres et à la voix —, Voix, Musique, Effacer les textes d'origine : nom suivi de sa
coche verte ou rouge, interrupteur
`ToggleSwitch`, moteur utilisé, roue crantée ; voix hors ligne : télécharger une langue ou toutes ; chaque voix s'écoute, extrait publié par le service, rien
n'est généré ; doublage automatique d'ElevenLabs à partir du son de la vidéo, voix `eleven_dub`,
`ai/elevenlabs.py` : ses sous-titres — le texte traduit par ElevenLabs — deviennent la traduction de la langue ; moteur
de traduction « ElevenLabs » au choix, à partir du son, jamais en automatique), puis ce que fait le logiciel et le résultat outil par outil (`result["status"]` : la voix peut échouer
sans emporter les sous-titres) ; l'aperçu d'une version fait entendre sa voix doublée (`StudioPage._tracks_for`).
Une version supprimée l'est pour de bon (sa langue quitte `translations.ready`). Moteur « Automatique » par défaut (`ai/translate.py` : le meilleur moteur prêt, puis le suivant en cas d'échec ;
Google Traduction et MyMemory gratuits sans clé) ; roues crantées = alternatives, clés, voix hors ligne et modèle
de langage (`ui/engines.py`, `ui/engine_panels.py`). Export, en deux colonnes : à gauche nom du fichier précédé
de la date du jour (« 2026-04-02 Nom.mp4 », `export_view.file_name`) et dossier, titre et description communs (sans
champ titre, le titre ouvre la description), petit aperçu, puis « Brouillon sur les réseaux » (comptes connectés par
API : YouTube privé, boîte de réception TikTok, Page Facebook ; les autres et Instagram par la page d'envoi) ou
« Sur mon PC seulement » — ni publication immédiate ni programmation ; à droite l'**Atelier de publication** : la
vidéo exportée (rectangle étroit, à glisser, ou « Afficher dans l'Explorateur »), puis une carte par réseau (YouTube,
TikTok, **Meta** = Facebook + Instagram, page d'envoi Meta Business Suite) : icône et nom, compte, « Ouvrir la page
d'envoi » (texte copié), lien de la vidéo publiée (`publish_workshop.LinkBox`) ; pas de case à cocher.
« Ajouter un compte » / « Connecter ce compte… » (`ui/account_setup.py`) : compte et adresse publique ; les pages
des réseaux s'ouvrent dans un **nouvel onglet du navigateur par défaut déjà ouvert**, avec les comptes où
l'utilisateur est connecté (`browser_login.open_page`) — pas de profil séparé ni de « Se connecter à ce compte » (le
profil par compte de `publish/browser.py` n'est plus proposé) ; la connexion par API (application développeur,
OAuth) est repliée dans « Avancé : connexion par API », jamais supprimée ; il n'y a plus d'onglet Paramètres →
Publication. **Sans API** : **Atelier de publication** (`ui/publish_workshop.py`, dans Export et depuis le
Calendrier) : page d'envoi dans un nouvel onglet du navigateur (texte copié en l'ouvrant), vidéo glissée depuis l'Atelier ; le clic
final « Publier » reste humain ; le lien est retrouvé tout seul sur la page publique du compte (`publish/reconcile.py`,
yt-dlp, YouTube et TikTok, tâche `match_published` toutes les heures) : sûr → confirmé, doute → « Est-ce cette
vidéo ? ». **Statistiques sans API** (`publish/public_stats.py`) : page publique de chaque vidéo publiée lue par
yt-dlp (Facebook / Instagram avec les cookies du navigateur par défaut, lus par yt-dlp, navigateur fermé), 6 h après puis
J+1, J+3, J+7, J+14, J+30, puis chaque mois, au plus 20 par heure, 5 à 20 s entre deux (`MainWindow.check_stats`) ;
Analytiques → « Par vidéo » : une vidéo sur les quatre réseaux dans un tableau (`AnalyticsService.by_video`).
Pas d'API de plateforme ni de service payant pour publier ou relever les statistiques.
Pas de titre ni de légende ailleurs que dans Export. Dans l'interface on dit « extrait », jamais « moment » ; la
bibliothèque s'appelle « Médias ».

## Tests
```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
```
FFmpeg doit être trouvable (PATH ou `STUDIOVIDEO_FFMPEG_DIR`), sinon des tests sont ignorés ou échouent.
La CI (`.github/workflows/ci.yml`) tourne sous Windows et Linux à chaque push.

## Publier une version
1. Modifier `__version__` dans `src/studiovideo/__init__.py` (ainsi que la ligne de version en tête du README).
2. Écrire les notes dans `docs/releases/X.Y.Z.md` (en français, pour l'utilisateur final).
3. Commit « Version X.Y.Z : résumé » puis push sur `main` : `release.yml` compile l'installateur, le publie
   sur GitHub Releases et met à jour le flux de mises à jour automatiques.
Ne jamais pousser ni publier sans l'accord explicite du propriétaire.
