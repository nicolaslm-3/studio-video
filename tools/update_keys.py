"""Clés de signature des mises à jour (EdDSA / ed25519, format Sparkle/WinSparkle).

Usage :
  python tools/update_keys.py generate --out private.key
      -> écrit la clé PRIVÉE (base64 de la graine 32 octets) et affiche la clé PUBLIQUE.
         Conservez la clé privée hors du dépôt (secret GitHub UPDATE_PRIVATE_KEY).
  python tools/update_keys.py sign --key private.key Setup.exe
      -> affiche la signature base64 et la taille, à placer dans l'appcast.
  python tools/update_keys.py verify --pub <clé publique> --sig <signature> Setup.exe

La clé privée peut aussi être fournie par la variable d'environnement UPDATE_PRIVATE_KEY.
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def _raw_pub(priv: Ed25519PrivateKey) -> bytes:
    return priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def load_private(text: str) -> Ed25519PrivateKey:
    raw = base64.b64decode(text.strip())
    if len(raw) == 64:          # format Sparkle « graine + publique »
        raw = raw[:32]
    if len(raw) != 32:
        raise ValueError("Clé privée invalide (32 octets attendus).")
    return Ed25519PrivateKey.from_private_bytes(raw)


def generate() -> tuple[str, str]:
    k = Ed25519PrivateKey.generate()
    seed = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                           serialization.NoEncryption())
    return base64.b64encode(seed).decode(), base64.b64encode(_raw_pub(k)).decode()


def public_from_private(text: str) -> str:
    return base64.b64encode(_raw_pub(load_private(text))).decode()


def sign_file(path: Path, private_text: str) -> tuple[str, int]:
    data = path.read_bytes()
    sig = load_private(private_text).sign(data)
    return base64.b64encode(sig).decode(), len(data)


def verify_file(path: Path, public_b64: str, sig_b64: str) -> bool:
    pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64))
    try:
        pub.verify(base64.b64decode(sig_b64), path.read_bytes())
        return True
    except InvalidSignature:
        return False


def _private_text(arg: str | None) -> str:
    if arg:
        return Path(arg).read_text(encoding="ascii")
    env = os.environ.get("UPDATE_PRIVATE_KEY")
    if not env:
        sys.exit("Clé privée absente : --key fichier ou variable UPDATE_PRIVATE_KEY.")
    return env


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--out", required=True)
    s = sub.add_parser("sign")
    s.add_argument("--key")
    s.add_argument("file")
    v = sub.add_parser("verify")
    v.add_argument("--pub", required=True)
    v.add_argument("--sig", required=True)
    v.add_argument("file")
    p = sub.add_parser("public")
    p.add_argument("--key")
    a = ap.parse_args()
    if a.cmd == "generate":
        out = Path(a.out)
        if out.exists():
            sys.exit(f"{out} existe déjà : refus d'écraser une clé.")
        priv, pub = generate()
        out.write_text(priv, encoding="ascii")
        print(f"Clé privée écrite dans {out} (à garder secrète et sauvegardée).")
        print(f"Clé publique : {pub}")
    elif a.cmd == "sign":
        sig, n = sign_file(Path(a.file), _private_text(a.key))
        print(f'sparkle:edSignature="{sig}" length="{n}"')
    elif a.cmd == "verify":
        ok = verify_file(Path(a.file), a.pub, a.sig)
        print("Signature valide" if ok else "SIGNATURE INVALIDE")
        sys.exit(0 if ok else 1)
    elif a.cmd == "public":
        print(public_from_private(_private_text(a.key)))


if __name__ == "__main__":
    main()
