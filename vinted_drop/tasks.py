"""Tâches de fond persistantes.

Chaque tâche est enregistrée en base (identifiant, statut, début, fin, résultat, erreur, tentatives).
- Anti-doublon : deux tâches avec la même `dedupe_key` ne tournent jamais en même temps.
- Réessais : `max_attempts` > 1 relance avec une attente croissante.
- Arrêt d'urgence : les tâches automatiques (source="autopilot") sont refusées.
- Au redémarrage, les tâches interrompues sont marquées en erreur (aucune n'est relancée en silence).
"""
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import config, db

log = logging.getLogger(__name__)
_pool = None
_lock = threading.Lock()
ACTIVE = ("queued", "running")


def pool():
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=config.TASK_WORKERS, thread_name_prefix="task")
    return _pool


def _short(value, n=2000):
    try:
        s = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = str(value)
    return s[:n]


def active(dedupe_key=None, product_id=None):
    q, args = "SELECT * FROM tasks WHERE status IN ('queued','running')", []
    if dedupe_key:
        q += " AND dedupe_key=?"
        args.append(dedupe_key)
    if product_id:
        q += " AND product_id=?"
        args.append(product_id)
    with db.connect() as c:
        return [dict(r) for r in c.execute(q, args)]


def get(tid):
    with db.connect() as c:
        r = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    return dict(r) if r else None


def list_tasks(limit=50, status=None, kind=None, product_id=None):
    q, args = "SELECT * FROM tasks WHERE 1=1", []
    for col, val in (("status", status), ("kind", kind), ("product_id", product_id)):
        if val:
            q += f" AND {col}=?"
            args.append(val)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with db.connect() as c:
        return [dict(r) for r in c.execute(q, args)]


def _create(kind, label, dedupe_key, shop_id, product_id, max_attempts, source):
    with _lock:  # vérification + insertion atomiques dans ce processus
        if dedupe_key:
            existing = active(dedupe_key)
            if existing:
                return existing[0]["id"], False
        with db.connect() as c:
            cur = c.execute(
                "INSERT INTO tasks (kind, label, dedupe_key, shop_id, product_id, status, max_attempts, source, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (kind, label, dedupe_key, shop_id, product_id, "queued", max_attempts, source, db.now()))
            return cur.lastrowid, True


def _set(tid, **f):
    cols = ", ".join(f"{k}=?" for k in f)
    with db.connect() as c:
        c.execute(f"UPDATE tasks SET {cols} WHERE id=?", (*f.values(), tid))


def blocked(source) -> str | None:
    if source == "autopilot":
        if db.get_setting("emergency_stop") == "on":
            return "Arrêt d'urgence activé"
        if db.get_setting("autopilot") != "on":
            return "Pilote automatique en pause"
    return None


def execute(tid, fn, args=(), kwargs=None, on_error=None):
    """Exécute la tâche `tid` dans le thread courant (utilisé par le pool et par le planning)."""
    kwargs = kwargs or {}
    t = get(tid)
    reason = blocked(t["source"])
    if reason:
        _set(tid, status="cancelled", error=reason, finished_at=db.now())
        return None
    attempts = 0
    while True:
        attempts += 1
        _set(tid, status="running", attempts=attempts, started_at=t.get("started_at") or db.now())
        try:
            result = fn(*args, **kwargs)
            _set(tid, status="success", result=_short(result), error=None, finished_at=db.now())
            return result
        except Exception as e:
            log.exception("Tâche %s (%s) en erreur", tid, t["kind"])
            from .errors import explain
            msg = explain(e)
            if attempts < (t.get("max_attempts") or 1):
                _set(tid, error=f"essai {attempts} : {msg}")
                time.sleep(min(60, 2 ** attempts))
                continue
            _set(tid, status="error", error=msg, finished_at=db.now())
            db.log_event("error", f"{t['label'] or t['kind']} : {msg}", shop_id=t.get("shop_id"),
                         product_id=t.get("product_id"), source=t["source"], level="error")
            if on_error:
                on_error(t, msg)
            return None


def submit(kind, fn, *args, label=None, dedupe_key=None, shop_id=None, product_id=None, max_attempts=1,
           source="dashboard", on_error=None, **kwargs):
    """Lance une tâche en arrière-plan. Renvoie (id, créée?) ; créée=False si une tâche identique tourne déjà."""
    tid, created = _create(kind, label, dedupe_key, shop_id, product_id, max_attempts, source)
    if created:
        pool().submit(execute, tid, fn, args, kwargs, on_error)
    return tid, created


def run_now(kind, fn, *args, label=None, dedupe_key=None, source="autopilot", max_attempts=1, **kwargs):
    """Version synchrone (pour le planning) : même traçabilité, exécution dans le thread appelant."""
    tid, created = _create(kind, label, dedupe_key, None, None, max_attempts, source)
    if not created:
        return None
    return execute(tid, fn, args, kwargs)


def cancel(tid):
    t = get(tid)
    if t and t["status"] == "queued":
        _set(tid, status="cancelled", error="Annulée", finished_at=db.now())
        return True
    return False


def recover():
    """À appeler au démarrage : les tâches restées 'running' ont été interrompues par un arrêt du programme."""
    with db.connect() as c:
        c.execute("UPDATE tasks SET status='error', error='Interrompue (redémarrage du programme)', finished_at=? "
                  "WHERE status IN ('queued','running')", (db.now(),))


def wait_all(timeout=30):
    """Pour les tests : attend la fin des tâches en cours."""
    end = time.time() + timeout
    while time.time() < end:
        if not active():
            return True
        time.sleep(0.05)
    return False
