-- Migration 0004 : doublages (V2, §10.2 et §11)
CREATE TABLE dub (
    id               INTEGER PRIMARY KEY,
    source_asset_id  INTEGER NOT NULL REFERENCES source_asset(id) ON DELETE CASCADE,
    language         TEXT NOT NULL,
    engine           TEXT NOT NULL DEFAULT '',
    directory        TEXT NOT NULL,
    manifest_json    TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (source_asset_id, language)
);
