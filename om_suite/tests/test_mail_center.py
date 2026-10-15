"""Mail Center end-to-end: drafts, region/full plans, Thunderbird queue, SMTP, safety rules."""
import socket

import pandas as pd
import pytest
from aiosmtpd.controller import Controller

from conftest import admin_post, do_login, do_signup
from core import bridge_queue as bq
from core.store import user_id_for
from dbp import masters
from dbp.masters import Masters
from test_dbp_web import post, token, upload

ME = "admin@x.com"


def directory():
    return pd.DataFrame([
        dict(BRANCH_CODE=101, NAME="Inch", DESIGNATION="BRANCH INCHARGE", EMAIL="inch@x.com"),
        dict(BRANCH_CODE=101, NAME="Mgr", DESIGNATION="BRANCH MANAGER", EMAIL="mgr@x.com"),
        dict(BRANCH_CODE=101, NAME="Bill", DESIGNATION="BILLING", EMAIL="bill@x.com"),
        dict(BRANCH_CODE=101, NAME="Area", DESIGNATION="AREA MANAGER", EMAIL="area@x.com"),
    ])


@pytest.fixture(autouse=True)
def synthetic_directory(monkeypatch):
    monkeypatch.setattr(masters, "load", lambda: Masters(directory_df=directory(), branch_master_map={}))


@pytest.fixture()
def ready(admin):
    upload(admin, "/dbp/report/generate", "/dbp/report", {"region": "Test Region"})
    return admin


def prepare_region(c, **extra):
    data = dict(cat="All Pending", skip="1", dbp_extra="regdbp@x.com", **extra)
    return post(c, "/dbp/mail/region/prepare", "/dbp/mail?tab=region", **data)


def send(c, kind="region", **kw):
    data = dict(kind=kw.pop("kind", kind), method="tb", test="1", test_to="me@x.com", confirm="1")
    data.update(kw)
    data = {k: v for k, v in data.items() if v is not None}
    return post(c, "/dbp/mail/send", f"/dbp/mail?tab={kind}", **data)


def jobs(owner=ME):
    return bq.poll(owner, 25)


def test_needs_report_first(admin):
    assert "Report Builder" in admin.get("/dbp/mail").get_data(as_text=True)


def test_single_branch_draft_and_downloads(ready):
    page = ready.get("/dbp/mail?tab=one&code=101").get_data(as_text=True)
    assert "sandbox" in page and "inch@x.com" in page and "Subject" in page      # preview is a sandboxed iframe
    eml = ready.get("/dbp/mail/eml?code=101")
    assert eml.status_code == 200 and eml.mimetype == "message/rfc822" and b"inch@x.com" in eml.data
    assert ready.get("/dbp/mail/sheet?code=101").data[:2] == b"PK"
    assert ready.get("/dbp/mail?tab=one&code=999999").status_code == 400                # unknown branch


def test_region_plan_shows_ready_branch(ready):
    prepare_region(ready)
    page = ready.get("/dbp/mail?tab=region").get_data(as_text=True)
    assert "Ready to send" in page and "READY" in page and "inch@x.com" in page
    assert "area@x.com" not in page.split("Preview")[0]            # area managers never get the branch mail


def test_thunderbird_send_in_test_mode_only_reaches_the_test_address(ready):
    prepare_region(ready)
    r = send(ready, identity="me@x.com")
    assert r.status_code == 302 and "batch=" in r.headers["Location"]
    (job,) = jobs()
    p = job["payload"]
    assert p["to"] == ["me@x.com"] and p["cc"] == [] and p["bcc"] == [] and p["subject"].startswith("[TEST]")
    assert p["identity_email"] == "me@x.com" and p["attachment"]["name"].endswith(".xlsx")


def test_real_mode_goes_to_the_directory_recipients(ready):
    prepare_region(ready)
    send(ready, test=None, test_to="")
    (job,) = jobs()
    p = job["payload"]
    assert p["to"] == ["inch@x.com"] and {"mgr@x.com", "bill@x.com", "regdbp@x.com"} <= set(p["cc"])
    assert not p["subject"].startswith("[TEST]")


def test_send_is_refused_without_confirmation_or_test_address(ready):
    prepare_region(ready)
    send(ready, confirm=None)
    send(ready, test_to="")
    assert jobs() == []


def test_send_without_a_plan_does_nothing(ready):
    send(ready)
    assert jobs() == []


def test_region_dbp_is_compulsory(ready):
    post(ready, "/dbp/mail/region/prepare", "/dbp/mail?tab=region", cat="All Pending", skip="1")
    page = ready.get("/dbp/mail?tab=region").get_data(as_text=True)
    assert "BLOCKED" in page and "Region DBP select" in page
    send(ready)
    assert jobs() == []                                            # nothing READY -> nothing queued


