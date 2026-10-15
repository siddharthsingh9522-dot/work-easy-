# -*- coding: utf-8 -*-
"""
Thunderbird bridge job queue (SQLite), multi-user.  Adapted from the DBP app's bridge_queue.

The web app puts 'send' (and later 'match') jobs here; the OM Suite Mail Bridge extension, running inside
each user's own Thunderbird, polls /api/bridge/poll, sends through that user's Thunderbird identity/SMTP
and reports the result back. A user's jobs are only ever visible to that user's Thunderbird.

Jobs/attachments are short-lived (14 days) and live next to the app on disk, so this queue belongs to ONE
running instance (fine for a single Render web service).
"""
import base64
import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time

CLAIM_TIMEOUT = 600          # a claimed job without a result after 10 min goes back into the queue
KEEP_DAYS = 14
_db_path = None
_init_lock = threading.Lock()
_inited = set()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_type TEXT NOT NULL,
  payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',   -- queued | claimed | done | error | cancelled
  batch TEXT, label TEXT,
  created REAL, claimed REAL, finished REAL,
  result TEXT, error TEXT, owner TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attachments(
  id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT, content_type TEXT, data BLOB, created REAL
);
CREATE TABLE IF NOT EXISTS seen(owner TEXT PRIMARY KEY, last_poll REAL);
CREATE INDEX IF NOT EXISTS jobs_owner_status ON jobs(owner, status);
CREATE INDEX IF NOT EXISTS jobs_batch ON jobs(batch);
"""


def configure(directory):
    """Called once by the app factory: where the queue database lives."""
    global _db_path
    os.makedirs(directory, exist_ok=True)
    _db_path = os.path.join(str(directory), "queue.db")


def _conn():
    if _db_path is None:
        raise RuntimeError("bridge_queue.configure() was not called")
    c = sqlite3.connect(_db_path, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    with _init_lock:
        if _db_path not in _inited:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(_SCHEMA)
            _inited.add(_db_path)
    return c


# ------------------------------------------------------------------ tokens
def token_for(master: str, owner: str, version: int = 1) -> str:
    """Personal bridge token of one user. Bumping `version` (Regenerate) invalidates the old one."""
    msg = f"om-bridge|{owner.strip().lower()}|{int(version)}".encode()
    return hmac.new(master.encode(), msg, hashlib.sha256).hexdigest()[:40]


def connection_code(url: str, owner: str, token: str) -> str:
    """One string to paste into the extension: URL + user + token."""
    raw = json.dumps({"v": 1, "url": url.rstrip("/"), "user": owner, "token": token}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def touch(owner):
    with _conn() as c:
        c.execute("INSERT INTO seen(owner,last_poll) VALUES(?,?) ON CONFLICT(owner) DO UPDATE SET last_poll=excluded.last_poll",
                  (owner, time.time()))


def last_seen(owner):
    with _conn() as c:
        r = c.execute("SELECT last_poll FROM seen WHERE owner=?", (owner,)).fetchone()
    return r["last_poll"] if r else None


# ------------------------------------------------------------------ attachments / jobs
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def add_attachment(owner, name, data: bytes, content_type) -> str:
    aid = hashlib.sha1(owner.encode() + b"|" + name.encode() + b"|" + data).hexdigest()[:24]
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO attachments(id,owner,name,content_type,data,created) VALUES(?,?,?,?,?,?)",
                  (aid, owner, name, content_type, data, time.time()))
        c.execute("UPDATE attachments SET created=? WHERE id=?", (time.time(), aid))
    return aid


def get_attachment(owner, aid):
    with _conn() as c:
        r = c.execute("SELECT name,content_type,data FROM attachments WHERE id=? AND owner=?", (aid, owner)).fetchone()
    return (r["name"], r["content_type"], bytes(r["data"])) if r else None


def enqueue_send(owner, mail: dict, batch: str, identity_email: str = "", bcc_list=None) -> int:
    """mail = mail_generator.build_mail() / plan_* dict. Returns the job id."""
    att = None
    if mail.get("attachment_bytes"):
        name = mail.get("attachment_name") or "pending_data.xlsx"
        ctype = mail.get("attachment_type") or XLSX
        aid = add_attachment(owner, name, mail["attachment_bytes"], ctype)
        att = {"url": f"/api/bridge/attachment/{aid}", "name": name, "content_type": ctype}
    payload = {
        "to": list(mail.get("to_list", [])), "cc": list(mail.get("cc_list", [])),
        "bcc": list(bcc_list if bcc_list is not None else mail.get("bcc_list", [])),
        "subject": mail["subject"], "plain_text": mail["body"], "html": mail.get("html_body") or "",
        "identity_email": identity_email or "", "attachment": att,
        "branch_code": mail.get("branch_code"), "branch_name": mail.get("branch_name"),
        "ref": mail.get("ref"),            # which workflow row this job belongs to (module + code)
    }
    label = str(mail.get("branch_code") or mail.get("label") or "")
    with _conn() as c:
        cur = c.execute("INSERT INTO jobs(job_type,payload,status,batch,label,created,owner) VALUES('send',?,?,?,?,?,?)",
                        (json.dumps(payload, default=str), "queued", batch, label, time.time(), owner))
        return cur.lastrowid


def enqueue_job(owner, job_type, payload: dict, batch="", label="") -> int:
    """Generic job (e.g. 'match' for the Bill Mail module later)."""
    with _conn() as c:
        cur = c.execute("INSERT INTO jobs(job_type,payload,status,batch,label,created,owner) VALUES(?,?,?,?,?,?,?)",
                        (job_type, json.dumps(payload, default=str), "queued", batch, label, time.time(), owner))
        return cur.lastrowid


def poll(owner, limit=5, types=None) -> list:
    """For the extension: claim this user's queued jobs (plus claimed ones that timed out)."""
    now = time.time()
    try:
        limit = max(1, min(int(limit or 5), 25))
    except (TypeError, ValueError):
        limit = 5
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            q = "SELECT id,job_type,payload FROM jobs WHERE owner=? AND (status='queued' OR (status='claimed' AND claimed<?))"
            args = [owner, now - CLAIM_TIMEOUT]
            if types:
                q += " AND job_type IN (%s)" % ",".join("?" * len(types))
                args += list(types)
            q += " ORDER BY id LIMIT ?"
            args.append(limit)
            rows = c.execute(q, args).fetchall()
            for r in rows:
                c.execute("UPDATE jobs SET status='claimed', claimed=? WHERE id=?", (now, r["id"]))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    return [{"id": r["id"], "job_type": r["job_type"], "payload": json.loads(r["payload"])} for r in rows]


