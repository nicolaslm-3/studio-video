# PyInstaller — build « onedir » (démarrage rapide, mises à jour fichier par fichier possibles).
# Lancer depuis la racine du dépôt : pyinstaller packaging/studiovideo.spec --noconfirm
# Les dossiers vendor/ffmpeg et vendor/winsparkle sont préparés par le workflow de compilation.
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"

# Fichiers de migration SQL (indispensables : la base ne peut pas être créée sans eux)
datas = [(str(SRC / "studiovideo" / "db" / "migrations" / "*.sql"), "studiovideo/db/migrations"),
         (str(SRC / "studiovideo" / "ui" / "icons" / "*"), "studiovideo/ui/icons"),
         (str(SRC / "studiovideo" / "ui" / "assets" / "*"), "studiovideo/ui/assets")]
binaries = []
for name in ("ffmpeg.exe", "ffprobe.exe"):
    p = ROOT / "vendor" / "ffmpeg" / name
    if p.exists():
        binaries.append((str(p), "ffmpeg"))
lic = ROOT / "vendor" / "ffmpeg" / "LICENSE.txt"
if lic.exists():
    datas.append((str(lic), "ffmpeg"))
ws = ROOT / "vendor" / "winsparkle" / "WinSparkle.dll"
if ws.exists():
    binaries.append((str(ws), "."))

# Transcription : moteur CTranslate2 (DLL natives), faster-whisper (modèle VAD Silero), onnxruntime, PyAV.
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules
for pkg in ("faster_whisper", "ctranslate2", "onnxruntime", "av", "tokenizers", "yt_dlp_ejs"):
    try:
        datas += collect_data_files(pkg)
        binaries += collect_dynamic_libs(pkg)
    except Exception as exc:  # paquet absent : l'application le signalera comme indisponible
        print(f"[spec] {pkg} : {exc}")
asr_hidden = []
for pkg in ("faster_whisper", "ctranslate2", "huggingface_hub", "yt_dlp", "yt_dlp_ejs"):
    try:
        asr_hidden += collect_submodules(pkg)
    except Exception:
        pass

a = Analysis(
    [str(SRC / "studiovideo" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["keyring.backends.Windows", "pypdf", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
                   "PySide6.QtSvg"] + asr_hidden,
    # Paquets inutiles à l'exécution (tirés par l'environnement de compilation) : allège l'installateur.
    excludes=["tkinter", "torch", "matplotlib", "IPython", "pytest", "scipy", "pandas", "PIL", "lxml", "sympy",
              "cryptography", "yaml", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtQuickWidgets",
              "PySide6.QtQuick3D", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtSql", "PySide6.QtTest",
              "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors",
              "PySide6.QtSerialPort", "PySide6.QtWebSockets", "PySide6.QtWebChannel", "PySide6.QtScxml", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick3D", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = ROOT / "packaging" / "icon.ico"
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="StudioVideo",
    console=False,
    icon=str(icon) if icon.exists() else None,
    version=str(ROOT / "packaging" / "file_version.txt") if (ROOT / "packaging" / "file_version.txt").exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="StudioVideo")
