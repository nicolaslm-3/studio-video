import sqlite3

import pytest

from studiovideo.db import database as dbmod
from studiovideo.db.database import Database, MigrationError, latest_schema_version


def test_fresh_database_is_migrated(db):
    assert db.schema_version() == latest_schema_version() >= 1
    assert db.integrity_ok()
    tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("concept", "social_account", "concept_account", "project", "source_asset",
              "transcript", "clip_candidate", "clip", "video_variant", "export_job",
              "master_publication", "publication", "analytics_snapshot", "preset", "job"):
        assert t in tables


def test_migrate_is_idempotent(db):
    assert db.migrate() == []


def test_backup_and_restore(db):
    db.connect().execute("INSERT INTO workspace(name) VALUES ('A')")
    b = db.backup("test")
    assert b.exists()
    db.connect().execute("INSERT INTO workspace(name) VALUES ('B')")
    db.restore(b)
    names = [r["name"] for r in db.query("SELECT name FROM workspace")]
    assert names == ["A"]
    # une sauvegarde de sécurité a été faite avant restauration
    assert any("avant-restauration" in p.name for p in db.list_backups())


def test_newer_database_is_refused(paths):
    d = Database(paths.database, paths.backups)
    d.migrate()
    d.connect().execute(f"PRAGMA user_version = {latest_schema_version() + 1}")
    with pytest.raises(MigrationError):
        d.migrate()


def test_failed_migration_rolls_back_and_backs_up(paths, monkeypatch):
    d = Database(paths.database, paths.backups)
    d.migrate()
    v = d.schema_version()
    real = dbmod.available_migrations()
    broken = real + [(v + 1, f"{v+1:04d}_broken.sql", "CREATE TABLE ok_t(x); THIS IS NOT SQL;")]
    monkeypatch.setattr(dbmod, "available_migrations", lambda: broken)
    with pytest.raises(MigrationError):
        d.migrate()
    assert d.schema_version() == v
    names = {r["name"] for r in d.query("SELECT name FROM sqlite_master")}
    assert "ok_t" not in names
    assert d.list_backups(), "une sauvegarde doit précéder la migration"


def test_foreign_keys_enforced(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.connect().execute("INSERT INTO project(concept_id, name) VALUES (999, 'x')")
