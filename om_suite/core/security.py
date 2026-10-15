"""CSRF, login rate-limit, password policy and safe redirects."""
import hmac
import secrets
import threading
import time

from flask import abort, request, session


# ------------------------------------------------------------------ CSRF
def csrf_token() -> str:
    tok = session.get("_csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["_csrf"] = tok
    return tok


def verify_csrf():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.path.startswith("/api/bridge/"):      # token-authenticated extension API (no cookies involved)
        return
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    want = session.get("_csrf", "")
    if not sent or not want or not hmac.compare_digest(sent, want):
        abort(400, "Security check fail ho gaya. Page refresh karke dobara try karein.")


# ------------------------------------------------------------------ rate limit
class RateLimiter:
    """Tiny in-memory limiter: `max_fails` failures within `window` seconds -> blocked."""

    def __init__(self, max_fails=6, window=600):
        self.max_fails, self.window = max_fails, window
        self._hits, self._lock = {}, threading.Lock()

    def _prune(self, key, now):
        self._hits[key] = [t for t in self._hits.get(key, []) if now - t < self.window]
        return self._hits[key]

    def blocked_for(self, key) -> int:
        now = time.time()
        with self._lock:
            hits = self._prune(key, now)
            if len(hits) >= self.max_fails:
                return int(self.window - (now - hits[0])) + 1
        return 0

    def fail(self, key):
        with self._lock:
            self._prune(key, time.time()).append(time.time())

    def reset(self, key):
        with self._lock:
            self._hits.pop(key, None)


# ------------------------------------------------------------------ misc
def validate_password(pw: str):
    if len(pw or "") < 8:
        return "Password kam se kam 8 characters ka rakhein."
    return None


def safe_next(url):
    """Only allow same-site relative redirects."""
    if url and url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    return None


def temp_password() -> str:
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(10))
