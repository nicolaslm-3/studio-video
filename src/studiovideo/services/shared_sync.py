"""Calendrier et concepts partagés entre plusieurs ordinateurs, par un dossier synchronisé (OneDrive, Google Drive,
Dropbox…) choisi dans Paramètres (réglage `shared_folder`).

Un petit fichier JSON par élément : `concepts/<uid>.json` (le concept et ses chaînes : réseau, nom, adresse publique)
et `calendrier/<uid>.json` (une vidéo programmée). Deux PC qui changent deux éléments différents ne se gênent jamais.
Pour chaque élément, `sync_state` retient l'empreinte de la version locale et de celle du dossier à la dernière
synchronisation :
- changé seulement ici : écrit dans le dossier ;
- changé seulement dans le dossier : appliqué ici ;
- changé des deux côtés : la version de ce PC l'emporte (la plus récente vue d'ici), sauf la toute première fois
  (élément retrouvé par son nom ou son lien), où le dossier fait foi ;
- supprimé : un fichier « supprimé » le dit aux autres PC.
Ne voyagent pas : les vidéos et les médias, les jetons de connexion par API (coffre Windows de chaque PC), les
réglages propres à un PC (navigateur, dernier rapprochement). Le concept « Brouillons » reste sur chaque PC.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform as _platform
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..db.database import Database
from .concepts import ConceptService
from .publications import PublicationService

FOLDERS = {"concept": "concepts", "plan": "calendrier"}
_META_KEYS = ("uid", "updated_at", "by", "deleted")


class SyncError(RuntimeError):
    pass


def _hash(content: dict) -> str:
    return hashlib.sha1(json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _content(data: dict) -> dict:
    return {k: v for k, v in data.items() if k not in _META_KEYS}


def _links(networks: dict) -> list[str]:
    from ..publish import reconcile
    return sorted(reconcile.video_key(u) for u in (networks.get("links") or {}).values() if u)


class SharedSync:
    def __init__(self, db: Database, concepts: ConceptService, publications: PublicationService, folder: str | Path):
        self.db, self.concepts, self.publications = db, concepts, publications
        self.root = Path(folder)

    # -- entrée -------------------------------------------------------------------------------------
    def run(self) -> dict:
        if not str(self.root) or not self.root.is_dir():
            raise SyncError(f"Dossier partagé introuvable : {self.root}")
        for d in FOLDERS.values():
            (self.root / d).mkdir(exist_ok=True)
        self._give_uids()
        out = {"sent": 0, "received": 0, "deleted": 0, "errors": []}
        remote = self._read_all("concept")
        self._adopt_concepts(remote)
        self._merge("concept", self._local_concepts(), remote, self._apply_concept, self._delete_concept, out)
        remote = self._read_all("plan")
        self._adopt_plans(remote)
        self._merge("plan", self._local_plans(), remote, self._apply_plan, self._delete_plan, out)
        # un concept supprimé ailleurs ne part qu'une fois ses vidéos du calendrier parties : second passage
        self._merge("concept", self._local_concepts(), self._read_all("concept"), self._apply_concept,
                    self._delete_concept, out)
        return out

    # -- fusion -------------------------------------------------------------------------------------
    def _merge(self, kind: str, local: dict[str, dict], remote: dict[str, dict], apply, delete, out: dict) -> None:
        state = {r["uid"]: (r["local_hash"], r["remote_hash"])
                 for r in self.db.query("SELECT * FROM sync_state WHERE kind = ?", (kind,))}
        for uid in sorted(set(local) | set(remote)):
            lc, rd = local.get(uid), remote.get(uid)
            known = uid in state
            sl, sr = state.get(uid, ("", ""))
            hl = _hash(lc) if lc is not None else ""
            gone = bool(rd and rd.get("deleted"))
            rc = _content(rd) if rd is not None and not gone else None
            hr = _hash(rc) if rc is not None else ""
            try:
                if lc is not None and rc is not None:
                    if hl == hr:
                        self._remember(kind, uid, hl, hr)
                    elif not known or (hl == sl and hr != sr):
                        self._receive(kind, uid, rc, hr, apply, out)
                    elif hl != sl:                                   # changé ici (des deux côtés : ce PC gagne)
                        self._send(kind, uid, lc, out)
                elif lc is not None:
                    if gone and known and hl == sl:                  # supprimé ailleurs, pas retouché ici
                        after = delete(uid)
                        out["deleted"] += 1 if after is None else 0
                        if after is None:
                            self._forget(kind, uid)
                        else:                                        # ne peut pas partir (concept avec des vidéos)
                            self._remember(kind, uid, _hash(after), "deleted")
                    elif not gone or hl != sl or not known:          # nouveau ici, ou retouché après la suppression
                        self._send(kind, uid, lc, out)
                elif rc is not None:
                    if known and hr == sr:                           # supprimé ici : on le dit aux autres
                        self._write(kind, uid, None)
                        self._forget(kind, uid)
                    else:
                        self._receive(kind, uid, rc, hr, apply, out)
                elif known:
                    self._forget(kind, uid)
            except Exception as exc:                                 # un élément en échec n'arrête pas les autres
                name = (lc or rc or {}).get("name") or (lc or rc or {}).get("title") or uid
                out["errors"].append(f"{name} : {(str(exc).splitlines() or ['?'])[0][:160]}")

    def _send(self, kind: str, uid: str, content: dict, out: dict) -> None:
        self._write(kind, uid, content)
        h = _hash(content)
        self._remember(kind, uid, h, h)
        out["sent"] += 1

    def _receive(self, kind: str, uid: str, content: dict, h: str, apply, out: dict) -> None:
        after = apply(uid, content)
        self._remember(kind, uid, _hash(after), h)
        out["received"] += 1

    def _remember(self, kind: str, uid: str, local_hash: str, remote_hash: str) -> None:
        with self.db.transaction() as c:
            c.execute("INSERT INTO sync_state(kind, uid, local_hash, remote_hash) VALUES (?,?,?,?) "
                      "ON CONFLICT(kind, uid) DO UPDATE SET local_hash = excluded.local_hash, "
                      "remote_hash = excluded.remote_hash", (kind, uid, local_hash, remote_hash))

    def _forget(self, kind: str, uid: str) -> None:
        with self.db.transaction() as c:
            c.execute("DELETE FROM sync_state WHERE kind = ? AND uid = ?", (kind, uid))

    # -- fichiers -----------------------------------------------------------------------------------
    def _read_all(self, kind: str) -> dict[str, dict]:
        out = {}
        for f in (self.root / FOLDERS[kind]).glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue                       # en cours de copie par OneDrive / Drive : à la prochaine fois
            if isinstance(data, dict) and data.get("uid") == f.stem:     # copies de conflit (« …-PC.json ») ignorées
                out[f.stem] = data
        return out

    def _write(self, kind: str, uid: str, content: dict | None) -> None:
        data = {"uid": uid, **(content if content is not None else {"deleted": True}),
                "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "by": _platform.node()}
        f = self.root / FOLDERS[kind] / f"{uid}.json"
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, f)                     # jamais un fichier à moitié écrit dans le dossier partagé

    # -- identifiants communs -----------------------------------------------------------------------
    def _give_uids(self) -> None:
        with self.db.transaction() as c:
            for r in c.execute("SELECT id FROM concept WHERE uid IS NULL").fetchall():
                c.execute("UPDATE concept SET uid = ? WHERE id = ?", (uuid.uuid4().hex, r["id"]))
            for r in c.execute("SELECT id, networks_json FROM master_publication WHERE uid IS NULL "
                               "AND planned_at IS NOT NULL").fetchall():
                # vidéo retrouvée sur une chaîne : le même identifiant sur tous les PC (pas de doublon si deux PC
                # la retrouvent chacun de leur côté)
                keys = _links(self._networks(r["networks_json"]))
                uid = hashlib.sha1(("video:" + keys[0]).encode()).hexdigest()[:32] if keys else uuid.uuid4().hex
                if c.execute("SELECT 1 FROM master_publication WHERE uid = ?", (uid,)).fetchone():
                    uid = uuid.uuid4().hex
                c.execute("UPDATE master_publication SET uid = ? WHERE id = ?", (uid, r["id"]))

    def _set_uid(self, table: str, row_id: int, uid: str) -> None:
        with self.db.transaction() as c:
            c.execute(f"UPDATE {table} SET uid = ? WHERE id = ?", (uid, row_id))

    @staticmethod
    def _networks(raw: str | None) -> dict:
        try:
            d = json.loads(raw or "{}")
        except ValueError:
            return {}
        return d if isinstance(d, dict) else {}

    def _unsynced(self, kind: str, rows) -> list:
        known = {r["uid"] for r in self.db.query("SELECT uid FROM sync_state WHERE kind = ?", (kind,))}
        return [r for r in rows if r["uid"] not in known]

    # -- concepts -----------------------------------------------------------------------------------
    def _concept_rows(self):
        return self.db.query("SELECT * FROM concept WHERE name != ?", (ConceptService.DRAFTS,))

    def _adopt_concepts(self, remote: dict[str, dict]) -> None:
        """Premier passage d'un PC : un concept du même nom que celui du dossier devient le même concept."""
        mine = {r["uid"] for r in self._concept_rows()}
        free = {r["name"].casefold(): r for r in self._unsynced("concept", self._concept_rows())
                if r["uid"] not in remote}
        for uid, d in remote.items():
            if uid in mine or d.get("deleted"):
                continue
            r = free.pop((d.get("name") or "").casefold(), None)
            if r is not None:
                self._set_uid("concept", r["id"], uid)

    def _local_concepts(self) -> dict[str, dict]:
        return {r["uid"]: self._concept_content(r["id"]) for r in self._concept_rows()}

    def _concept_content(self, concept_id: int) -> dict:
        c = self.concepts.get_concept(concept_id)
        accounts = sorted(({"platform": a.platform.value, "name": a.name, "public_url": a.public_url,
                            "language": a.language} for a, _d in self.concepts.concept_accounts(concept_id)),
                          key=lambda a: (a["platform"], a["name"], a["public_url"]))
        return {"name": c.name, "theme": c.theme, "color": c.color, "language": c.language,
                "archived": bool(c.archived), "accounts": accounts}

    def _apply_concept(self, uid: str, d: dict) -> dict:
        row = self.db.query_one("SELECT id FROM concept WHERE uid = ?", (uid,))
        fields = {"name": d.get("name") or "Sans nom", "theme": d.get("theme") or "", "color": d.get("color") or "#8A8F98",
                  "language": d.get("language") or "fr"}
        if row is None:
            cid = self.concepts.create_concept(**fields).id
            self._set_uid("concept", cid, uid)
        else:
            cid = row["id"]
            self.concepts.update_concept(cid, **fields)
        self.concepts.update_concept(cid, archived=int(bool(d.get("archived"))))
        # ses chaînes : une par réseau, mises à jour sur place (la connexion par API de ce PC est gardée)
        want = {a["platform"]: a for a in d.get("accounts") or [] if a.get("platform")}
        linked = [a for a, _d in self.concepts.concept_accounts(cid)]
        for a in linked:
            if a.platform.value not in want:
                self.concepts.unlink(cid, a.id)
        for pf, w in want.items():
            same = next((a for a in linked if a.platform.value == pf), None)
            if same is not None:
                self.concepts.update_account(same.id, name=w.get("name") or fields["name"],
                                             public_url=w.get("public_url") or "", language=w.get("language") or "fr")
                continue
            url = w.get("public_url") or ""
            known = next((a for a in self.concepts.list_accounts() if url and a.platform.value == pf
                          and a.public_url == url), None)      # chaîne déjà enregistrée sur ce PC
            aid = known.id if known else self.concepts.create_account(pf, w.get("name") or fields["name"], url,
                                                                      w.get("language") or "fr").id
            self.concepts.link(cid, aid)
        return self._concept_content(cid)

    def _delete_concept(self, uid: str) -> dict | None:
        row = self.db.query_one("SELECT id FROM concept WHERE uid = ?", (uid,))
        if row is None:
            return None
        try:
            self.concepts.delete_concept(row["id"])
            return None
        except Exception:                                        # il a des vidéos sur ce PC : mis aux archives
            self.concepts.update_concept(row["id"], archived=1)
            return self._concept_content(row["id"])

    # -- calendrier ---------------------------------------------------------------------------------
    _PLANS = ("SELECT m.*, c.uid concept_uid, c.name concept_name, pr.name project_name FROM master_publication m "
              "JOIN concept c ON c.id = m.concept_id LEFT JOIN project pr ON pr.id = m.project_id "
              "WHERE m.planned_at IS NOT NULL")

    def _adopt_plans(self, remote: dict[str, dict]) -> None:
        """Premier passage : une vidéo déjà au calendrier ici (même lien publié, ou même date et même titre) devient
        celle du dossier, sans doublon."""
        rows = self.db.query(self._PLANS)
        mine = {r["uid"] for r in rows}
        free = [r for r in self._unsynced("plan", rows) if r["uid"] not in remote]
        for uid, d in remote.items():
            if uid in mine or d.get("deleted"):
                continue
            keys = set(_links(d.get("networks") or {}))
            for r in free:
                same_link = keys and keys & set(_links(self._networks(r["networks_json"])))
                same_text = (r["planned_at"] == d.get("planned_at") and r["title"] == (d.get("title") or "")
                             and r["concept_name"] == d.get("concept_name"))
                if same_link or same_text:
                    self._set_uid("master_publication", r["id"], uid)
                    free.remove(r)
                    break

    def _local_plans(self) -> dict[str, dict]:
        return {r["uid"]: self._plan_content(r) for r in self.db.query(self._PLANS)}

    def _plan_content(self, r) -> dict:
        return {"concept": r["concept_uid"], "concept_name": r["concept_name"],
                "video": r["project_name"] or r["video_name"] or "", "title": r["title"],
                "description": r["description"], "hashtags": r["hashtags"], "planned_at": r["planned_at"],
                "networks": self._networks(r["networks_json"])}

    def _apply_plan(self, uid: str, d: dict) -> dict:
        c = self.db.query_one("SELECT id FROM concept WHERE uid = ?", (d.get("concept"),)) or \
            self.db.query_one("SELECT id FROM concept WHERE name = ? COLLATE NOCASE", (d.get("concept_name") or "",))
        cid = c["id"] if c else self.concepts.drafts_concept().id
        video = d.get("video") or ""
        p = self.db.query_one("SELECT id FROM project WHERE name = ? AND status != 'archived' ORDER BY id LIMIT 1",
                              (video,)) if video else None
        values = (cid, p["id"] if p else None, video, d.get("title") or "", d.get("description") or "",
                  d.get("hashtags") or "", d.get("planned_at"), json.dumps(d.get("networks") or {}))
        with self.db.transaction() as conn:
            row = conn.execute("SELECT id FROM master_publication WHERE uid = ?", (uid,)).fetchone()
            if row is None:
                conn.execute("INSERT INTO master_publication(concept_id, project_id, video_name, title, description,"
                             " hashtags, planned_at, networks_json, uid) VALUES (?,?,?,?,?,?,?,?,?)", (*values, uid))
            else:
                conn.execute("UPDATE master_publication SET concept_id = ?, project_id = ?, video_name = ?, title = ?,"
                             " description = ?, hashtags = ?, planned_at = ?, networks_json = ? WHERE id = ?",
                             (*values, row["id"]))
        return self._plan_content(self.db.query_one(self._PLANS + " AND m.uid = ?", (uid,)))

    def _delete_plan(self, uid: str) -> dict | None:
        row = self.db.query_one("SELECT id FROM master_publication WHERE uid = ?", (uid,))
        if row is None:
            return None
        try:
            self.publications.delete_master(row["id"])
            return None
        except Exception:                                        # déjà publiée par API depuis ce PC : gardée
            return self._plan_content(self.db.query_one(self._PLANS + " AND m.id = ?", (row["id"],)))
