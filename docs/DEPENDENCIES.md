# Dépendances et licences

## Embarquées dans la V1 (jalon 1)

| Composant | Rôle | Licence | Remarques de redistribution |
|---|---|---|---|
| Python 3.12 | interpréteur (embarqué par PyInstaller) | PSF | libre |
| PySide6 / Qt 6 | interface, lecteur | LGPL v3 | Qt chargé en DLL dynamiques (mode `onedir`) : remplaçables par l'utilisateur ; fournir la licence et un lien vers les sources de Qt |
| keyring | Gestionnaire d'identifiants Windows | MIT | libre |
| FFmpeg / FFprobe (build BtbN « gpl ») | analyse, miniatures, futur export | GPL v3 | **à valider** : exécutables séparés appelés en processus (pas de liaison) ; fournir la licence et l'offre des sources. Alternative : build « lgpl » + encodeurs matériels / Media Foundation (`h264_mf`), sans libx264 |
| WinSparkle | mises à jour | MIT | libre |
| yt-dlp (optionnel) | import par URL | Unlicense | se met à jour souvent ; prévoir sa mise à jour indépendante |
| faster-whisper | transcription locale | MIT | modèles Whisper (MIT) téléchargés après installation, jamais embarqués |
| CTranslate2 | moteur d'inférence | MIT | inclut oneDNN (Apache 2.0) |
| onnxruntime | détection de parole (Silero VAD, MIT) | MIT | |
| PyAV | décodage audio pour faster-whisper | BSD | embarque des bibliothèques FFmpeg (LGPL) |
| tokenizers, huggingface_hub | découpage du texte, téléchargement des modèles | Apache 2.0 | |

## Outils de compilation (non redistribués)

PyInstaller (GPL avec exception de bootloader : l'exécutable produit n'est pas soumis à la GPL),
Inno Setup (licence libre propre), cryptography (Apache 2.0 / BSD), pytest, pytest-qt.

## Prévues (évaluation à faire au moment de l'intégration)

| Composant | Version | Licence | Point d'attention |
|---|---|---|---|
| WhisperX / pyannote.audio | V2 | BSD / MIT | modèles pyannote soumis à conditions d'accès Hugging Face |
| Argos Translate | V2 | MIT | modèles par paire de langues |
| Demucs | V2 | MIT | PyTorch volumineux : installation à la demande |
| DeepFilterNet | V2 | MIT / Apache 2.0 | |
| pyloudnorm | V1 j2 | MIT | |
| PaddleOCR | V2 | Apache 2.0 | |
| Piper | V2 | MIT (voix : licences variables) | vérifier la licence de chaque voix |
| ElevenLabs, DeepL, Google Translation | V2 | services payants | clé de l'utilisateur, consentement avant envoi |
| MediaPipe | V1 j2 | Apache 2.0 | suivi de visage |
