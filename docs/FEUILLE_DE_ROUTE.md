# Feuille de route : Clipping et Traduction

Ce document répond au cahier des charges « Studio vidéo IA ». Il contient l'analyse des deux logiciels de
référence, l'état réel du projet par rapport à la demande, l'architecture, le plan de développement et ce qui a
été fait puis vérifié à cette étape.

## 1. Logiciels de référence (examinés en lecture seule)

Rien n'a été modifié, copié ou déplacé dans ces deux dossiers.

### ClipFlow : `Desktop\ClipFlow-source` (code source lisible)

Vérifié dans le code :

- **Technologie** : programme en Go, avec une interface web locale (`ui/index.html`, `app.js`). Outils externes :
  FFmpeg, yt-dlp, whisper.cpp (transcription) et llama.cpp (IA locale facultative).
- **Chaîne de traitement** (`pipeline.go`) : téléchargement → extraction du son en 16 kHz → transcription mot par
  mot (`-ml 1`) → calcul de l'énergie sonore toutes les 100 ms → analyse → rendu des clips.
- **Détection des moments** (`highlights.go`) :
  - découpage en phrases (ponctuation, silence de plus de 0,9 s, ou 40 mots au maximum) ;
  - note de chaque phrase :
    - question ;
    - exclamation ;
    - chiffres ;
    - « mots forts » (secret, jamais, argent…) ;
    - débuts accrocheurs (« le problème », « personne ne ») ;
    - adresse directe (« tu », « vous ») ;
    - **volume sonore comparé à la moyenne** (écart-type) ;
    - débit de parole ;
  - pénalités : phrase qui commence par « et », « mais » ou un numéro de liste (début en milieu d'idée) ;
  - choix des fenêtres : accroche forte au début, phrase finale complète, durée proche de 40 s, aucun chevauchement
    entre deux clips.
- **IA locale** (`localllm.go`) : pour chaque passage, le modèle reçoit les phrases numérotées et renvoie en JSON
  une note sur 10, les bornes début/fin, un titre, une accroche, une légende, des hashtags et une justification.
  Les bornes ne sont retenues que si la durée reste valide. Si l'IA échoue, l'algorithme seul est utilisé et
  l'utilisateur en est informé.
- **Ajustement des bornes** (`moments.go`) : début et fin calés sur les mots, avec une petite marge.

À retenir pour nous :

- la note mixte « algorithme et IA » (65 % IA, 35 % algorithme) ;
- le refus explicite quand rien n'est trouvé ;
- l'alignement des clips sur les mots.

Studio Vidéo applique déjà ces principes (`ai/highlights.py`), avec en plus :

- des catégories ;
- les intervenants ;
- une demande en langage naturel ;
- l'intensité ;
- le contexte des déclarations.

### Dubber : `AppData\Local\Programs\Dubber` (programme compilé, pas de code source)

Seule la liste des bibliothèques et des ressources fournies avec le programme est observable :

- Python 3.12 et PySide6 (application Qt, comme la nôtre), emballée avec PyInstaller ;
- `ctranslate2` : moteur de faster-whisper, très probablement utilisé pour la transcription ;
- `resources/models/UVR-MDX-NET-Inst_HQ_3.onnx` et `onnxruntime` : **séparation voix / musique** (modèle UVR MDX) ;
- `rapidocr_onnxruntime` (modèles PP-OCRv4) et `cv2` (OpenCV) : **détection du texte incrusté** par OCR ;
- `sentencepiece` et `google` (protobuf) : outils de traduction ou de modèles de langue ;
- FFmpeg fourni dans `resources/ffmpeg` ;
- la police Montserrat pour les sous-titres.

**Non vérifiable** : le déroulé exact de l'interface, le choix des voix et les moteurs de traduction. Le code de
l'application est compilé dans `Dubber.exe` ; il n'a pas été décompilé.

**Ce que l'on peut supposer sans l'affirmer** : un parcours « transcrire → traduire → doubler → mixer avec la musique
séparée → sous-titrer », et un retrait du texte d'origine par OCR.

Studio Vidéo dispose déjà de chaque brique équivalente :

- `edit/separate.py` : séparation voix / musique ;
- `edit/textdetect.py` : détection du texte incrusté ;
- `edit/dubbing.py` : doublage ;
- `ai/translate.py` : traduction.

## 2. État du projet par rapport au cahier des charges

Le projet existant est conservé : environ 21 000 lignes et plus de 200 tests automatiques.

| Demande | État |
|---|---|
| Transcription automatique à chaque import (règle absolue) | ✅ **fait à cette étape** (voir §5) |
| Langue détectée, mots horodatés, correction, recherche, clic → passage, cache | ✅ déjà présent |
| « Aucune parole » : pas de texte inventé | ✅ (message clair, pas d'échec) |
| Lecteur : image par image, volume, forme d'onde, zoom timeline, entrée/sortie | ✅ |
| Clipping : catégories humour, débat, déclarations (politique), émotion, pédagogie, potentiel | ✅ |
| Clipping : **réactions des commentateurs** (cris, sans phrase remarquable) | ❌ à faire (phase 5) |
| Clipping : mots-clés, demande libre, intervenants, intensité, durées, nombre | ✅ |
| Clipping : création manuelle d'extraits, liste, export multiple | ✅ |
| Titres, description, hashtags, accroche | ✅ |
| Traduction : DeepL, Google, LLM local (Ollama) | ✅ |
| Traduction : **Argos Translate** (100 % local, gratuit) | ❌ à faire (phase 6) |
| ElevenLabs (clé dans le coffre Windows, voix, aperçu, régénération d'une phrase) + Piper local | ✅ |
| Sous-titres : couleurs, contour, fond, mots max, lignes, animations (mot par mot, surlignage, pop, fondu) | ✅ |
| Recadrage auto sur les visages (lissé, réglable) | ✅ |
| **Double cadrage vertical** (deux vues d'une même source, un seul son) | ✅ **fait à cette étape** |
| **Deux vidéos haut / bas** (décalage, son choisi) | ✅ **fait à cette étape** |
| **Image dans l'image** (position, taille, contour) | ✅ **fait à cette étape** (sans coins arrondis) |
| Suivi des visages **dans chaque vue** du double cadrage | ❌ à faire (phase 4 bis) |
| Images-clés de cadrage (changer le cadre à un instant donné) | ❌ à faire |
| Optimiser l'audio, séparation voix / musique, normalisation | ✅ |
| Masquage du texte incrusté (flou, effacement, bande) + détection | ✅ |
| Export MP4 H.264 / AAC, SRT, VTT, ASS, file d'exports annulable | ✅ |
| Installateur Windows et mises à jour automatiques | ✅ |

### Point à trancher : la navigation

Le cahier des charges décrit une barre latérale avec Clipping, Traduction, Projets, Bibliothèque et Paramètres.
Le logiciel actuel a un dock en bas avec quatre parcours : Montage libre, Clipping, Traduction et Création
originale.

Rien n'a été retiré : la décision revient au propriétaire. Il peut garder les quatre parcours, ou ne garder que
Clipping et Traduction en mettant Montage libre et Création originale de côté.

## 3. Architecture

Les deux espaces partagent les mêmes moteurs. Aucun moteur n'est dupliqué.

```
Interface (ui/)                       aucune logique métier
  ├─ Studio : lecteur commun, transcription, moments forts, onglets Cadrage / Textes / Sous-titres / Audio / Export
  └─ espaces : Clipping, Traduction (mêmes composants, outils différents)
Services (services/)                  projets, bibliothèque, transcriptions, candidats, doublages
Tâches (jobs/manager.py)              tout calcul long en arrière-plan, progression, annulation, relance
Moteurs (sans Qt, testés seuls)
  ├─ media/      import, analyse FFmpeg, téléchargement par lien (yt-dlp), aperçus légers
  ├─ transcribe/ faster-whisper (langue détectée, mots horodatés)
  ├─ ai/         moments forts, traduction, voix (ElevenLabs, Piper), intervenants
  ├─ edit/       montage (description), rendu FFmpeg, mise en page, recadrage, sous-titres, audio
  └─ publish/    plateformes (préparé pour plus tard)
Base SQLite (%LOCALAPPDATA%\StudioVideo)  migrations versionnées, conservée lors des mises à jour
```

**Composants communs à Clipping et Traduction** :

- lecteur du montage ;
- panneau de transcription ;
- recadrage et mise en page ;
- sous-titres ;
- textes ;
- audio ;
- export ;
- file de tâches.

**Propres à chaque espace** :

- Clipping : moments forts et export des clips ;
- Traduction : traduction, doublage et sous-titres traduits.

## 4. Double cadrage vertical : comment ça marche

1. La mise en page est une **description** enregistrée avec le projet (`VideoSettings.layout`). Le fichier source
   n'est jamais modifié. Elle contient :
   - le type (plein écran, double cadrage, deux vidéos, incrustation) ;
   - la proportion haut / bas ;
   - l'ordre des vues ;
   - pour chaque vue : son point central et son zoom ;
   - la seconde vidéo, son décalage et le son conservé.
2. Un seul module de géométrie (`edit/compose.py`) calcule les zones et le recadrage de chaque vue.
3. **À l'export**, FFmpeg lit la vidéo une fois et duplique **l'image seulement** (`split`). Chaque copie est
   recadrée dans sa zone, puis les deux sont empilées (`vstack`, ou `overlay` pour l'incrustation). Le son est
   traité à part, une seule fois. Il ne peut donc pas être entendu deux fois. Avec deux vidéos différentes, on
   choisit le son conservé : principal, second ou les deux.
4. **Dans l'aperçu en direct**, chaque image décodée est affichée dans les deux vues avec la même géométrie.
   On **glisse une vue à la souris** pour la déplacer et on **zoome à la molette**. Les réglages sont aussi dans
   l'onglet Cadrage :
   - disposition ;
   - vue réglée ;
   - zoom ;
   - positions ;
   - partage 50/50, 60/40, 40/60 ou libre ;
   - inversion haut / bas ;
   - seconde vidéo ;
   - décalage ;
   - son ;
   - incrustation et contour.
5. Le **suivi des visages** dans chaque vue réutilisera `edit/reframe.py`, qui fonctionne déjà pour le plein écran.
   Ce sera une trajectoire par vue.

## 5. Ce qui a été fait et vérifié à cette étape

- **Transcription automatique à chaque import**, fichier ou lien :
  - elle part en arrière-plan avec la langue détectée ;
  - elle utilise le modèle préféré, ou le meilleur modèle installé ;
  - elle n'est jamais lancée deux fois ;
  - elle se désactive dans Paramètres → Général ;
  - sans modèle installé, un message le dit clairement ;
  - sans parole, le message « aucune parole exploitable », sans échec et sans texte inventé ;
  - la progression s'affiche dans le panneau de transcription ;
  - le montage manuel reste possible pendant la transcription.
  - Au passage, j'ai corrigé un défaut : la liste des moments forts ne se rafraîchissait pas après une
    transcription.
- **Double cadrage, deux vidéos, image dans l'image** : moteur, réglages, aperçu en direct et déplacement à la
  souris.
- **Tests ajoutés** (vrais rendus FFmpeg, pas de simulation) :
  - `tests/test_compose.py` :
    - débat simulé, intervenant A (rouge) à gauche et B (bleu) à droite ;
    - vérifié : A en haut et B en bas, inversion, 70/30, un seul flux audio, export 9:16 vérifié, deux vidéos,
      incrustation avec contour ;
  - `tests/test_jobs_import.py` : transcription automatique (lancée, langue auto, modèle choisi, désactivable,
    message sans modèle) ;
  - `tests/test_ui_smoke.py` : réglages du panneau, deux vues dans l'aperçu, déplacement à la souris, sauvegarde.
- **Vérification de fidélité** : une vraie vidéo a été décodée par le lecteur Qt et affichée en double cadrage.
  L'aperçu en direct a été comparé point par point à l'image calculée par FFmpeg pour l'export : **0 différence
  sur 32 400 points**.

## 6. Plan de la suite (chaque étape laisse le logiciel utilisable)

1. **Phase 4 bis : suivi des visages dans chaque vue** du double cadrage. Pour un débat : visage de gauche en
   haut, visage de droite en bas, avec le choix de la personne à suivre.
2. **Images-clés de cadrage** : changer le cadrage à un instant donné, avec une transition douce.
3. **Phase 5 : catégorie « Réactions des commentateurs »**. Pics de volume dans la bande de la voix (300 Hz à
   3 kHz), comparés au bruit de fond, pour ne pas confondre avec les sons du jeu. Elle utilisera la voix séparée
   quand elle existe.
4. **Phase 6 : Argos Translate** (traduction 100 % locale et gratuite), avec la liste des langues installées et
   l'indication des coûts des moteurs payants.
5. Coins arrondis pour l'incrustation, mise en page par extrait et non pour tout le montage.
6. Navigation : à décider (voir §2).

## 7. Comment tester soi-même

1. Lancer le logiciel, ouvrir **Montage libre** ou **Clipping**, importer une vidéo de débat horizontale.
   La transcription démarre seule et sa progression s'affiche dans le panneau Transcription.
2. Ajouter la vidéo au montage, onglet **Cadrage** → **Mise en page** → « Double cadrage (même vidéo) ».
3. Dans l'aperçu, glisser la vue du haut sur l'intervenant A et celle du bas sur B ; molette pour zoomer.
4. Bouton œil (« Aperçu exact de l'export ») pour voir l'image calculée, puis **Exporter** : le fichier fait
   1080 × 1920, avec un seul son.
