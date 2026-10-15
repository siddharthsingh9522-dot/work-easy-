"""Thunderbird bridge: per-user tokens, queue, attachments, CSRF-free extension API."""
import base64
import json
import re
import time

import pytest

from conftest import admin_post, csrf, do_login, do_signup
from core import bridge_queue as bq
from core.store import user_id_for

MAIL = {"to_list": ["a@x.com"], "cc_list": ["am@x.com"], "bcc_list": [], "subject": "S", "body": "b",
        "html_body": "<p>b</p>", "attachment_bytes": b"PK-data", "attachment_name": "R.xlsx",
        "branch_code": 7, "branch_name": "N"}


def code_of(client):
    html = client.get("/dbp/mail/bridge").get_data(as_text=True)
    raw = re.search(r'id="codeBox"[^>]*>([^<]+)<', html).group(1).strip()
    return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))


def hdr(info):
    return {"X-Thunderbird-User": info["user"], "X-Thunderbird-Token": info["token"]}


@pytest.fixture()
def me(admin):
    info = code_of(admin)
    return admin, info


def test_connection_code_contains_url_user_token(me):
    _, info = me
    assert info["user"] == "admin@x.com" and len(info["token"]) == 40 and info["url"].startswith("http")


def test_status_needs_valid_token(app, me):
    c = app.test_client()                                   # no cookies: the extension never has a session
    _, info = me
    assert c.get("/api/bridge/status", headers=hdr(info)).status_code == 200
    assert c.get("/api/bridge/status", headers={**hdr(info), "X-Thunderbird-Token": "bad"}).status_code == 401
    assert c.get("/api/bridge/status", headers={**hdr(info), "X-Thunderbird-User": "ghost@x.com"}).status_code == 401
    assert c.get("/api/bridge/status").status_code == 401


def test_poll_claims_once_and_result_updates_batch(app, me):
    c = app.test_client()
    _, info = me
    for _ in range(7):
        bq.enqueue_send("admin@x.com", MAIL, "B1", "me@x.com")
    jobs = c.get("/api/bridge/poll?limit=5", headers=hdr(info)).get_json()["jobs"]
    assert len(jobs) == 5 and jobs[0]["job_type"] == "send"
    p = jobs[0]["payload"]
    assert p["to"] == ["a@x.com"] and p["cc"] == ["am@x.com"] and p["identity_email"] == "me@x.com"
    assert c.get(p["attachment"]["url"], headers=hdr(info)).data == b"PK-data"
    # result posts carry no CSRF token: the extension authenticates with its token only
    assert c.post(f"/api/bridge/job/{jobs[0]['id']}/result", json={"result": {"ok": 1}}, headers=hdr(info)).status_code == 200
    c.post(f"/api/bridge/job/{jobs[1]['id']}/result", json={"status": "error", "error": "boom"}, headers=hdr(info))
    assert c.get("/api/bridge/poll?limit=5&types=match", headers=hdr(info)).get_json()["jobs"] == []
    assert len(c.get("/api/bridge/poll?limit=5", headers=hdr(info)).get_json()["jobs"]) == 2
    st = {r["job"]: r["status"] for r in bq.batch_status("admin@x.com", "B1")}
    assert st[jobs[0]["id"]] == "DONE" and st[jobs[1]["id"]] == "ERROR"
    assert bq.retry_failed("admin@x.com", "B1") == 1


def test_stale_claim_goes_back_to_queue(app, me, monkeypatch):
    c = app.test_client()
    _, info = me
    bq.enqueue_send("admin@x.com", MAIL, "B2")
    assert len(c.get("/api/bridge/poll", headers=hdr(info)).get_json()["jobs"]) == 1
    assert c.get("/api/bridge/poll", headers=hdr(info)).get_json()["jobs"] == []
    monkeypatch.setattr(bq, "CLAIM_TIMEOUT", -1)             # pretend the extension died after claiming
    assert len(c.get("/api/bridge/poll", headers=hdr(info)).get_json()["jobs"]) == 1


def test_jobs_and_attachments_are_private_to_their_owner(app, admin):
    other = app.test_client()
    do_signup(other, "Ravi", "ravi@x.com")
    admin_post(admin, f"/admin/users/{user_id_for('ravi@x.com')}/action", action="approve", modules=["mail_center"])
    do_login(other, "ravi@x.com")
    mine, theirs = code_of(admin), code_of(other)
    bq.enqueue_send("admin@x.com", MAIL, "B3")
    c = app.test_client()
    assert c.get("/api/bridge/poll", headers=hdr(theirs)).get_json()["jobs"] == []      # Ravi sees nothing of admin's
    job = c.get("/api/bridge/poll", headers=hdr(mine)).get_json()["jobs"][0]
    assert c.get(job["payload"]["attachment"]["url"], headers=hdr(theirs)).status_code == 404


def test_extension_user_needs_mail_center_access_and_active_account(app, admin):
    u = app.test_client()
    do_signup(u, "Ravi", "ravi@x.com")
    admin_post(admin, f"/admin/users/{user_id_for('ravi@x.com')}/action", action="approve", modules=["mail_center"])
    do_login(u, "ravi@x.com")
    info = code_of(u)
    c = app.test_client()
    assert c.get("/api/bridge/status", headers=hdr(info)).status_code == 200
    admin_post(admin, f"/admin/users/{user_id_for('ravi@x.com')}/action", action="save_modules", modules=["paid_topay"])
    assert c.get("/api/bridge/status", headers=hdr(info)).status_code == 401           # access removed
    admin_post(admin, f"/admin/users/{user_id_for('ravi@x.com')}/action", action="save_modules", modules=["mail_center"])
    assert c.get("/api/bridge/status", headers=hdr(info)).status_code == 200
    admin_post(admin, f"/admin/users/{user_id_for('ravi@x.com')}/action", action="block")
    assert c.get("/api/bridge/status", headers=hdr(info)).status_code == 401           # blocked


def test_regenerate_kills_the_old_token(app, me):
    admin, old = me
    assert app.test_client().get("/api/bridge/status", headers=hdr(old)).status_code == 200
    tok = re.search(r'name="csrf_token" value="([^"]+)"', admin.get("/dbp/mail/bridge").get_data(as_text=True)).group(1)
    admin.post("/dbp/mail/bridge/regenerate", data={"csrf_token": tok})
    new = code_of(admin)
    assert new["token"] != old["token"]
    c = app.test_client()
    assert c.get("/api/bridge/status", headers=hdr(old)).status_code == 401
    assert c.get("/api/bridge/status", headers=hdr(new)).status_code == 200


def test_bridge_page_shows_connected_after_a_poll(app, me):
    admin, info = me
    assert "abhi tak connect nahi hua" in admin.get("/dbp/mail/bridge").get_data(as_text=True)
    app.test_client().get("/api/bridge/poll", headers=hdr(info))
    assert "Extension connected" in admin.get("/dbp/mail/bridge").get_data(as_text=True)


def test_extension_zip_has_manifest_at_root(me):
    import io, zipfile
    admin, _ = me
    z = zipfile.ZipFile(io.BytesIO(admin.get("/dbp/mail/extension.zip").data))
    names = z.namelist()
    assert "manifest.json" in names and "background.js" in names and "options.js" in names
    assert json.loads(z.read("manifest.json"))["version"] == "2.0.0"


def test_cors_preflight_for_extension(app):
    r = app.test_client().open("/api/bridge/poll", method="OPTIONS")
    assert r.status_code in (200, 204) and r.headers["Access-Control-Allow-Origin"] == "*"
    assert "X-Thunderbird-Token" in r.headers["Access-Control-Allow-Headers"]
