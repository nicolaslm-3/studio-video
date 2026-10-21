"""Stockage des secrets (clés API, jetons OAuth).

Utilise le Gestionnaire d'identifiants Windows via `keyring`.
Aucun secret n'est écrit en clair dans la base ou dans des fichiers.
Si aucun coffre sécurisé n'est disponible, l'enregistrement est refusé.
"""
from __future__ import annotations

import logging

from .. import __app_name__

log = logging.getLogger(__name__)
SERVICE = f"{__app_name__}"


class SecretStoreUnavailable(RuntimeError):
    pass


def _backend():
    try:
        import keyring
        from keyring.backends import fail
    except ImportError as exc:  # pragma: no cover
        raise SecretStoreUnavailable("Module keyring absent.") from exc
    kr = keyring.get_keyring()
    if isinstance(kr, fail.Keyring) or "fail" in type(kr).__module__ or "null" in type(kr).__module__:
        raise SecretStoreUnavailable("Aucun coffre d'identifiants sécurisé n'est disponible sur ce système.")
    return keyring


def set_secret(name: str, value: str) -> None:
    _backend().set_password(SERVICE, name, value)
    log.info("Secret enregistré : %s", name)  # jamais la valeur


def get_secret(name: str) -> str | None:
    try:
        return _backend().get_password(SERVICE, name)
    except SecretStoreUnavailable:
        return None


def delete_secret(name: str) -> None:
    try:
        _backend().delete_password(SERVICE, name)
    except Exception:  # absent : rien à faire
        pass


def available() -> bool:
    try:
        _backend()
        return True
    except SecretStoreUnavailable:
        return False
