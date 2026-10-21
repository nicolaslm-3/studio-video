-- Migration 0001 : modèle relationnel initial (cahier des charges §3.4)

CREATE TABLE workspace (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE concept (
    id            INTEGER PRIMARY KEY,
    workspace_id  INTEGER NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    theme         TEXT NOT NULL DEFAULT '',
    color         TEXT NOT NULL DEFAULT '#8A8F98',
    language      TEXT NOT NULL DEFAULT 'fr',
    archived      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (workspace_id, name)
);

-- Un compte social existe indépendamment des concepts.
CREATE TABLE social_account (
    id                INTEGER PRIMARY KEY,
    workspace_id      INTEGER NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    platform          TEXT NOT NULL CHECK (platform IN ('youtube','tiktok','instagram','facebook','x')),
    name              TEXT NOT NULL,
    public_url        TEXT NOT NULL DEFAULT '',
    avatar_path       TEXT NOT NULL DEFAULT '',
    language          TEXT NOT NULL DEFAULT 'fr',
    connection_status TEXT NOT NULL DEFAULT 'disconnected'
                      CHECK (connection_status IN ('disconnected','connected','expired','revoked','error')),
    scopes            TEXT NOT NULL DEFAULT '',
    remote_id         TEXT NOT NULL DEFAULT '',
    settings_json     TEXT NOT NULL DEFAULT '{}',
    daily_limit       INTEGER,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Association volontaire et visible entre un concept et un compte.
CREATE TABLE concept_account (
    concept_id         INTEGER NOT NULL REFERENCES concept(id) ON DELETE CASCADE,
    social_account_id  INTEGER NOT NULL REFERENCES social_account(id) ON DELETE CASCADE,
    is_default         INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (concept_id, social_account_id)
);

CREATE TABLE project (
    id          INTEGER PRIMARY KEY,
    concept_id  INTEGER NOT NULL REFERENCES concept(id) ON DELETE RESTRICT,
    name        TEXT NOT NULL,
    mode        TEXT NOT NULL DEFAULT 'free_edit'
                CHECK (mode IN ('smart_clipping','translation','original','campaign','free_edit','batch')),
    status      TEXT NOT NULL DEFAULT 'active',
    data_json   TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Média source. Le fichier n'est jamais modifié (non destructif).
CREATE TABLE source_asset (
    id             INTEGER PRIMARY KEY,
    workspace_id   INTEGER NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    project_id     INTEGER REFERENCES project(id) ON DELETE SET NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('video','audio','image','document','other')),
    path           TEXT NOT NULL,
    file_name      TEXT NOT NULL,
    size_bytes     INTEGER NOT NULL DEFAULT 0,
    fingerprint    TEXT NOT NULL,
    duration_s     REAL,
    width          INTEGER,
    height         INTEGER,
    fps            REAL,
    video_codec    TEXT,
    audio_codec    TEXT,
    audio_tracks   INTEGER NOT NULL DEFAULT 0,
    probe_json     TEXT NOT NULL DEFAULT '{}',
    thumbnail_path TEXT NOT NULL DEFAULT '',
    origin_url     TEXT NOT NULL DEFAULT '',
    provenance     TEXT NOT NULL DEFAULT '',
    rights_note    TEXT NOT NULL DEFAULT '',
    tags           TEXT NOT NULL DEFAULT '',
    favorite       INTEGER NOT NULL DEFAULT 0,
    missing        INTEGER NOT NULL DEFAULT 0,
    imported_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (workspace_id, fingerprint)
);

CREATE TABLE transcript (
    id               INTEGER PRIMARY KEY,
    source_asset_id  INTEGER NOT NULL REFERENCES source_asset(id) ON DELETE CASCADE,
    language         TEXT,
    engine           TEXT NOT NULL DEFAULT '',
    original_json    TEXT NOT NULL DEFAULT '[]',   -- résultat brut, conservé
    edited_json      TEXT,                          -- corrections utilisateur
    speakers_json    TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE clip_candidate (
    id               INTEGER PRIMARY KEY,
    project_id       INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    source_asset_id  INTEGER NOT NULL REFERENCES source_asset(id) ON DELETE CASCADE,
    start_s          REAL NOT NULL,
    end_s            REAL NOT NULL,
    subject          TEXT NOT NULL DEFAULT '',
    category         TEXT NOT NULL DEFAULT '',
    score            REAL,
    rationale        TEXT NOT NULL DEFAULT '',
    hook             TEXT NOT NULL DEFAULT '',
    context_flags    TEXT NOT NULL DEFAULT '[]',
    state            TEXT NOT NULL DEFAULT 'proposed' CHECK (state IN ('proposed','accepted','rejected')),
    favorite         INTEGER NOT NULL DEFAULT 0,
    CHECK (end_s > start_s)
);

CREATE TABLE clip (
    id               INTEGER PRIMARY KEY,
    project_id       INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    source_asset_id  INTEGER REFERENCES source_asset(id) ON DELETE SET NULL,
    candidate_id     INTEGER REFERENCES clip_candidate(id) ON DELETE SET NULL,
    name             TEXT NOT NULL,
    start_s          REAL NOT NULL DEFAULT 0,
    end_s            REAL,
    edit_json        TEXT NOT NULL DEFAULT '{}',   -- timeline, recadrage, sous-titres...
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Une vidéo produite -> plusieurs variantes (langue, format...).
CREATE TABLE video_variant (
    id           INTEGER PRIMARY KEY,
    clip_id      INTEGER NOT NULL REFERENCES clip(id) ON DELETE CASCADE,
    label        TEXT NOT NULL,
    language     TEXT NOT NULL DEFAULT 'fr',
    aspect       TEXT NOT NULL DEFAULT '9:16',
    output_path  TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE export_job (
    id           INTEGER PRIMARY KEY,
    variant_id   INTEGER NOT NULL REFERENCES video_variant(id) ON DELETE CASCADE,
    job_id       INTEGER REFERENCES job(id) ON DELETE SET NULL,
    settings_json TEXT NOT NULL DEFAULT '{}',
    output_path  TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Publication maître : informations communes à toutes les destinations.
CREATE TABLE master_publication (
    id            INTEGER PRIMARY KEY,
    concept_id    INTEGER NOT NULL REFERENCES concept(id) ON DELETE RESTRICT,
    variant_id    INTEGER REFERENCES video_variant(id) ON DELETE SET NULL,
    title         TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    hashtags      TEXT NOT NULL DEFAULT '',
    thumbnail     TEXT NOT NULL DEFAULT '',
    language      TEXT NOT NULL DEFAULT 'fr',
    planned_at    TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Une publication = une destination (un compte) précise.
CREATE TABLE publication (
    id                 INTEGER PRIMARY KEY,
    master_id          INTEGER NOT NULL REFERENCES master_publication(id) ON DELETE CASCADE,
    social_account_id  INTEGER NOT NULL REFERENCES social_account(id) ON DELETE RESTRICT,
    variant_id         INTEGER REFERENCES video_variant(id) ON DELETE SET NULL,
    is_exceptional     INTEGER NOT NULL DEFAULT 0,   -- compte hors concept, confirmé
    title              TEXT,                         -- NULL = hérite de la publication maître
    description        TEXT,
    hashtags           TEXT,
    thumbnail          TEXT,
    scheduled_at       TEXT,
    schedule_mode      TEXT NOT NULL DEFAULT 'none'
                       CHECK (schedule_mode IN ('none','platform','local','remote')),
    status             TEXT NOT NULL DEFAULT 'draft'
                       CHECK (status IN ('draft','ready','scheduled_local','scheduled_platform',
                                         'uploading','processing_remote','published','failed',
                                         'manual_action','cancelled')),
    remote_id          TEXT NOT NULL DEFAULT '',
    public_url         TEXT NOT NULL DEFAULT '',
    last_error         TEXT NOT NULL DEFAULT '',
    last_attempt_at    TEXT,
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (master_id, social_account_id)            -- prévention des doublons
);

CREATE TABLE publication_attempt (
    id              INTEGER PRIMARY KEY,
    publication_id  INTEGER NOT NULL REFERENCES publication(id) ON DELETE CASCADE,
    at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    outcome         TEXT NOT NULL,
    detail          TEXT NOT NULL DEFAULT ''
);

CREATE TABLE analytics_snapshot (
    id              INTEGER PRIMARY KEY,
    publication_id  INTEGER NOT NULL REFERENCES publication(id) ON DELETE CASCADE,
    captured_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    metrics_json    TEXT NOT NULL DEFAULT '{}'   -- clé absente = indicateur indisponible
);

CREATE TABLE preset (
    id          INTEGER PRIMARY KEY,
    concept_id  INTEGER REFERENCES concept(id) ON DELETE CASCADE,   -- NULL = global
    kind        TEXT NOT NULL,            -- subtitle, export, audio, video, hook...
    name        TEXT NOT NULL,
    data_json   TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (concept_id, kind, name)
);

-- File de tâches persistante.
CREATE TABLE job (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL,
    label         TEXT NOT NULL,
    params_json   TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','running','succeeded','failed','cancelled')),
    progress      REAL NOT NULL DEFAULT 0,
    message       TEXT NOT NULL DEFAULT '',
    error         TEXT NOT NULL DEFAULT '',
    result_json   TEXT NOT NULL DEFAULT '{}',
    critical      INTEGER NOT NULL DEFAULT 0,   -- export/publication : ne pas interrompre
    attempts      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    started_at    TEXT,
    finished_at   TEXT
);

CREATE TABLE app_setting (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE audit_log (
    id      INTEGER PRIMARY KEY,
    at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    action  TEXT NOT NULL,
    detail  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_publication_status ON publication(status);
CREATE INDEX idx_publication_sched  ON publication(scheduled_at);
CREATE INDEX idx_asset_project      ON source_asset(project_id);
CREATE INDEX idx_job_status         ON job(status);
