"""OM Suite - unified web app (Phase 1: shell, login/admin, Paid/ToPay Suite)."""
import logging
import os
from datetime import datetime, timedelta, timezone

from flask import Flask, g, jsonify, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from blueprints import admin as admin_bp
from blueprints import bridge_api as bridge_api_bp
from blueprints import billing as billing_bp
from blueprints import branch as branch_bp
from blueprints import customer as customer_bp
from blueprints import dbp as dbp_bp
from blueprints import flows as flows_bp
from blueprints import jobs as jobs_bp
from blueprints import mail as mail_bp
from blueprints import om as om_bp
from blueprints import main as main_bp
from blueprints import paid_topay as ptp_bp
from core import auth
from core.config import Config
from core.modules import BY_ID, can_access, grouped_for
from core.security import csrf_token, verify_csrf
from core.web import wants_json
from core.store import build_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

BLUEPRINT_TO_MODULE = {"paid_topay": "paid_topay", "billing": "adr_net_suite", "admin": "admin_users", "mail": "mail_center"}


def create_app(config_overrides=None, store=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)
    if app.config["TRUST_PROXY"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.extensions["store"] = store or build_store(_CfgView(app.config))

    # DBP engine: master data (directory / branch master) lives under DATA_DIR and is seeded on first start
    os.environ["DBP_MASTER_DIR"] = str(app.config["DATA_DIR"] / "masters")
    from dbp import paths as dbp_paths
    from dbp.workspace import purge_old
    dbp_paths.ensure_seeded()
    from core import bridge_queue, jobs as core_jobs
    import omauto.config as om_config
    core_jobs.configure(app.config["DATA_DIR"])
    om_config.configure(app.config["DATA_DIR"])
    om_config.CN_API = app.config["CN_API_URL"]
    om_config.CUSTOMER_API = app.config["CUSTOMER_URL"]
    bridge_queue.configure(app.config["DATA_DIR"] / "bridge")
    bridge_queue.purge_old()
    purge_old(app.config["DATA_DIR"])
    if not app.config.get("TESTING"):      # the directory xlsx takes ~8 s to parse: do it once at boot, not on a user's click
        import threading
        from dbp import masters as dbp_masters
        threading.Thread(target=dbp_masters.load, daemon=True, name="warm-masters").start()

    try:  # gzip/brotli for the 300 KB tool script
        from flask_compress import Compress
        Compress(app)
    except ImportError:
        pass

    tz = timezone(timedelta(minutes=int(app.config["DISPLAY_TZ_OFFSET_MIN"])))
    app.jinja_env.filters["timestamp"] = lambda v: datetime.fromtimestamp(float(v), tz).strftime("%d %b %Y, %H:%M")

    app.register_blueprint(auth.bp)
    app.register_blueprint(main_bp.bp)
    app.register_blueprint(admin_bp.bp)
    app.register_blueprint(ptp_bp.bp)
    app.register_blueprint(dbp_bp.bp)
    app.register_blueprint(mail_bp.bp)
    app.register_blueprint(flows_bp.bp)
    app.register_blueprint(branch_bp.bp)
    app.register_blueprint(billing_bp.bp)
    app.register_blueprint(customer_bp.bp)
    app.register_blueprint(om_bp.bp)
    app.register_blueprint(jobs_bp.bp)
    app.register_blueprint(bridge_api_bp.bp)

    app.before_request(auth.load_user)
    app.before_request(verify_csrf)

    @app.context_processor
    def inject():
        user = getattr(g, "user", None)
        pending = 0
        if user and user.get("role") == "admin":
            pending = sum(1 for u in app.extensions["store"].list_users() if u["status"] == "pending")
        active = getattr(g, "active_module", None) or BLUEPRINT_TO_MODULE.get(request.blueprint or "")
        return dict(
            app_name=app.config["APP_NAME"], app_version=app.config["APP_VERSION"],
            csrf_token=csrf_token, user=user, active_module=active, pending_count=pending,
            nav_groups=grouped_for(user) if user else [],
            gr_auto_url=app.config["GR_AUTO_URL"],
            asset=lambda f: url_for("static", filename=f, v=_asset_version(app, f)),
        )

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if app.config["IS_PROD"]:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.endpoint != "static" and "Cache-Control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-store"      # never cache logged-in pages
        return resp

    for code, title, text in [
        (400, "Request galat thi", None),
        (403, "Access nahi hai", "Aapko is page ka access nahi mila. Admin se tool access maang lein."),
        (404, "Page nahi mila", "Ye page maujood nahi hai."),
        (429, "Thoda ruk jaiye", "Bahut zyada requests."),
        (500, "Server error", "Kuch gadbad ho gayi. Dobara try karein."),
    ]:
        def handler(e, code=code, title=title, text=text):
            msg = text or getattr(e, "description", "") or ""
            if wants_json():
                return jsonify(success=False, ok=False, error=msg or title, message=msg or title), code
            return render_template("error.html", code=code, title=title, message=msg), code
        app.register_error_handler(code, handler)

    return app


_ASSET_VERSIONS = {}


def _asset_version(app, filename):
    """Short content hash of a static file: any edit changes the URL, so stale cached copies are never reused."""
    import hashlib
    path = os.path.join(app.static_folder, filename)
    try:
        st = os.stat(path)
    except OSError:
        return app.config["APP_VERSION"]
    sig = (st.st_mtime_ns, st.st_size)
    hit = _ASSET_VERSIONS.get(path)
    if not hit or hit[0] != sig:
        with open(path, "rb") as f:
            hit = (sig, hashlib.md5(f.read()).hexdigest()[:10])
        _ASSET_VERSIONS[path] = hit
    return hit[1]


class _CfgView:
    """Lets build_store read dict-style Flask config as attributes."""
    def __init__(self, d): self._d = d
    def __getattr__(self, k): return self._d[k]


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=5000, debug=True)
