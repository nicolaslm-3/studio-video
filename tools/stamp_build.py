"""Prépare une compilation de distribution.

- vérifie que le tag Git (vX.Y.Z) correspond à studiovideo.__version__ ;
- injecte la clé PUBLIQUE de vérification et l'URL du flux de mises à jour
  dans studiovideo/update/config.py ;
- écrit packaging/version.iss (lu par Inno Setup).

Usage : python tools/stamp_build.py --tag v0.2.0 --base-url https://u.github.io/studio-video
        (la clé publique est dérivée de UPDATE_PRIVATE_KEY, ou passée avec --public-key)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))


def main() -> None:
    from studiovideo import __version__
    from update_keys import public_from_private

    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--public-key", default="")
    a = ap.parse_args()

    if a.tag and a.tag.lstrip("v") != __version__:
        sys.exit(f"Le tag {a.tag} ne correspond pas à la version du code ({__version__}).")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-(?:beta|rc)\.\d+)?", __version__):
        sys.exit(f"Numéro de version invalide : {__version__}")

    pub = a.public_key
    if not pub and os.environ.get("UPDATE_PRIVATE_KEY"):
        pub = public_from_private(os.environ["UPDATE_PRIVATE_KEY"])
    cfg = ROOT / "src" / "studiovideo" / "update" / "config.py"
    text = cfg.read_text(encoding="utf-8")
    text = re.sub(r'^PUBLIC_KEY = ".*"$', f'PUBLIC_KEY = "{pub}"', text, flags=re.M)
    text = re.sub(r'^BASE_URL = ".*"$', f'BASE_URL = "{a.base_url}"', text, flags=re.M)
    cfg.write_text(text, encoding="utf-8")

    numeric = __version__.split("-")[0]
    (ROOT / "packaging" / "version.iss").write_text(
        f'#define AppVersion "{__version__}"\n#define AppVersionNumeric "{numeric}.0"\n', encoding="utf-8")
    print(f"Version {__version__} · mises à jour {'activées' if pub and a.base_url else 'DÉSACTIVÉES'}")


if __name__ == "__main__":
    main()
