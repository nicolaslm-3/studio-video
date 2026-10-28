-- Migration 0009 : Atelier de publication. Liens retrouvés sur la page publique du compte pour une publication
-- faite à la main : proposés à l'utilisateur (« Est-ce cette vidéo ? ») tant qu'ils ne sont pas sûrs.
CREATE TABLE publication_match (
    id              INTEGER PRIMARY KEY,
    publication_id  INTEGER NOT NULL REFERENCES publication(id) ON DELETE CASCADE,
    url             TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    score           REAL NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'rejected')),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (publication_id, url)
);
