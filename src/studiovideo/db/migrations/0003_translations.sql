-- Migration 0003 : traductions des transcriptions (V2, §10)
CREATE TABLE translation (
    id               INTEGER PRIMARY KEY,
    source_asset_id  INTEGER NOT NULL REFERENCES source_asset(id) ON DELETE CASCADE,
    language         TEXT NOT NULL,
    engine           TEXT NOT NULL DEFAULT '',
    original_json    TEXT NOT NULL,
    edited_json      TEXT,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (source_asset_id, language)
);
