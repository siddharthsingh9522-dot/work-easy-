"""
OmInsights (Metabase) access, done server-side for the logged-in user.

This replaces the old local `ominsights_proxy.py`: every user saves THEIR OWN OmInsights login in
Settings (stored encrypted), and the app logs in on their behalf. It is deliberately NOT an open
proxy - only the whitelisted card ids may be queried, via POST .../api/card/<id>/query/json.
"""
import base64
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request

from cryptography.fernet import Fernet, InvalidToken

MAX_PARALLEL = 3                       # protects RAM/threads on small instances
_slots = threading.BoundedSemaphore(MAX_PARALLEL)
_tokens, _lock = {}, threading.Lock()  # user id -> Metabase session token (in memory only)


class InsightsError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ credential encryption
def _fernet(cfg):
    secret = cfg.get("CREDENTIAL_KEY") or cfg["SECRET_KEY"]
    raw = hashlib.sha256(("omsuite-credentials|" + secret).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt(cfg, text: str) -> str:
    return _fernet(cfg).encrypt(text.encode()).decode()


def decrypt(cfg, token: str):
    try:
        return _fernet(cfg).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError, AttributeError):
        return None


def saved_credentials(cfg, user):
    """(username, password) saved by this user, or None."""
    blob = (user or {}).get("insights") or {}
    if not blob.get("username") or not blob.get("password_enc"):
        return None
    pw = decrypt(cfg, blob["password_enc"])
    return (blob["username"], pw) if pw else None


# ------------------------------------------------------------------ Metabase session
def _read_error(e: urllib.error.HTTPError) -> str:
    try:
        raw = e.read().decode("utf-8", "ignore")
        try:
            j = json.loads(raw)
            return str(j.get("message") or j.get("error") or raw)[:300]
        except ValueError:
            return raw[:300]
    except Exception:
        return ""


def login(base_url, username, password, timeout=30) -> str:
    req = urllib.request.Request(
        base_url + "/api/session", method="POST",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            token = json.loads(r.read().decode()).get("id")
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):
            raise InsightsError("OmInsights login galat hai - Settings me username/password check karein.", 401)
        if e.code == 429:
            raise InsightsError("OmInsights ne bahut zyada login attempts roke hain - thodi der baad try karein.", 429)
        raise InsightsError(f"OmInsights login error (HTTP {e.code}).", 502)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise InsightsError("Server OmInsights tak pahunch nahi paya (network/VPN). "
                            "Agar OmInsights sirf office network par khulta hai to ye server use nahi kar sakta.", 502)
    if not token:
        raise InsightsError("OmInsights ne session nahi diya.", 502)
    return token


def remember_token(uid, token):
    with _lock:
        _tokens[uid] = token


def forget(uid):
    with _lock:
        _tokens.pop(uid, None)


def _token_for(cfg, uid, creds):
    with _lock:
        tok = _tokens.get(uid)
    if tok:
        return tok
    tok = login(cfg["OMINSIGHTS_URL"], creds[0], creds[1])
    remember_token(uid, tok)
    return tok


def _post(base_url, card_id, body, token, timeout):
    req = urllib.request.Request(
        f"{base_url}/api/card/{int(card_id)}/query/json", data=body, method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json",
                 "X-Metabase-Session": token})
    return urllib.request.urlopen(req, timeout=timeout)


def open_query(cfg, uid, creds, card_id, body):
    """Returns an open upstream response (caller streams + closes it). Re-logs in once if the session expired."""
    base, timeout = cfg["OMINSIGHTS_URL"], cfg["OMINSIGHTS_TIMEOUT"]
    token = _token_for(cfg, uid, creds)
    for attempt in (1, 2):
        try:
            return _post(base, card_id, body, token, timeout)
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 1:
                forget(uid)
                token = login(base, creds[0], creds[1])
                remember_token(uid, token)
                continue
            detail = _read_error(e)
            raise InsightsError(f"OmInsights ne HTTP {e.code} diya" + (f": {detail}" if detail else "."),
                                e.code if 400 <= e.code < 600 else 502)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise InsightsError("OmInsights se data lete waqt connection tut gaya / time out ho gaya.", 504)


def acquire_slot() -> bool:
    return _slots.acquire(blocking=False)


def release_slot():
    try:
        _slots.release()
    except ValueError:
        pass
