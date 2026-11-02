-- Migration 0011 : calendrier et concepts partagés entre plusieurs ordinateurs (dossier OneDrive, Google Drive,
-- Dropbox… choisi dans Paramètres). Un concept (avec ses chaînes) et une vidéo du calendrier ont un identifiant
-- commun à tous les PC (`uid`) ; la vidéo du calendrier garde aussi le nom de la vidéo, qui n'existe peut-être pas
-- sur cet ordinateur. `sync_state` retient, pour chaque élément, l'empreinte de sa version locale et de celle du
-- dossier partagé à la dernière synchronisation : ce qui a changé depuis, et de quel côté.
ALTER TABLE concept ADD COLUMN uid TEXT;
ALTER TABLE master_publication ADD COLUMN uid TEXT;
ALTER TABLE master_publication ADD COLUMN video_name TEXT NOT NULL DEFAULT '';

CREATE TABLE sync_state (
    kind         TEXT NOT NULL CHECK (kind IN ('concept', 'plan')),
    uid          TEXT NOT NULL,
    local_hash   TEXT NOT NULL DEFAULT '',
    remote_hash  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (kind, uid)
);
