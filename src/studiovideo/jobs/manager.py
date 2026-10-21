"""Gestionnaire de tâches persistant (cahier des charges §17.3, §23.4).

- Les tâches sont enregistrées dans la table `job` : elles survivent à un redémarrage.
- Elles s'exécutent dans des threads de travail, jamais dans le thread graphique.
- Chaque type de tâche a un gestionnaire `handler(ctx, params) -> dict`.
- Annulation coopérative : `ctx.check()` lève JobCancelled ; les processus
  externes lancés via `ctx.run_process` sont arrêtés.
- Au démarrage, une tâche restée « running » (plantage, coupure) est marquée
  en échec « interrompue » et peut être relancée.
- Les tâches `critical` (export, publication) bloquent la mise à jour tant
  qu'elles tournent.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from ..db.database import Database

log = logging.getLogger(__name__)

Handler = Callable[["JobContext", dict], dict | None]
Listener = Callable[[int], None]


class JobCancelled(Exception):
    pass


@dataclass
class JobInfo:
    id: int
    kind: str
    label: str
    status: str
    progress: float
    message: str
    error: str
    critical: bool
    created_at: str
    result: dict


class JobContext:
    def __init__(self, manager: "JobManager", job_id: int):
        self._m = manager
        self.job_id = job_id
        self.cancel_event = threading.Event()
        self._proc: subprocess.Popen | None = None

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def check(self) -> None:
        if self.cancelled:
            raise JobCancelled()

    def progress(self, fraction: float, message: str = "") -> None:
        self.check()
        self._m._update(self.job_id, progress=max(0.0, min(1.0, float(fraction))), message=message)

    def run_process(self, cmd: list[str], on_line: Callable[[str], None] | None = None) -> int:
        """Lance un processus externe, relaie stderr ligne à ligne, tue en cas d'annulation."""
        self.check()
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                      stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                      errors="replace", creationflags=flags)
        tail: list[str] = []
        assert self._proc.stderr is not None
        for line in self._proc.stderr:
            if self.cancelled:
                self._proc.kill()
                break
            tail = (tail + [line.rstrip()])[-20:]
            if on_line:
                on_line(line)
        rc = self._proc.wait()
        self._proc = None
        self.check()
        if rc != 0:
            raise RuntimeError(f"Le processus a échoué (code {rc}) :\n" + "\n".join(tail[-8:]))
        return rc

    def _kill(self) -> None:
        p = self._proc
        if p and p.poll() is None:
            p.kill()


