"""
User/audit storage.

* FirestoreStore  -> used when a Firebase service account is configured (persistent, free tier).
* LocalStore      -> SQLite file fallback for local dev. On Render's free plan this file is
                     wiped on every redeploy, so production must use Firestore.

Both expose the same small interface, so the rest of the app never knows which one it has.
User ids are derived from the e-mail (sha256), which makes "create" atomic and unique in both.
"""
import hashlib
import json
import logging
import os
import sqlite3
import time
import threading

log = logging.getLogger("omsuite.store")


class StoreError(Exception):
    pass


class UserExists(StoreError):
    pass


def norm_email(email: str) -> str:
    return (email or "").strip().lower()


def user_id_for(email: str) -> str:
    return hashlib.sha256(norm_email(email).encode()).hexdigest()[:24]


# --------------------------------------------------------------------------- local
class LocalStore:
    kind = "local"

    def __init__(self, path):
        self.path = str(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            c.execute(
                "CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "ts REAL NOT NULL, actor TEXT, action TEXT, detail TEXT)"
            )

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        return c

    def get_user(self, uid):
        with self._conn() as c:
            r = c.execute("SELECT data FROM users WHERE id=?", (uid,)).fetchone()
        return json.loads(r["data"]) if r else None

    def get_user_by_email(self, email):
        return self.get_user(user_id_for(email))

    def create_user(self, user: dict):
        with self._lock, self._conn() as c:
            try:
                c.execute("INSERT INTO users (id, data) VALUES (?, ?)", (user["id"], json.dumps(user)))
            except sqlite3.IntegrityError:
                raise UserExists(user["email"])
        return user

    def update_user(self, uid, **fields):
        with self._lock, self._conn() as c:
            r = c.execute("SELECT data FROM users WHERE id=?", (uid,)).fetchone()
            if not r:
                raise StoreError("user not found")
            u = json.loads(r["data"])
            u.update(fields)
            c.execute("UPDATE users SET data=? WHERE id=?", (json.dumps(u), uid))
        return u

    def delete_user(self, uid):
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM users WHERE id=?", (uid,))

    def list_users(self):
        with self._conn() as c:
            rows = c.execute("SELECT data FROM users").fetchall()
        return [json.loads(r["data"]) for r in rows]

    def is_empty(self):
        with self._conn() as c:
            return c.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None

    def add_audit(self, actor, action, detail=""):
        with self._conn() as c:
            c.execute("INSERT INTO audit (ts, actor, action, detail) VALUES (?,?,?,?)",
                      (time.time(), actor, action, detail))

    def list_audit(self, limit=150):
        with self._conn() as c:
            rows = c.execute("SELECT ts, actor, action, detail FROM audit ORDER BY id DESC LIMIT ?",
                             (limit,)).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------- firestore
class FirestoreStore:
    kind = "firestore"

    def __init__(self, project_id, credentials_json="", credentials_file="", prefix="omsuite_"):
        import firebase_admin
        from firebase_admin import credentials, firestore

        if not firebase_admin._apps:
            if credentials_json:
                cred = credentials.Certificate(json.loads(credentials_json))
            else:
                cred = credentials.Certificate(credentials_file)
            opts = {"projectId": project_id} if project_id else None
            firebase_admin.initialize_app(cred, opts)
        self._fs = firestore
        self.db = firestore.client()
        self.users = self.db.collection(prefix + "users")
        self.audit = self.db.collection(prefix + "audit")

    def get_user(self, uid):
        d = self.users.document(uid).get()
        return d.to_dict() if d.exists else None

    def get_user_by_email(self, email):
        return self.get_user(user_id_for(email))

    def create_user(self, user: dict):
        from google.api_core.exceptions import AlreadyExists
        try:
            self.users.document(user["id"]).create(user)
        except AlreadyExists:
            raise UserExists(user["email"])
        return user

    def update_user(self, uid, **fields):
        ref = self.users.document(uid)
        ref.update(fields)
        return ref.get().to_dict()

    def delete_user(self, uid):
        self.users.document(uid).delete()

    def list_users(self):
        return [d.to_dict() for d in self.users.stream()]

    def is_empty(self):
        return not any(True for _ in self.users.limit(1).stream())

    def add_audit(self, actor, action, detail=""):
        self.audit.add({"ts": time.time(), "actor": actor, "action": action, "detail": detail})

    def list_audit(self, limit=150):
        q = self.audit.order_by("ts", direction=self._fs.Query.DESCENDING).limit(limit)
        return [d.to_dict() for d in q.stream()]


# --------------------------------------------------------------------------- factory
def build_store(cfg):
    if cfg.FIREBASE_SERVICE_ACCOUNT_JSON or cfg.FIREBASE_SERVICE_ACCOUNT_FILE:
        store = FirestoreStore(
            cfg.FIREBASE_PROJECT_ID,
            cfg.FIREBASE_SERVICE_ACCOUNT_JSON,
            cfg.FIREBASE_SERVICE_ACCOUNT_FILE,
            cfg.FIRESTORE_PREFIX,
        )
        log.info("Using Firebase Firestore store (project %s)", cfg.FIREBASE_PROJECT_ID)
        return store
    path = cfg.DATA_DIR / "omsuite.db"
    if cfg.IS_PROD:
        log.warning("No Firebase credentials set: using local SQLite (%s). "
                    "Data will be lost on every redeploy!", path)
    return LocalStore(path)
