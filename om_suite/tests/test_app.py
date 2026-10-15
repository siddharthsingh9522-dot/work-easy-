from werkzeug.security import check_password_hash

from conftest import PW, admin_post, csrf, do_login, do_signup
from core.store import user_id_for


def uid(email):
    return user_id_for(email)


def test_admin_email_gets_admin_and_dashboard(admin):
    r = admin.get("/")
    assert r.status_code == 200 and b"Dashboard" in r.data
    assert admin.get("/admin/users").status_code == 200


def test_unauthenticated_redirects_to_login(app):
    r = app.test_client().get("/paid-topay/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_regular_signup_is_pending_and_cannot_login(app):
    c = app.test_client()
    assert do_signup(c, "Ravi", "ravi@x.com").status_code == 200
    r = do_login(c, "ravi@x.com")
    assert r.status_code == 403 and b"pending" in r.data


def test_approve_with_module_access(app, admin):
    u = app.test_client()
    do_signup(u, "Ravi", "ravi@x.com")
    r = admin_post(admin, f"/admin/users/{uid('ravi@x.com')}/action", action="approve", modules="paid_topay")
    assert r.status_code == 302
    assert do_login(u, "ravi@x.com").status_code == 302
    page = u.get("/paid-topay/")
    assert page.status_code == 200 and b'class="ptp-app"' in page.data
    assert u.get("/admin/users").status_code == 403           # not an admin
    assert u.get("/m/dbp_report").status_code == 404          # tool not granted


def test_block_logs_out_existing_session(app, admin):
    u = app.test_client()
    do_signup(u, "Ravi", "ravi@x.com")
    admin_post(admin, f"/admin/users/{uid('ravi@x.com')}/action", action="approve", modules="paid_topay")
    do_login(u, "ravi@x.com")
    assert u.get("/").status_code == 200
    admin_post(admin, f"/admin/users/{uid('ravi@x.com')}/action", action="block")
    assert u.get("/").status_code == 302                       # kicked out immediately
    assert b"block" in do_login(u, "ravi@x.com").data


def test_csrf_is_required(app):
    r = app.test_client().post("/login", data=dict(email="a@b.com", password="x"))
    assert r.status_code == 400


def test_open_redirect_is_ignored(app, admin):
    fresh = app.test_client()
    r = do_login(fresh, "admin@x.com", next="//evil.com")
    assert r.status_code == 302 and "evil.com" not in r.headers["Location"]
    fresh2 = app.test_client()
    r = do_login(fresh2, "admin@x.com", next="/paid-topay/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/paid-topay/")


def test_reset_password_flow(app, admin):
    u = app.test_client()
    do_signup(u, "Ravi", "ravi@x.com")
    admin_post(admin, f"/admin/users/{uid('ravi@x.com')}/action", action="approve", modules="paid_topay")
    admin_post(admin, f"/admin/users/{uid('ravi@x.com')}/action", action="reset_password")
    stored = app.extensions["store"].get_user(uid("ravi@x.com"))
    assert not check_password_hash(stored["pw_hash"], PW)       # old password dead
    page = admin.get("/admin/users").get_data(as_text=True)
    assert "temporary password" in page


def test_cannot_demote_or_block_self_or_last_admin(app, admin):
    r = admin_post(admin, f"/admin/users/{uid('admin@x.com')}/action", action="block")
    assert r.status_code == 302
    assert app.extensions["store"].get_user(uid("admin@x.com"))["status"] == "approved"


def test_login_rate_limit(app):
    c = app.test_client()
    do_signup(c, "Ravi", "ravi@x.com")
    codes = [do_login(c, "ravi@x.com", pw="wrong-pass").status_code for _ in range(8)]
    assert 429 in codes


def test_static_tool_assets_and_scoping(admin):
    assert admin.get("/static/modules/paid_topay/ptp.js").status_code == 200
    assert admin.get("/static/modules/paid_topay/cnquery.html").status_code == 200
    css = admin.get("/static/modules/paid_topay/ptp.css").get_data(as_text=True)
    assert ".ptp-app .upload-zone" in css and "prefers-color-scheme" not in css
    import re
    assert not re.search(r"(^|\})\s*(body|html)\s*\{", css)       # nothing leaks onto the shell


def test_pwa_and_health(app):
    c = app.test_client()
    assert c.get("/healthz").get_json()["status"] == "ok"
    assert c.get("/manifest.webmanifest").get_json()["display"] == "standalone"
    assert c.get("/sw.js").status_code == 200


def test_every_page_renders_for_admin(admin):
    from core.modules import MODULES
    for path in ["/", "/settings", "/admin/users", "/admin/audit", "/paid-topay/", "/login", "/signup"]:
        r = admin.get(path)
        assert r.status_code in (200, 302), path
    for m in MODULES:
        r = admin.get(f"/m/{m['id']}")
        assert r.status_code in (200, 302), m["id"]
    assert b"Link pending" in admin.get("/").data or b"Opens in new tab" in admin.get("/").data


def test_gr_auto_tile_links_out_when_url_set(app):
    app.config["GR_AUTO_URL"] = "https://gr.example.com"
    c = app.test_client()
    do_signup(c, "Admin", "admin@x.com"); do_login(c, "admin@x.com")
    assert b'href="https://gr.example.com"' in c.get("/").data
    assert c.get("/m/gr_auto").headers["Location"] == "https://gr.example.com"