class JobManager:
    def __init__(self, db: Database, max_workers: int = 2):
        self.db = db
        self._handlers: dict[str, Handler] = {}
        self._contexts: dict[int, JobContext] = {}
        self._listeners: list[Listener] = []
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="job")
        self._idle = threading.Condition(self._lock)
        self._recover()

    # -- enregistrement --------------------------------------------------
    def register(self, kind: str, handler: Handler) -> None:
        self._handlers[kind] = handler

    def add_listener(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def _notify(self, job_id: int) -> None:
        for fn in list(self._listeners):
            try:
                fn(job_id)
            except Exception:
                log.exception("Listener de tâche en erreur")

    # -- cycle de vie ----------------------------------------------------
    def _recover(self) -> None:
        with self.db.transaction() as c:
            c.execute("UPDATE job SET status = 'failed', error = 'Interrompue (arrêt de l''application).',"
                      " finished_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE status = 'running'")

    def resume_pending(self) -> int:
        """Relance les tâches en attente laissées par une session précédente."""
        rows = self.db.query("SELECT id FROM job WHERE status = 'pending' ORDER BY id")
        for r in rows:
            self._dispatch(r["id"])
        return len(rows)

    def submit(self, kind: str, label: str, params: dict | None = None, *, critical: bool = False) -> int:
        if kind not in self._handlers:
            raise KeyError(f"Type de tâche inconnu : {kind}")
        with self.db.transaction() as c:
            jid = c.execute("INSERT INTO job(kind, label, params_json, critical) VALUES (?,?,?,?)",
                            (kind, label, json.dumps(params or {}), int(critical))).lastrowid
        self._notify(jid)
        self._dispatch(jid)
        return jid

    def _dispatch(self, job_id: int) -> None:
        ctx = JobContext(self, job_id)
        with self._lock:
            self._contexts[job_id] = ctx
        self._pool.submit(self._run, job_id, ctx)

    def _run(self, job_id: int, ctx: JobContext) -> None:
        row = self.db.query_one("SELECT * FROM job WHERE id = ?", (job_id,))
        try:
            if row is None or row["status"] != "pending" or ctx.cancelled:
                if row is not None and row["status"] == "pending":
                    self._update(job_id, status="cancelled", finished=True)
                return
            handler = self._handlers.get(row["kind"])
            if handler is None:
                self._update(job_id, status="failed", error=f"Type de tâche non disponible : {row['kind']}",
                             finished=True)
                return
            self.db.connect().execute(
                "UPDATE job SET status='running', attempts = attempts + 1, error = '',"
                " started_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?", (job_id,))
            self._notify(job_id)
            try:
                result = handler(ctx, json.loads(row["params_json"])) or {}
            except JobCancelled:
                self._update(job_id, status="cancelled", message="Annulée", finished=True)
            except Exception as exc:
                log.error("Tâche %s (%s) en échec\n%s", job_id, row["kind"], traceback.format_exc())
                self._update(job_id, status="failed", error=str(exc) or type(exc).__name__, finished=True)
            else:
                self._update(job_id, status="succeeded", progress=1.0, result=result, finished=True)
        finally:
            with self._lock:
                self._contexts.pop(job_id, None)
                self._idle.notify_all()
            self.db.close()  # connexion propre au thread

    def _update(self, job_id: int, *, status: str | None = None, progress: float | None = None,
                message: str | None = None, error: str | None = None, result: dict | None = None,
                finished: bool = False) -> None:
        sets, vals = [], []
        for col, val in (("status", status), ("progress", progress), ("message", message), ("error", error)):
            if val is not None:
                sets.append(f"{col} = ?")
                vals.append(val)
        if result is not None:
            sets.append("result_json = ?")
            vals.append(json.dumps(result, ensure_ascii=False, default=str))
        if finished:
            sets.append("finished_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')")
        if sets:
            self.db.connect().execute(f"UPDATE job SET {', '.join(sets)} WHERE id = ?", (*vals, job_id))
        self._notify(job_id)

    def cancel(self, job_id: int) -> None:
        with self._lock:
            ctx = self._contexts.get(job_id)
        if ctx:
            ctx.cancel_event.set()
            ctx._kill()
        self.db.connect().execute(
            "UPDATE job SET status='cancelled', finished_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')"
            " WHERE id = ? AND status = 'pending'", (job_id,))
        self._notify(job_id)

    def retry(self, job_id: int) -> int:
        row = self.db.query_one("SELECT * FROM job WHERE id = ?", (job_id,))
        if row is None or row["status"] not in ("failed", "cancelled"):
            raise ValueError("Seule une tâche échouée ou annulée peut être relancée.")
        self.db.connect().execute(
            "UPDATE job SET status='pending', progress=0, message='', error='', finished_at=NULL WHERE id = ?",
            (job_id,))
        self._notify(job_id)
        self._dispatch(job_id)
        return job_id

    # -- consultation ----------------------------------------------------
    def get(self, job_id: int) -> JobInfo | None:
        r = self.db.query_one("SELECT * FROM job WHERE id = ?", (job_id,))
        return self._info(r) if r else None

    def list(self, statuses: tuple[str, ...] | None = None, limit: int = 200) -> list[JobInfo]:
        if statuses:
            q = ",".join("?" * len(statuses))
            rows = self.db.query(f"SELECT * FROM job WHERE status IN ({q}) ORDER BY id DESC LIMIT ?",
                                 (*statuses, limit))
        else:
            rows = self.db.query("SELECT * FROM job ORDER BY id DESC LIMIT ?", (limit,))
        return [self._info(r) for r in rows]

    @staticmethod
    def _info(r) -> JobInfo:
        return JobInfo(id=r["id"], kind=r["kind"], label=r["label"], status=r["status"],
                       progress=r["progress"], message=r["message"], error=r["error"],
                       critical=bool(r["critical"]), created_at=r["created_at"],
                       result=json.loads(r["result_json"] or "{}"))

    def active_count(self) -> int:
        return self.db.query_one("SELECT COUNT(*) n FROM job WHERE status IN ('pending','running')")["n"]

    def critical_running(self) -> list[JobInfo]:
        return [j for j in self.list(("pending", "running")) if j.critical]

    def wait_idle(self, timeout: float | None = None, critical_only: bool = False) -> bool:
        """Attend la fin des tâches (toutes, ou seulement critiques)."""
        def done() -> bool:
            with self._lock:
                ids = list(self._contexts)
            if not critical_only:
                return not ids
            return not any(j.critical for j in (self.get(i) for i in ids) if j)
        with self._idle:
            return self._idle.wait_for(done, timeout=timeout)

    def cancel_non_critical(self) -> None:
        with self._lock:
            ids = list(self._contexts)
        for jid in ids:
            j = self.get(jid)
            if j and not j.critical:
                self.cancel(jid)

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            ctxs = list(self._contexts.values())
        for ctx in ctxs:
            ctx.cancel_event.set()
            ctx._kill()
        self._pool.shutdown(wait=wait, cancel_futures=True)
