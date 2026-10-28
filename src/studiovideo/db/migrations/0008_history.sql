-- Migration 0008 : historique des transcriptions, des traductions et des voix doublées. Chaque nouvelle
-- génération est gardée ; celle qui est appliquée reste dans les tables transcript, translation et dub.
CREATE TABLE version_history (
    id               INTEGER PRIMARY KEY,
    source_asset_id  INTEGER NOT NULL REFERENCES source_asset(id) ON DELETE CASCADE,
    kind             TEXT NOT NULL CHECK (kind IN ('transcript', 'translation', 'dub')),
    language         TEXT NOT NULL DEFAULT '',
    engine           TEXT NOT NULL DEFAULT '',
    label            TEXT NOT NULL DEFAULT '',
    data_json        TEXT NOT NULL DEFAULT '',
    directory        TEXT NOT NULL DEFAULT '',
    active           INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX version_history_asset ON version_history(source_asset_id, kind, language);
-- ce qui existe déjà devient la première version de son historique
INSERT INTO version_history(source_asset_id, kind, language, engine, data_json, active, created_at)
    SELECT source_asset_id, 'transcript', '', engine, COALESCE(edited_json, original_json), 1, created_at
    FROM transcript;
INSERT INTO version_history(source_asset_id, kind, language, engine, data_json, active, created_at)
    SELECT source_asset_id, 'translation', language, engine, COALESCE(edited_json, original_json), 1, created_at
    FROM translation;
INSERT INTO version_history(source_asset_id, kind, language, engine, data_json, directory, active, created_at)
    SELECT source_asset_id, 'dub', language, engine, manifest_json, directory, 1, created_at FROM dub;
