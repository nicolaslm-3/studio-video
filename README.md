# Studio Vidéo

Studio de production et de distribution audiovisuelle assisté par IA, pour Windows.

**Version 0.3.0 — V1 complète.** Socle, montage, transcription et sous-titres : on peut importer,
transcrire, monter, recadrer, titrer, sous-titrer, régler le son et exporter un MP4. Ce qui n'est pas encore opérationnel est signalé comme tel dans
l'interface : aucun bouton n'est simulé.

## Ce qui fonctionne

| Domaine | État |
|---|---|
| Concepts éditoriaux, comptes sociaux, associations (plusieurs comptes par plateforme, un compte dans plusieurs concepts) | ✅ |
| Publication maître et publications par destination, avec héritage et exceptions, confirmation des comptes hors concept et prévention des doublons (couche données, sans interface) | ✅ |
| Projets (6 parcours ; « Montage libre » actif, les autres marqués « Prévu V2/V4 ») | ✅ |
| Import local non destructif : glisser-déposer, plusieurs fichiers, détection des doublons, analyse FFprobe, miniatures, contrôle d'espace disque | ✅ |
| Import par URL (yt-dlp), avec conservation de la provenance | ✅ (si yt-dlp est installé) |
| Bibliothèque : recherche, filtres, favoris, tags, détection des fichiers manquants | ✅ |
| Lecteur : lecture image par image, vitesse, points d'entrée/sortie, prévisualisation, création de clips | ✅ |
| Gestionnaire de tâches persistant (hors thread graphique, annulation, relance, reprise après redémarrage) | ✅ |
| Base SQLite à migrations versionnées, sauvegarde automatique, restauration | ✅ |
| Thème sombre / clair, disposition mémorisée, raccourcis clavier | ✅ |
| Mise à jour automatique (WinSparkle + Inno Setup + GitHub), retour à la version précédente | ✅ (voir [docs/UPDATES.md](docs/UPDATES.md)) |
| Timeline : extraits, coupe, déplacement, suppression, duplication, zoom, annuler/rétablir | ✅ |
| Recadrage 9:16 / 16:9 / 1:1 / 4:5, zoom, position, rotation, miroir, fond flou ou bandes noires | ✅ |
| Corrections d'image : luminosité, contraste, saturation, température, netteté | ✅ |
| Textes et accroches : police, taille, couleur, contour, fond, position, fondu | ✅ |
| Audio : gain, passe-haut, réduction de bruit, normalisation EBU R128 en 2 passes, limiteur, fondus | ✅ |
| Musique de fond avec atténuation automatique sous la voix (ducking) | ✅ |
| Analyse du son (volume, crêtes, silences) | ✅ |
| Aperçu en direct du cadrage et des textes, aperçu exact calculé par FFmpeg, zones TikTok/Reels | ✅ |
| Export MP4 H.264/H.265 avec vérification réelle du fichier (dimensions, durée, volume final) | ✅ |
| Réglages mémorisés par concept (format, image, audio, export, style de texte) | ✅ |
| Transcription locale (faster-whisper, CPU) avec mots horodatés, modèles téléchargeables, choix de la langue | ✅ |
| Correction du texte et des intervenants, original conservé, recherche, clic pour se positionner | ✅ |
| Clip ou extrait de timeline créé depuis une sélection de phrases | ✅ |
| Export TXT, SRT, VTT, JSON, ASS ; import de SRT / VTT existants | ✅ |
| Sous-titres générés depuis la transcription, découpage réglable (mots, caractères, lignes, pauses) | ✅ |
| Style complet, mot prononcé surligné, apparition mot par mot, effet pop, fondu ; incrustation ou .srt séparé | ✅ |
| Identification automatique des intervenants (diarisation) | ⏳ V2 (saisie manuelle disponible) |
| IA, traduction, doublage | ⏳ V2 |
| Publication, calendrier | ⏳ V3 |
| Analytiques | ⏳ V4 |

## Raccourcis

`Ctrl+1…6` espaces · `Ctrl+N` nouveau projet · `Ctrl+I` importer · `Ctrl+J` tâches.
Dans le lecteur : `Espace` lecture, `←/→` image par image, `I` / `O` entrée / sortie,
`P` prévisualiser, `C` créer un clip.

## Lancer depuis les sources

```bash
python -m pip install -e ".[dev]"
python -m studiovideo
```

FFmpeg doit être dans le PATH (ou dans `STUDIOVIDEO_FFMPEG_DIR`). La variable `STUDIOVIDEO_HOME`
permet d'utiliser un autre dossier de données.

## Tests

```bash
QT_QPA_PLATFORM=offscreen python -m pytest
```

Les 73 tests couvrent la base et ses migrations, ainsi que les scénarios D (Poussey Pass) et E (Pokémon).
Ils couvrent aussi les tâches, l'import, l'interface, la signature des mises à jour, le flux appcast
et de vrais exports FFmpeg (formats, modes de cadrage, musique, normalisation, vérification).

## Compiler l'exécutable Windows

La compilation se fait sur GitHub, sur une machine Windows, à chaque tag `vX.Y.Z` (fichier
`.github/workflows/release.yml`). Elle est reproductible.

1. Lancer les tests.
2. Embarquer FFmpeg et WinSparkle.
3. Compiler avec PyInstaller (`onedir`).
4. Construire l'installateur Inno Setup et la version portable `.zip`.
5. Signer l'installateur.
6. Créer la GitHub Release et mettre à jour le flux de mises à jour.

Sur un PC Windows, la même compilation se fait à la main :

```powershell
python -m pip install -e ".[dev]"
python tools/stamp_build.py            # écrit packaging/version.iss
pyinstaller packaging/studiovideo.spec --noconfirm
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" packaging\installer.iss
```

## Données

Les données ne sont jamais dans le dossier du programme. Elles se trouvent dans
`%LOCALAPPDATA%\StudioVideo` :

- `studio.db` : la base ;
- `backups\` : les sauvegardes ;
- `logs\` : le journal d'erreurs et celui des mises à jour ;
- `cache\` : les miniatures ;
- `models\` : les modèles IA ;
- `rollback\` : la version précédente.

Les clés API et les jetons iront dans le Gestionnaire d'identifiants Windows.

Documentation : [architecture](docs/ARCHITECTURE.md) · [dépendances et licences](docs/DEPENDENCIES.md)
· [mises à jour](docs/UPDATES.md).
