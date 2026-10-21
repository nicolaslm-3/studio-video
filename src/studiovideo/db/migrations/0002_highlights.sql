-- Migration 0002 : informations complémentaires des propositions d'extraits (V2, clipping intelligent)
ALTER TABLE clip_candidate ADD COLUMN speakers TEXT NOT NULL DEFAULT '';
ALTER TABLE clip_candidate ADD COLUMN engine TEXT NOT NULL DEFAULT '';
ALTER TABLE clip_candidate ADD COLUMN request_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE clip_candidate ADD COLUMN created_at TEXT NOT NULL DEFAULT '';
CREATE INDEX idx_candidate_project ON clip_candidate(project_id, source_asset_id);
