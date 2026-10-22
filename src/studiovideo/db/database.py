"""Accès SQLite, migrations versionnées et sauvegardes.

- Les migrations sont des fichiers NNNN_nom.sql appliqués dans l'ordre.
- La version courante est stockée dans PRAGMA user_version.
- Avant toute migration, la base est sauvegardée (API backup de SQLite).
- Chaque migration s'exécute dans une transaction : en cas d'échec,
  la base reste à la version précédente.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)

_MIGRATION_RE = re.compile(r"^(\d{4})_[\w-]+\.sql$")
MAX_BACKUPS = 20
MAX_DAILY = 7          # sauvegardes automatiques quotidiennes conservées (en plus des autres)


class MigrationError(RuntimeError):
    pass


def available_migrations() -> list[tuple[int, str, str]]:
    """Liste (version, nom, sql) triée."""
    pkg = resources.files("studiovideo.db") / "migrations"
    out = []
    for entry in pkg.iterdir():
        m = _MIGRATION_RE.match(entry.name)
        if m:
            out.append((int(m.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    out.sort()
    versions = [v for v, _, _ in out]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f"Numérotation des migrations incohérente : {versions}")
    return out


def latest_schema_version() -> int:
    migs = available_migrations()
    return migs[-1][0] if migs else 0


class Database:
    def __init__(self, path: Path, backups_dir: Path | None = None):
        self.path = Path(path)
        self.backups_dir = Path(backups_dir) if backups_dir else self.path.parent / "backups"
        self._local = threading.local()
        self._write_lock = threading.RLock()

    # -- connexions (une par thread) -------------------------------------
    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None,
                                   check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        with self._write_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return self.connect().execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        return self.connect().execute(sql, params).fetchone()

    # -- version / migrations --------------------------------------------
    def schema_version(self) -> int:
        return self.connect().execute("PRAGMA user_version").fetchone()[0]

    def migrate(self) -> list[int]:
        """Applique les migrations manquantes. Retourne les versions appliquées."""
        current = self.schema_version()
        migs = available_migrations()
        target = migs[-1][0] if migs else 0
        if current > target:
            raise MigrationError(
                f"La base (v{current}) est plus récente que ce programme (v{target}). "
                "Installez une version plus récente ou restaurez une sauvegarde.")
        pending = [m for m in migs if m[0] > current]
        if not pending:
            return []
        if current > 0:
            self.backup(reason=f"avant-migration-v{current}-vers-v{target}")
        conn = self.connect()
        applied = []
        for version, name, sql in pending:
            log.info("Migration %s", name)
            with self._write_lock:
                try:
                    # executescript commit implicitement : on encadre explicitement.
                    conn.executescript(f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;")
                except Exception as exc:
                    try:
                        conn.execute("ROLLBACK")
                    except sqlite3.OperationalError:
                        pass
                    raise MigrationError(f"Échec de la migration {name} : {exc}") from exc
            applied.append(version)
        return applied

    # -- sauvegardes -----------------------------------------------------
    def backup(self, reason: str = "manuelle") -> Path:
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        safe = re.sub(r"[^\w.-]", "_", reason)
        dest = self.backups_dir / f"studio-{stamp}-{safe}.db"
        src = self.connect()
        dst = sqlite3.connect(dest)
        try:
            with self._write_lock:
                src.backup(dst)
        finally:
            dst.close()
        self._prune_backups()
        log.info("Sauvegarde créée : %s", dest)
        return dest

    def list_backups(self) -> list[Path]:
        if not self.backups_dir.exists():
            return []
        return sorted(self.backups_dir.glob("studio-*.db"), reverse=True)

    def daily_backup(self, min_age_h: float = 20.0) -> Path | None:
        """Sauvegarde automatique, au plus une par jour (au démarrage). None si inutile."""
        last = next(iter(sorted(self.backups_dir.glob("studio-*-quotidienne.db"), reverse=True)), None) \
            if self.backups_dir.exists() else None
        if last is not None:
            try:
                made = datetime.strptime(last.name[7:22], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
            except ValueError:
                made = None
            if made and (datetime.now(timezone.utc) - made).total_seconds() < min_age_h * 3600:
                return None
        return self.backup("quotidienne")

    def _prune_backups(self) -> None:
        daily = [b for b in self.list_backups() if b.name.endswith("-quotidienne.db")]
        for old in daily[MAX_DAILY:]:
            try:
                old.unlink()
            except OSError:
                pass
        for old in [b for b in self.list_backups() if not b.name.endswith("-quotidienne.db")][MAX_BACKUPS:]:
            try:
                old.unlink()
            except OSError:
                pass

    def restore(self, backup_path: Path) -> None:
        """Remplace la base courante par une sauvegarde (après sauvegarde de sécurité)."""
        backup_path = Path(backup_path)
        if not backup_path.exists():
            raise FileNotFoundError(backup_path)
        self.backup(reason="avant-restauration")
        src = sqlite3.connect(backup_path)
        try:
            with self._write_lock:
                src.backup(self.connect())
        finally:
            src.close()

    def integrity_ok(self) -> bool:
        return self.connect().execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def audit(self, action: str, detail: str = "") -> None:
        self.connect().execute("INSERT INTO audit_log(action, detail) VALUES (?, ?)", (action, detail))


def open_database(path: Path, backups_dir: Path | None = None) -> Database:
    db = Database(path, backups_dir)
    db.migrate()
    return db
