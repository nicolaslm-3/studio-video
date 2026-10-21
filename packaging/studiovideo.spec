# PyInstaller — build « onedir » (démarrage rapide, mises à jour fichier par fichier possibles).
# Lancer depuis la racine du dépôt : pyinstaller packaging/studiovideo.spec --noconfirm
# Les dossiers vendor/ffmpeg et vendor/winsparkle sont préparés par le workflow de compilation.
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"

# Fichiers de migration SQL (indispensables : la base ne peut pas être créée sans eux)
datas = [(str(SRC / "studiovideo" / "db" / "migrations" / "*.sql"), "studiovideo/db/migrations")]
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

a = Analysis(
    [str(SRC / "studiovideo" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["keyring.backends.Windows", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
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
