-- sans-cles-etrangeres
-- Migration 0007 : onglet « Textes » (mode 'texts'). SQLite ne modifie pas une contrainte CHECK : la table des
-- projets est reconstruite à l'identique (mêmes identifiants, mêmes données) avec la nouvelle liste de modes.
CREATE TABLE project_new (
    id          INTEGER PRIMARY KEY,
    concept_id  INTEGER NOT NULL REFERENCES concept(id) ON DELETE RESTRICT,
    name        TEXT NOT NULL,
    mode        TEXT NOT NULL DEFAULT 'free_edit'
                CHECK (mode IN ('smart_clipping','translation','original','free_edit','texts')),
    status      TEXT NOT NULL DEFAULT 'active',
    data_json   TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
INSERT INTO project_new(id, concept_id, name, mode, status, data_json, created_at, updated_at)
    SELECT id, concept_id, name, CASE WHEN mode IN ('campaign', 'batch') THEN 'free_edit' ELSE mode END, status,
           data_json, created_at, updated_at FROM project;
DROP TABLE project;
ALTER TABLE project_new RENAME TO project;
