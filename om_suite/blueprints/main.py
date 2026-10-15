"""Dashboard, settings, coming-soon pages, health check and PWA files."""
from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import time

from core import insights
from core.auth import invalidate, login_required, new_pv, store
from core.modules import BY_ID, can_access, grouped_for
from core.security import validate_password
from dbp import mailcfg

bp = Blueprint("main", __name__)


@bp.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html", groups=grouped_for(g.user))


@bp.route("/m/<module_id>")
@login_required
def module_page(module_id):
    m = BY_ID.get(module_id)
    if not m or not can_access(g.user, module_id):
        abort(404)
    if m["status"] == "live":
        return redirect(url_for(m["endpoint"]))
    if m["status"] == "external":
        url = current_app.config["GR_AUTO_URL"]
        return redirect(url) if url else render_template("module_soon.html", m=m, pending_link=True)
    return render_template("module_soon.html", m=m, pending_link=False)


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        form = request.form.get("form")
        if form == "profile":
            name = (request.form.get("name") or "").strip()
            if 2 <= len(name) <= 60:
                store().update_user(g.user["id"], name=name)
                invalidate(g.user["id"])
                flash("Profile update ho gaya.", "ok")
            else:
                flash("Naam 2 se 60 characters ka hona chahiye.", "error")
        elif form == "password":
            cur, new, new2 = (request.form.get(k, "") for k in ("current", "new", "confirm"))
            u = store().get_user(g.user["id"])
            err = None
            if not check_password_hash(u["pw_hash"], cur):
                err = "Current password galat hai."
            elif validate_password(new):
                err = validate_password(new)
            elif new != new2:
                err = "Naye passwords match nahi karte."
            if err:
                flash(err, "error")
            else:
                pv = new_pv()
                store().update_user(u["id"], pw_hash=generate_password_hash(new), pv=pv)
                session["pv"] = pv            # stay logged in here, every other device logs out
                invalidate(u["id"])
                flash("Password change ho gaya. Baaki devices se logout ho gaya.", "ok")
        elif form == "insights":
            _save_insights()
        elif form == "smtp":
            _save_smtp()
        return redirect(url_for("main.settings"))
    return render_template("settings.html", u=g.user, smtp=mailcfg.public_view(current_app.config, g.user))


def _save_smtp():
    cfg_app, uid = current_app.config, g.user["id"]
    if request.form.get("action") == "remove":
        store().update_user(uid, smtp=None)
        invalidate(uid)
        flash("SMTP settings hata di gayi.", "ok")
        return
    cfg, err = mailcfg.build_from_form(cfg_app, request.form, g.user)
    if err:
        flash(err, "error")
        return
    state, msg = mailcfg.verify(cfg)
    if state == "rejected":
        flash(f"SMTP login/sender reject hua - save nahi hua. ({msg})", "error")
        return
    store().update_user(uid, smtp=mailcfg.to_record(cfg_app, cfg))
    invalidate(uid)
    if state == "ok":
        flash("SMTP verify hokar save ho gaya.", "ok")
    else:
        flash(f"SMTP save ho gaya, par abhi connect nahi ho paya - verify nahi hua. ({msg})", "warn")


def _save_insights():
    cfg, uid = current_app.config, g.user["id"]
    if request.form.get("action") == "remove":
        store().update_user(uid, insights=None)
        insights.forget(uid)
        invalidate(uid)
        flash("OmInsights login hata diya gaya.", "ok")
        return
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    if not username or not password:
        flash("OmInsights username aur password dono daalein.", "error")
        return
    verified = True
    try:
        token = insights.login(cfg["OMINSIGHTS_URL"], username, password, timeout=20)
        insights.remember_token(uid, token)
    except insights.InsightsError as e:
        if e.status in (401, 429):
            flash(str(e), "error")          # wrong credentials -> do not save
            return
        verified = False                     # server cannot reach OmInsights: save anyway, warn
        insights.forget(uid)
    store().update_user(uid, insights={"username": username, "password_enc": insights.encrypt(cfg, password),
                                       "updated": time.time()})
    invalidate(uid)
    if verified:
        flash("OmInsights login verify hokar save ho gaya.", "ok")
    else:
        flash("Login save ho gaya, par abhi OmInsights se connect nahi ho paya - verify nahi hua.", "warn")


@bp.route("/healthz")
def healthz():
    return jsonify(status="ok", store=current_app.extensions["store"].kind)


@bp.route("/manifest.webmanifest")
def manifest():
    resp = jsonify(
        name=current_app.config["APP_NAME"], short_name=current_app.config["APP_NAME"],
        start_url="/", scope="/", display="standalone",
        background_color="#F4F7FB", theme_color="#2563EB",
        icons=[
            {"src": "/static/img/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/img/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/img/icon-maskable-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ])
    resp.headers["Content-Type"] = "application/manifest+json"
    return resp


@bp.route("/sw.js")
def service_worker():
    resp = send_from_directory(current_app.static_folder, "js/sw.js", mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    return resp