def set_result(owner, job_id: int, body: dict):
    """Extension body: {"result": {...}} or {"status": "error", "error": "..."}."""
    err = None
    if isinstance(body, dict) and body.get("status") == "error":
        err = str(body.get("error") or "Unknown error")[:1000]
    with _conn() as c:
        c.execute("UPDATE jobs SET status=?, finished=?, result=?, error=? WHERE id=? AND owner=?",
                  ("error" if err else "done", time.time(),
                   None if err else json.dumps((body or {}).get("result"), default=str), err, int(job_id), owner))


def batch_status(owner, batch: str) -> list:
    with _conn() as c:
        rows = c.execute("SELECT id,label,status,payload,error,finished FROM jobs WHERE batch=? AND owner=? ORDER BY id",
                         (batch, owner)).fetchall()
    out = []
    for r in rows:
        p = json.loads(r["payload"])
        to = p.get("to", [])
        out.append({"job": r["id"], "branch_code": p.get("branch_code") or r["label"],
                    "branch_name": p.get("branch_name") or "", "status": r["status"].upper(),
                    "to": ", ".join(to[:6]) + (" ..." if len(to) > 6 else ""),
                    "n_to": len(to), "n_cc": len(p.get("cc", [])), "error": r["error"] or ""})
    return out


def get_job(owner, job_id):
    """-> dict(id, job_type, status, result, error, payload) of one of THIS user's jobs, or None."""
    with _conn() as c:
        r = c.execute("SELECT id,job_type,status,payload,result,error,created,finished FROM jobs WHERE id=? AND owner=?",
                      (int(job_id), owner)).fetchone()
    if not r:
        return None
    return {"id": r["id"], "job_type": r["job_type"], "status": r["status"].upper(), "error": r["error"] or "",
            "result": json.loads(r["result"]) if r["result"] else None, "payload": json.loads(r["payload"]),
            "created": r["created"], "finished": r["finished"]}


def job_states(owner, ids) -> dict:
    """{job id: (status, error)} for several of this user's jobs."""
    ids = [int(i) for i in ids]
    if not ids:
        return {}
    with _conn() as c:
        rows = c.execute("SELECT id,status,error FROM jobs WHERE owner=? AND id IN (%s)" % ",".join("?" * len(ids)),
                         [owner, *ids]).fetchall()
    return {r["id"]: (r["status"].upper(), r["error"] or "") for r in rows}


def recent(owner, limit=40) -> list:
    with _conn() as c:
        rows = c.execute("SELECT id,job_type,status,payload,error,created,finished FROM jobs WHERE owner=? ORDER BY id DESC LIMIT ?",
                         (owner, limit)).fetchall()
    out = []
    for r in rows:
        p = json.loads(r["payload"])
        out.append({"id": r["id"], "type": r["job_type"], "status": r["status"].upper(), "created": r["created"],
                    "finished": r["finished"], "error": r["error"] or "",
                    "to": ", ".join(p.get("to", [])[:4]), "subject": (p.get("subject") or "")[:90],
                    "ref": p.get("ref") or {}})
    return out


def counts(owner) -> dict:
    with _conn() as c:
        rows = c.execute("SELECT status,COUNT(*) n FROM jobs WHERE owner=? GROUP BY status", (owner,)).fetchall()
    return {r["status"]: r["n"] for r in rows}


def cancel_queued(owner, batch: str) -> int:
    with _conn() as c:
        return c.execute("UPDATE jobs SET status='cancelled' WHERE batch=? AND status='queued' AND owner=?",
                         (batch, owner)).rowcount


def retry_failed(owner, batch: str) -> int:
    with _conn() as c:
        return c.execute("UPDATE jobs SET status='queued', error=NULL WHERE batch=? AND status='error' AND owner=?",
                         (batch, owner)).rowcount


def purge_old():
    cutoff = time.time() - KEEP_DAYS * 86400
    with _conn() as c:
        c.execute("DELETE FROM jobs WHERE created<? AND status IN ('done','error','cancelled')", (cutoff,))
        c.execute("DELETE FROM attachments WHERE created<?", (cutoff,))
