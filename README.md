# Studio Vidéo

Studio de production et de distribution audiovisuelle assisté par IA, pour Windows.

**Version 0.20.0 — V1 et V2 complètes ; V3 : calendrier, publication assistée, mise en ligne automatique sur YouTube ; V4 : analytiques.** On peut importer, transcrire,
trouver les meilleurs moments d'une vidéo longue, monter, recadrer, titrer, sous-titrer, régler le son
et exporter un MP4. Ce qui n'est pas encore opérationnel est signalé comme tel dans
l'interface : aucun bouton n'est simulé.

## Ce qui fonctionne

| Domaine | État |
|---|---|
| Concepts éditoriaux, comptes sociaux, associations (plusieurs comptes par plateforme, un compte dans plusieurs concepts) | ✅ |
| Publication maître et publications par destination, avec héritage et exceptions, confirmation des comptes hors concept et prévention des doublons (couche données, sans interface) | ✅ |
| Projets : 6 parcours ; Montage libre, Clipping intelligent, Traduction (guidée), Campagne (brief + conformité), Production en série, Création originale : tous actifs | ✅ |
| **V2** — Parcours Traduction guidé : importer → transcrire → traduire → doubler → monter (sous-titres et voix dans la langue) → exporter | ✅ |
| **V2** — Campagne : brief client (formats, durées, mots requis/interdits, hashtags, mentions, partenariat, date limite) et contrôle automatique de chaque export et de chaque texte de publication | ✅ |
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
| Recadrage intelligent : le cadre suit les visages (export, aperçu, production en série) | ✅ |
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
| **V2** — Moments forts : analyse locale hors ligne (texte, énergie de la voix, intervenants) | ✅ |
| **V2** — Filtres humour, débat, politique, émotion, pédagogie, potentiel ; mots-clés ; intervenants ; durées | ✅ |
| **V2** — Score d'estimation, justification, accroche proposée, protection du contexte (§7.4) | ✅ |
| **V2** — Modèle de langage facultatif : Ollama local ou API compatible OpenAI | ✅ (à configurer) |
| **V2** — Accepter / rejeter / favoris / timecodes ; clips et timeline depuis les propositions | ✅ |
| **V2** — Montage par le texte (décocher des phrases, aperçu de la durée) | ✅ |
| **V2** — « Optimiser l'audio » : équilibrage des extraits et des intervenants, débruitage, compression, présence, de-esser, musique sous la voix, rapport des traitements | ✅ |
| **V2** — Production en série : clips × formats avec les réglages du montage, sous-titres et accroche automatiques | ✅ |
| **V2** — Traduction des transcriptions (13 langues) : modèle de langage (hors ligne avec Ollama), DeepL, Google ; glossaire ; corrections | ✅ |
| **V2** — Sous-titres traduits (voix d'origine conservée) et export multilingue en série | ✅ |
| **V2** — Doublage : voix de synthèse par intervenant (ElevenLabs ; Piper si installé), calage sur les phrases, voix d'origine remplacée ou gardée en fond, reprise phrase par phrase | ✅ |
| **V2** — Séparation voix / musique sur le PC (MDX-Net, ONNX) : voix isolée, doublage sur la musique et l'ambiance d'origine | ✅ |
| **V2** — Identification des intervenants sur le PC (empreintes vocales WeSpeaker), reportée sur les traductions | ✅ |
| **V2** — Masquage manuel de zones (texte incrusté, logo) : flou, effacement, bande | ✅ |
| **V2** — Détection automatique des logos et sous-titres incrustés : zones proposées, à vérifier (sans lecture du texte) | ✅ |
| **V4** — Création originale : idée, texte, PDF ou Word → script en scènes (IA si configurée) → voix off → vidéo (images, vidéos ou couleurs) avec textes et sous-titres calés | ✅ |
| **V2** — Voix hors ligne gratuites (Piper, licence MIT) pour le doublage et la voix off : moteur et voix téléchargés à la demande | ✅ |
| **V3** — Miniature : instant choisi + titre ; miniature YouTube, couverture TikTok et Instagram | ✅ |
| **V3** — Calendrier éditorial : tous concepts et comptes, mois par mois, filtre par concept | ✅ |
| **V3** — Publication : vidéo + texte maître, destinations du concept (hors concept sur confirmation), horaire et texte propres à chaque destination, contrôle des limites de chaque plateforme | ✅ |
| **V3** — Rappels à l'heure prévue et publication assistée (texte copié, vidéo prête, page d'envoi), confirmée par le lien public | ✅ |
| **V3** — Connexion d'une chaîne YouTube (OAuth, jeton dans le coffre Windows) et mise en ligne automatique : envoi reprenable, programmation par YouTube (`publishAt`), aucun doublon — voir [docs/YOUTUBE.md](docs/YOUTUBE.md) | ✅ |
| **V3** — TikTok : connexion (Login Kit ordinateur, PKCE), publication directe par blocs, suivi du traitement, statistiques — voir [docs/TIKTOK.md](docs/TIKTOK.md) | ✅ |
| **V3** — Instagram (Reels, compte professionnel), Facebook (Reels de Page), X : connexion, publication automatique, statistiques — voir [docs/META.md](docs/META.md), [docs/X.md](docs/X.md) | ✅ |
| **V4** — Analytiques : comparaison par concept, plateforme, compte et vidéo (vues, j'aime, commentaires, partages, engagement) ; « — » quand un chiffre n'est pas fourni | ✅ |
| **V4** — Relevé automatique des statistiques YouTube (API), saisie manuelle pour les autres plateformes, historique horodaté | ✅ |
| **V4** — YouTube Analytics : durée moyenne vue, % moyen vu, minutes vues, courbe de rétention par vidéo | ✅ |
| **V4** — Statistiques automatiques YouTube, TikTok, Instagram, X ; saisie manuelle ailleurs | ✅ |

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

Les 96 tests couvrent la base et ses migrations, ainsi que les scénarios D (Poussey Pass) et E (Pokémon).
Ils couvrent aussi les tâches, l'import, l'interface, la signature des mises à jour, le flux appcast
et de vrais exports FFmpeg (formats, modes de cadrage, musique, normalisation, vérification),
les scénarios A et G complets du cahier des charges et une transcription Whisper réelle (intégration continue).

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
