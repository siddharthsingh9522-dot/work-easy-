"""TBB Party Mail / Bill Mail / Previous Mail & Format: upload -> review -> confirm -> send, end to end."""
import io
import json
import re

import openpyxl
import pytest
from aiosmtpd.controller import Controller

from conftest import admin_post, do_login, do_signup
from core import bridge_queue as bq
from core.store import user_id_for
from mailflows import directories
from test_bridge import code_of, hdr
from test_mail_center import Sink, save_smtp, smtp_server  # noqa: F401  (fixture re-use)

ME = "admin@x.com"
DIRECTORY = {
    "301": [dict(branch_name="AHMEDABAD", name="Inch Charge", designation="BRANCH INCHARGE", emails=["inch@omlogistics.co.in"]),
            dict(branch_name="AHMEDABAD", name="Bill Person", designation="BILLING EXECUTIVE", emails=["bill@omlogistics.co.in"])],
    "302": [],
}


@pytest.fixture(autouse=True)
def directory(monkeypatch):
    monkeypatch.setattr(directories, "load", lambda: DIRECTORY)


def sheet(headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    b.seek(0)
    return b


TBB_HEADERS = ["B.CODE", "BRANCH", "CN NO", "PARTY CODE", "PARTY NAME", "CN DATE"]
TBB_ROWS = [[301, "AHMEDABAD", "CN1001", "P1", "ACME LTD", "01-01-2026"], [301, "AHMEDABAD", "CN1002", "P1", "ACME LTD", "02-01-2026"],
            [302, "PUNE", "CN2001", "P2", "ZETA CORP", "03-01-2026"], [303, "GOA", "CN3001", "P3", "OMEGA INC", "04-01-2026"]]
BILL_HEADERS = ["B. CODE", "BRANCH", "REGION", "CN", "BILL NO", "CN DATE", "PARTY CODE", "PARTY NAME"]
BILL_ROWS = [[301, "AHMEDABAD", "WEST", "CN1", "B-77", "01-01-2026", "P1", "ACME LTD"], [301, "AHMEDABAD", "WEST", "CN2", "B-78", "02-01-2026", "P1", "ACME LTD"]]


def tok(c):
    return re.search(r'csrf-token" content="([^"]+)"', c.get("/").get_data(as_text=True)).group(1)


def upload(c, slug, headers=TBB_HEADERS, rows=TBB_ROWS, name="report.xlsx"):
    return c.post(f"/mail/{slug}/upload", data={"csrf_token": tok(c), "report_file": (sheet(headers, rows), name)},
                  content_type="multipart/form-data")


def api(c, slug, path, body=None, method="post"):
    fn = getattr(c, method)
    kw = {"json": body or {}} if method == "post" else {}
    return fn(f"/mail/{slug}/api/{path}", headers={"X-CSRF-Token": tok(c)}, **kw)


def status(c, slug="tbb"):
    return api(c, slug, "status", method="get").get_json()


@pytest.fixture()
def me(admin):
    return admin


@pytest.fixture()
def tbb(me):
    assert upload(me, "tbb").status_code == 302
    return me


def extension_finishes(app, info, ok=True, error="boom"):
    """What the Thunderbird extension does: claim the queued jobs, then report success/failure."""
    c = app.test_client()
    jobs = c.get("/api/bridge/poll?limit=25", headers=hdr(info)).get_json()["jobs"]
    for j in jobs:
        body = {"result": {"ok": True}} if ok else {"status": "error", "error": error}
        c.post(f"/api/bridge/job/{j['id']}/result", json=body, headers=hdr(info))
    return jobs


# ================================================================= TBB
def test_tbb_upload_builds_rows_with_directory_recipients(tbb):
    rows = status(tbb)["rows"]
    assert set(rows) == {"301", "302", "303"} and all(r["status"] == "DRAFT" for r in rows.values())
    page = tbb.get("/mail/tbb/preview").get_data(as_text=True)
    assert "inch@omlogistics.co.in" in page and "bill@omlogistics.co.in" in page and "AHMEDABAD" in page


def test_tbb_mail_content_matches_the_original_format(tbb):
    m = api(tbb, "tbb", "mail/301", method="get").get_json()
    assert m["subject"] == "PROVIDE THE TBB CODE & MODIFY THE SAME............. GR:-CN1001/CN1002 M/S- ACME LTD"
    assert "Please check the GR" in m["text"] and "CN1001" in m["text"] and "ACME LTD" in m["html"]
    assert m["attachment"] == "TBB_301_AHMEDABAD.xlsx" and f"Regards,\n{'Admin'}" in m["text"]


def test_idempotency_a_sent_mail_is_never_sent_twice(app, tbb):
    info = code_of(tbb)
    api(tbb, "tbb", "confirm", {"selected": ["301"]})
    r = api(tbb, "tbb", "send/301", {"email": "inch@omlogistics.co.in", "via": "tb"}).get_json()
    assert r["ok"] and r["status"] == "queued_thunderbird"
    assert status(tbb)["rows"]["301"]["status"] == "SENDING"
    extension_finishes(app, info)
    assert status(tbb)["rows"]["301"]["status"] == "SENT"                   # result folded in from the extension
    again = api(tbb, "tbb", "send/301", {"email": "inch@omlogistics.co.in", "via": "tb"}).get_json()
    assert again["status"] == "already_sent" and bq.poll(ME, 10) == []        # nothing new queued
    upload(tbb, "tbb")                                                        # same file again
    assert status(tbb)["rows"]["301"]["status"] == "SENT"
    upload(tbb, "tbb", rows=TBB_ROWS + [[301, "AHMEDABAD", "CN1003", "P1", "ACME LTD", "05-01-2026"]])
    assert status(tbb)["rows"]["301"]["status"] == "DRAFT"                    # different content -> fresh draft


def test_test_mode_goes_to_the_test_address_and_never_marks_rows_sent(tbb):
    api(tbb, "tbb", "confirm", {"selected": ["301"]})
    r = api(tbb, "tbb", "send/301", {"email": "inch@omlogistics.co.in", "via": "tb", "test": True, "test_to": "me@x.com"}).get_json()
    assert r["ok"]
    (job,) = bq.poll(ME, 10)
    p = job["payload"]
    assert p["to"] == ["me@x.com"] and p["cc"] == [] and p["subject"].startswith("[TEST]")
    assert status(tbb)["rows"]["301"]["status"] == "QUEUED"                    # untouched: the real send is still possible
    assert api(tbb, "tbb", "send/301", {"email": "x@y.com", "test": True, "test_to": ""}).status_code == 400


def test_send_all_needs_confirmed_and_reviewed_rows_and_skips_the_rest(tbb):
    api(tbb, "tbb", "confirm", {"selected": ["301", "302"]})
    st = status(tbb)["rows"]
    assert st["301"]["status"] == "QUEUED" and st["303"]["status"] == "SKIPPED"
    none = api(tbb, "tbb", "send_all", {"test": True, "test_to": "me@x.com", "items": {}})
    assert none.status_code == 400 and bq.poll(ME, 10) == []                    # not reviewed yet -> nothing goes out
    assert api(tbb, "tbb", "review/301", {"email": "inch@omlogistics.co.in"}).get_json()["status"] == "manual_ok"
    assert api(tbb, "tbb", "review/302", {}).status_code == 400                 # no recipient -> cannot be reviewed
    r = api(tbb, "tbb", "send_all", {"test": True, "test_to": "me@x.com", "via": "tb",
                                     "items": {"301": {"email": "inch@omlogistics.co.in", "cc": "", "bcc": ""}}}).get_json()
    assert r["ok"] and r["queued"] == 1
    assert [j["payload"]["ref"]["code"] for j in bq.poll(ME, 10)] == ["301"]    # skipped / unreviewed rows never sent


def test_failed_send_can_be_retried(app, tbb):
    info = code_of(tbb)
    api(tbb, "tbb", "confirm", {"selected": ["301"]})
    api(tbb, "tbb", "send/301", {"email": "inch@omlogistics.co.in", "via": "tb"})
    extension_finishes(app, info, ok=False, error="Thunderbird: no identity")
    row = status(tbb)["rows"]["301"]
    assert row["status"] == "FAILED" and "no identity" in row["error"]
    assert api(tbb, "tbb", "retry_failed").get_json()["retried"] == ["301"]
    assert status(tbb)["rows"]["301"]["status"] == "QUEUED"


def test_invalid_recipient_is_rejected_and_nothing_is_queued(tbb):
    api(tbb, "tbb", "confirm", {"selected": ["301"]})
    r = api(tbb, "tbb", "send/301", {"email": "not-an-email", "via": "tb"})
    assert r.status_code == 400 and "Invalid email" in r.get_json()["error"] and bq.poll(ME, 10) == []
    assert status(tbb)["rows"]["301"]["status"] == "QUEUED"


def test_smtp_send_marks_sent_and_attaches_the_xlsx(tbb, smtp_server):
    sink, port = smtp_server
    save_smtp(tbb, port)
    api(tbb, "tbb", "confirm", {"selected": ["301"]})
    r = api(tbb, "tbb", "send/301", {"email": "inch@omlogistics.co.in", "cc": "bill@omlogistics.co.in", "via": "smtp"}).get_json()
    assert r["ok"] and r["status"] == "sent" and status(tbb)["rows"]["301"]["status"] == "SENT"
    env = sink.got[0]
    assert set(env.rcpt_tos) == {"inch@omlogistics.co.in", "bill@omlogistics.co.in"}
    assert b"TBB_301_AHMEDABAD.xlsx" in env.content and b"text/html" in env.content


def test_smtp_without_settings_marks_the_row_failed(tbb):
    api(tbb, "tbb", "confirm", {"selected": ["301"]})
    r = api(tbb, "tbb", "send/301", {"email": "inch@omlogistics.co.in", "via": "smtp"})
    assert r.status_code == 500 and "SMTP settings" in r.get_json()["error"]
    assert status(tbb)["rows"]["301"]["status"] == "FAILED"


def test_ai_fix_repairs_a_typo_domain_and_fills_missing_emails_without_ai(tbb):
    fixed = api(tbb, "tbb", "ai_fix/301", {"email": "Inch@omlogistic.co.in "}).get_json()
    assert fixed["ok"] and fixed["email"] == "inch@omlogistics.co.in"
    assert fixed["status"] == "unavailable"                                    # AI is not configured; the fix itself worked
    filled = api(tbb, "tbb", "ai_fix/301", {"email": ""}).get_json()
    assert "inch@omlogistics.co.in" in filled["email"]
    nothing = api(tbb, "tbb", "ai_fix/303", {"email": ""}).get_json()
    assert nothing["status"] == "unresolved"
    assert api(tbb, "tbb", "ai_check/301", {"email": "inch@omlogistics.co.in"}).get_json()["status"] == "unavailable"


def test_tbb_rejects_bad_uploads(me):
    r = me.post("/mail/tbb/upload", data={"csrf_token": tok(me), "report_file": (io.BytesIO(b"x"), "a.csv")},
                content_type="multipart/form-data", follow_redirects=True)
    assert "Sirf .xls" in r.get_data(as_text=True)
    r = upload(me, "tbb", headers=["foo", "bar"], rows=[[1, 2]])
    assert "header nahi mila" in me.get("/mail/tbb/").get_data(as_text=True)


# ================================================================= Bill
def test_bill_flow_builds_subject_and_csv_attachment(me):
    upload(me, "bill", BILL_HEADERS, BILL_ROWS)
    m = api(me, "bill", "mail/301", method="get").get_json()
    assert "B-77" in m["subject"] and "B-78" in m["subject"] and m["attachment"] == "Bill_301.csv"
    api(me, "bill", "confirm", {"selected": ["301"]})
    api(me, "bill", "send/301", {"email": "inch@omlogistics.co.in", "via": "tb"})
    (job,) = bq.poll(ME, 5)
    assert job["payload"]["attachment"]["content_type"] == "text/csv" and job["payload"]["attachment"]["name"] == "Bill_301.csv"


def test_workflows_do_not_mix_their_rows(me):
    upload(me, "tbb")
    upload(me, "bill", BILL_HEADERS, BILL_ROWS)
    assert set(status(me, "tbb")["rows"]) == {"301", "302", "303"} and set(status(me, "bill")["rows"]) == {"301"}


# ================================================================= Previous Mail & Format
def analyze(c, subject="Bill {B-1} pending for ACME", body="Dear sir,\nBill B-1 of ACME LTD is pending.\nRegards"):
    csv = io.BytesIO(b"Branch Code,Party,Bill No\n301,ACME LTD,B-77\n301,ACME LTD,B-78\n302,ZETA CORP,B-90\n")
    return c.post("/mail/previous/analyze", data={"csrf_token": tok(c), "old_subject": subject, "old_body": body,
                                                  "old_to": "x@y.com", "data_file": (csv, "new.csv")},
                  content_type="multipart/form-data")


def test_previous_mail_template_review_confirm_and_groups(me):
    assert analyze(me).status_code == 302
    review = me.get("/mail/previous/review").get_data(as_text=True)
    assert "Template review" in review and "Branch Code" in review and "AI configure nahi" in review or "Confidence" in review
    tpl = {"subject_template": "Bill {{Bill No}} pending - {{Party}}", "body_template": "Dear sir,\n{{Party}} bill {{Bill No}} pending.",
           "key_col": "Branch Code", "to": "ops@x.com", "cc": "", "bcc": ""}
    assert me.post("/mail/previous/confirm", data={"csrf_token": tok(me), **tpl}).status_code == 302
    rows = status(me, "previous")["rows"]
    assert set(rows) == {"301", "302"}
    m301 = api(me, "previous", "mail/301", method="get").get_json()
    assert m301["subject"] == "Bill B-77 pending - ACME LTD" and "Is group ke saare rows" in m301["text"] and "B-78" in m301["text"]
    assert api(me, "previous", "mail/302", method="get").get_json()["subject"] == "Bill B-90 pending - ZETA CORP"
    assert api(me, "previous", "mail/301", method="get").get_json()["attachment"].endswith(".csv")


def test_previous_mail_needs_either_an_eml_or_manual_text(me):
    r = me.post("/mail/previous/analyze", data={"csrf_token": tok(me), "data_file": (io.BytesIO(b"a,b\n1,2\n"), "n.csv")},
                content_type="multipart/form-data", follow_redirects=True)
    assert "eml upload karein YA" in r.get_data(as_text=True)


def test_previous_mail_find_recipients_through_thunderbird(app, me):
    info = code_of(me)
    analyze(me)
    me.post("/mail/previous/confirm", data={"csrf_token": tok(me), "subject_template": "S {{Bill No}}", "body_template": "B",
                                            "key_col": "Branch Code", "to": "", "cc": "", "bcc": ""})
    job_id = api(me, "previous", "find/301").get_json()["job_id"]
    c = app.test_client()
    (job,) = [j for j in c.get("/api/bridge/poll?types=match", headers=hdr(info)).get_json()["jobs"] if j["id"] == job_id]
    assert job["job_type"] == "match" and job["payload"]["party_names"] == ["ACME LTD"] and job["payload"]["bill_nos"] == ["B-77", "B-78"]
    pending = api(me, "previous", f"find_result/{job_id}", method="get").get_json()
    assert pending["status"] == "CLAIMED" and pending["profile"] == {}
    c.post(f"/api/bridge/job/{job_id}/result", headers=hdr(info),
           json={"result": {"profile": {"to": ["old@x.com"], "cc": ["boss@x.com"], "bcc": [], "confidence": 88}}})
    done = api(me, "previous", f"find_result/{job_id}", method="get").get_json()
    assert done["status"] == "DONE" and done["profile"]["to"] == ["old@x.com"]


# ================================================================= access, privacy, errors
def test_each_tool_needs_its_own_module(app, me):
    u = app.test_client()
    do_signup(u, "Ravi", "ravi@x.com")
    admin_post(me, f"/admin/users/{user_id_for('ravi@x.com')}/action", action="approve", modules=["tbb_mail"])
    do_login(u, "ravi@x.com")
    assert u.get("/mail/tbb/").status_code == 200
    for path in ("/mail/bill/", "/mail/previous/", "/mail/bill/preview"):
        assert u.get(path).status_code == 403, path
    assert api(u, "bill", "status", method="get").status_code == 403
    assert u.get("/mail/nonsense/").status_code == 404


def test_tbb_only_user_can_use_the_thunderbird_bridge(app, me):
    u = app.test_client()
    do_signup(u, "Ravi", "ravi@x.com")
    admin_post(me, f"/admin/users/{user_id_for('ravi@x.com')}/action", action="approve", modules=["tbb_mail"])
    do_login(u, "ravi@x.com")
    info = code_of(u)                                                       # setup page reachable without Mail Center access
    assert app.test_client().get("/api/bridge/status", headers=hdr(info)).status_code == 200
    assert u.get("/dbp/mail/extension.zip").status_code == 200


def test_users_never_see_each_others_rows_or_history(app, tbb):
    api(tbb, "tbb", "confirm", {"selected": ["301"]})
    api(tbb, "tbb", "send/301", {"email": "inch@omlogistics.co.in", "via": "tb"})
    u = app.test_client()
    do_signup(u, "Ravi", "ravi@x.com")
    admin_post(tbb, f"/admin/users/{user_id_for('ravi@x.com')}/action", action="approve", modules=["tbb_mail"])
    do_login(u, "ravi@x.com")
    assert status(u)["rows"] == {}
    assert "inch@omlogistics.co.in" not in u.get("/mail/history").get_data(as_text=True)
    assert "TBB" in tbb.get("/mail/history").get_data(as_text=True) or "tbb" in tbb.get("/mail/history").get_data(as_text=True)


def test_api_errors_are_json(me):
    r = me.post("/mail/tbb/api/confirm", json={"selected": []})          # no CSRF header
    assert r.status_code == 400 and r.mimetype == "application/json"
    assert api(me, "tbb", "mail/NOPE", method="get").status_code == 404
    assert api(me, "tbb", "mail/NOPE", method="get").mimetype == "application/json"


def test_pages_render(me):
    upload(me, "tbb")
    for path in ("/mail/tbb/", "/mail/bill/", "/mail/previous/", "/mail/tbb/preview", "/mail/history"):
        assert me.get(path).status_code == 200, path
    assert 'id="flow"' in me.get("/mail/tbb/preview").get_data(as_text=True)


def test_rows_without_a_directory_email_show_an_empty_box_not_the_word_none(tbb):
    page = tbb.get("/mail/tbb/preview").get_data(as_text=True)
    row302 = page.split('data-code="302"')[1].split("</tr>")[0]
    assert 'class="em" value=""' in row302 and "None" not in row302
    assert status(tbb)["rows"]["302"]["review"] == "pending"
