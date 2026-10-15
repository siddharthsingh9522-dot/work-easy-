"""Login / signup / logout, current-user loading and access decorators."""
import re
import threading
import time
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .modules import can_access
from .security import RateLimiter, safe_next, validate_password
from .web import wants_json
from .store import UserExists, norm_email, user_id_for

bp = Blueprint("auth", __name__)
limiter = RateLimiter(max_fails=6, window=600)
_DUMMY_HASH = generate_password_hash("dummy-password-for-timing")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# ----------------------------------------------------------------------- user cache
_cache, _lock = {}, threading.Lock()


def store():
    return current_app.extensions["store"]


def get_user_cached(uid):
    ttl = current_app.config["USER_CACHE_TTL"]
    now = time.time()
    with _lock:
        hit = _cache.get(uid)
    if ttl and hit and now - hit[0] < ttl:
        return hit[1]
    u = store().get_user(uid)
    with _lock:
        _cache[uid] = (now, u)
    return u


def invalidate(uid=None):
    with _lock:
        if uid is None:
            _cache.clear()
        else:
            _cache.pop(uid, None)


def new_pv():
    import secrets
    return secrets.token_hex(8)


def load_user():
    """before_request: resolve the logged-in user; any block/reset/removal logs them out."""
    g.user = None
    uid = session.get("uid")
    if not uid:
        return
    u = get_user_cached(uid)
    if u and u.get("status") == "approved" and u.get("pv") == session.get("pv"):
        g.user = u
    else:
        session.clear()


# ----------------------------------------------------------------------- decorators
def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not g.user:
            if wants_json():          # page scripts expect JSON, never a redirect to the login page
                from flask import jsonify
                msg = "Login expire ho gaya - page refresh karke dobara login karein."
                return jsonify(success=False, ok=False, error=msg, message=msg), 401
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*a, **kw)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*a, **kw):
        if g.user.get("role") != "admin":
            abort(403)
        return view(*a, **kw)
    return wrapped


def module_required(module_id):
    def deco(view):
        @wraps(view)
        @login_required
        def wrapped(*a, **kw):
            if not can_access(g.user, module_id):
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco


def any_module_required(*module_ids):
    """Allow users who may use at least one of the given tools (e.g. every mail tool shares one Thunderbird bridge)."""
    def deco(view):
        @wraps(view)
        @login_required
        def wrapped(*a, **kw):
            if not any(can_access(g.user, m) for m in module_ids):
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco


# ----------------------------------------------------------------------- views
@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("main.dashboard"))
    nxt = safe_next(request.values.get("next"))
    if request.method == "POST":
        email = norm_email(request.form.get("email"))
        pw = request.form.get("password", "")
        key = f"{request.remote_addr}|{email}"
        wait = limiter.blocked_for(key)
        if wait:
            flash(f"Bahut zyada galat attempts. {wait} second baad try karein.", "error")
            return render_template("login.html", email=email, next=nxt), 429
        u = store().get_user_by_email(email)
        ok = check_password_hash(u["pw_hash"] if u else _DUMMY_HASH, pw) and u is not None
        if not ok:
            limiter.fail(key)
            flash("Email ya password galat hai.", "error")
            return render_template("login.html", email=email, next=nxt), 401
        if u["status"] == "pending":
            flash("Aapka account abhi admin approval ke liye pending hai.", "warn")
            return render_template("login.html", email=email, next=nxt), 403
        if u["status"] == "blocked":
            flash("Aapka account block kar diya gaya hai. Admin se sampark karein.", "error")
            return render_template("login.html", email=email, next=nxt), 403
        limiter.reset(key)
        session.clear()
        session.permanent = True
        session["uid"], session["pv"] = u["id"], u["pv"]
        store().update_user(u["id"], last_login=time.time())
        invalidate(u["id"])
        store().add_audit(email, "login", request.remote_addr or "")
        return redirect(nxt or url_for("main.dashboard"))
    return render_template("login.html", email="", next=nxt)


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if g.user:
        return redirect(url_for("main.dashboard"))
    cfg = current_app.config
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = norm_email(request.form.get("email"))
        pw = request.form.get("password", "")
        pw2 = request.form.get("confirm", "")
        err = None
        if not (2 <= len(name) <= 60):
            err = "Naam 2 se 60 characters ka hona chahiye."
        elif not _EMAIL_RE.match(email):
            err = "Sahi email daalein."
        elif validate_password(pw):
            err = validate_password(pw)
        elif pw != pw2:
            err = "Dono password match nahi karte."
        if err:
            flash(err, "error")
            return render_template("signup.html", name=name, email=email), 400
        is_admin = email in cfg["ADMIN_EMAILS"] or (not cfg["ADMIN_EMAILS"] and store().is_empty())
        user = dict(
            id=user_id_for(email), name=name, email=email, pw_hash=generate_password_hash(pw),
            role="admin" if is_admin else "user",
            status="approved" if is_admin else "pending",
            modules=["*"] if is_admin else [],
            pv=new_pv(), created=time.time(), last_login=None,
        )
        try:
            store().create_user(user)
        except UserExists:
            flash("Ye email pehle se registered hai.", "error")
            return render_template("signup.html", name=name, email=email), 409
        store().add_audit(email, "signup", "admin" if is_admin else "pending")
        if is_admin:
            flash("Admin account ban gaya! Ab login karein.", "ok")
            return redirect(url_for("auth.login"))
        return render_template("signup_done.html", email=email)
    return render_template("signup.html", name="", email="")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
