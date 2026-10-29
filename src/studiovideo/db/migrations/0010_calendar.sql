-- Migration 0010 : calendrier. Une publication programmée retient la vidéo (projet) qu'elle concerne et l'état des
-- petits logos YouTube / TikTok / Meta du calendrier ({"youtube": true, …}), cochés ou non par l'utilisateur pour
-- s'organiser (rien n'est publié par le logiciel à partir de ces cases).
ALTER TABLE master_publication ADD COLUMN project_id INTEGER REFERENCES project(id) ON DELETE SET NULL;
ALTER TABLE master_publication ADD COLUMN networks_json TEXT NOT NULL DEFAULT '{}';