def test_full_report_plan_and_queue(ready):
    post(ready, "/dbp/mail/full/prepare", "/dbp/mail?tab=full", max="400")
    page = ready.get("/dbp/mail?tab=full").get_data(as_text=True)
    assert "Area Managers (CC)" in page and "Recipient list" in page
    send(ready, kind="full")
    got = jobs()
    assert got and all(j["payload"]["to"] == ["me@x.com"] for j in got)


def test_progress_cancel_and_retry(ready, app):
    prepare_region(ready)
    batch = send(ready).headers["Location"].split("batch=")[1].split("&")[0]
    tok = token(ready, "/dbp/mail")
    prog = ready.get(f"/dbp/mail/progress?batch={batch}").get_json()
    assert prog["total"] == 1 and prog["pending"] == 1
    assert ready.post("/dbp/mail/batch", json={"batch": batch, "action": "cancel"}, headers={"X-CSRF-Token": tok}).get_json()["n"] == 1
    assert ready.get(f"/dbp/mail/progress?batch={batch}").get_json()["cancelled"] == 1


def test_progress_is_private_to_the_sender(app, ready):
    prepare_region(ready)
    batch = send(ready).headers["Location"].split("batch=")[1].split("&")[0]
    other = app.test_client()
    do_signup(other, "Ravi", "ravi@x.com")
    admin_post(ready, f"/admin/users/{user_id_for('ravi@x.com')}/action", action="approve", modules=["mail_center"])
    do_login(other, "ravi@x.com")
    assert other.get(f"/dbp/mail/progress?batch={batch}").get_json()["total"] == 0


# ------------------------------------------------------------------ SMTP
class Sink:
    def __init__(self):
        self.got = []

    async def handle_DATA(self, server, session, envelope):
        self.got.append(envelope)
        return "250 OK"


@pytest.fixture()
def smtp_server():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    sink = Sink()
    c = Controller(sink, hostname="127.0.0.1", port=port)
    c.start()
    yield sink, port
    c.stop()


def save_smtp(c, srv_port, **kw):
    data = dict(form="smtp", action="save", host="127.0.0.1", port=str(srv_port), security="none", smtp_user="",
                smtp_password="", from_addr="dbp@om.test", delay="0")
    data.update(kw)
    return post(c, "/settings", "/settings", **data)


def test_smtp_settings_verify_and_send(ready, smtp_server):
    sink, port = smtp_server
    save_smtp(ready, port)
    assert "SMTP verify hokar save" in ready.get("/settings").get_data(as_text=True)
    prepare_region(ready)
    send(ready, method="smtp")
    assert len(sink.got) == 1
    env = sink.got[0]
    assert env.rcpt_tos == ["me@x.com"] and env.mail_from == "dbp@om.test" and b"[TEST]" in env.content
    page = ready.get("/dbp/mail?tab=region&method=smtp").get_data(as_text=True)
    assert "SMTP send log" in page and "SENT" in page


def test_smtp_without_saved_settings_is_refused(ready, smtp_server):
    sink, _ = smtp_server
    prepare_region(ready)
    send(ready, method="smtp")
    assert sink.got == [] and jobs() == []


def test_smtp_unreachable_server_saves_with_warning(ready):
    page = save_smtp(ready, 9).get_data(as_text=True)              # port 9: nothing listens
    page = ready.get("/settings").get_data(as_text=True)
    assert "verify nahi hua" in page


def test_smtp_rejects_bad_input_and_never_echoes_password(app, ready, smtp_server):
    _, srv = smtp_server
    save_smtp(ready, srv, port="99999")
    assert not app.extensions["store"].get_user(user_id_for(ME)).get("smtp")          # invalid port -> nothing saved
    save_smtp(ready, srv, smtp_password="s3cret-pw")
    stored = app.extensions["store"].get_user(user_id_for(ME))["smtp"]
    assert "s3cret-pw" not in str(stored) and stored["password_enc"]                   # encrypted at rest
    assert "s3cret-pw" not in ready.get("/settings").get_data(as_text=True)            # never sent back to the browser


def test_mail_center_respects_module_access(app, admin):
    u = app.test_client()
    do_signup(u, "Ravi", "ravi@x.com")
    admin_post(admin, f"/admin/users/{user_id_for('ravi@x.com')}/action", action="approve", modules=["paid_topay"])
    do_login(u, "ravi@x.com")
    for path in ("/dbp/mail", "/dbp/mail/bridge", "/dbp/mail/extension.zip", "/dbp/mail/progress?batch=x"):
        assert u.get(path).status_code == 403, path
