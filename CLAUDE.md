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
série ont été retirés : ne pas les réintroduire). **Un projet = une vidéo, qui traverse tous les onglets** : même
montage (`Timeline`), même format ; la vidéo importée est placée d'office dans la timeline (`StudioPage.
_sync_project_video`, `project_video`). Un clic sur un parcours affiche le projet ouvert dans cette interface
(`project.mode` = dernière interface, `ProjectService.set_mode`) ; sans projet ouvert, le dernier, sinon un projet
vierge conservé seulement à la première modification (`ProjectService.is_untouched`). Clic droit sur un projet :
renommer, supprimer. L'en-tête du Studio est commun à tous les onglets : projet (menu avec flèche), format en trois
icônes (16:9, 1:1, 9:16), import (icône), lien (icône), Médias, tâches, Exporter (icône sur fond rouge, mène toujours
à l'onglet Export ; import et Exporter masqués dans Export). Infobulles dans la bulle du thème (`TipFilter`).
Timecodes au dixième de seconde.
Chaque onglet fait sa tâche : Montage libre, Création originale et Textes dans le Studio (`LAYOUTS` ; Textes :
médias à gauche, onglets Textes / Sous-titres / Export à droite, sans « Tous les outils » ni annuler / rétablir) ;
Clipping et Traduction dans `ui/simple_views.py` (inspirés de ClipFlow et Dubber) ; Export dans `ui/export_view.py`.
Clipping (découpage seulement, sans outils de texte) : critères demandés pendant la transcription (politique par
défaut, explication au survol), extraits à cocher, début et fin réglables à la souris, suivi de la tête sur
bouton-icône (`edit/reframe.py`) ; chaque extrait coché devient une vidéo (le premier celle du projet, les autres
un projet chacun), puis « Suivant » (`NextTab`, signal `goto_tab`) passe la vidéo à l'onglet suivant. Traduction
prépare les langues (`translate_video` avec `export=False`) sur le montage du projet ; moteur « Automatique »
par défaut (`ai/translate.py` : le meilleur moteur prêt, puis le suivant en cas d'échec ; Google Traduction et
MyMemory gratuits sans clé) ; icônes réglages = alternatives et clés (`ui/engines.py`). Export : nom du fichier et
dossier, titre et description communs, une colonne par réseau (YouTube Shorts, TikTok, Facebook, Instagram ; sans
champ titre, le titre ouvre la description), aperçu téléphone, puis publier / programmer via `PublicationService`,
ou « Sur mon PC seulement » (aucune publication). Plus de brouillon.
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
