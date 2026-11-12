"""FTL Pending Mail (upload -> review -> confirm -> send) + the send-safety rules shared with TBB / Bill."""
import datetime

import openpyxl
import pytest

from conftest import admin_post, do_login, do_signup
from core import bridge_queue as bq
from core.store import user_id_for
from dbp import mail_sender
from mailflows import builders, directories, directory_parser, engine
from mailflows.engine import Flow
from dbp.workspace import Workspace
from test_bridge import code_of
from test_mail_center import Sink, save_smtp, smtp_server  # noqa: F401  (fixture re-use)
from test_mail_flows import ME, api, extension_finishes, sheet, status, tok

DIRECTORY = {
    "301": [dict(branch_name="AHMEDABAD", name="Inch Charge", designation="BRANCH INCHARGE", emails=["inch@omlogistics.co.in"]),
            dict(branch_name="AHMEDABAD", name="Bill Person", designation="BILLING EXECUTIVE", emails=["bill@omlogistics.co.in"]),
            dict(branch_name="AHMEDABAD", name="Dbp Person", designation="DBP", emails=["dbp@omlogistics.co.in"]),
            dict(branch_name="AHMEDABAD", name="Delivery Boy", designation="DELIVERY BOY", emails=["boy@omlogistics.co.in"])],
    "302": [],
}
FTL_HEADERS = ["B.CODE", "BRANCH", "REGION", "CN_CN_NO", "CN_DATE", "PARTY_CODE", "PARTY_NAME", "CN_ACTUAL_WEIGHT", "LOAD_TYPE"]
FTL_ROWS = [
    [301, "AHMEDABAD", "WEST", 7147163, "25.10.2026", 840607, "STEEL & METALS", 1383, "FTL"],
    [301, "AHMEDABAD", "WEST", 7153900, "25.10.2026", 840607, "STEEL & METALS", 1416.7, "FTL"],
    [301, "AHMEDABAD", "WEST", 7147162, "25.10.2026", 641194, "AIM TECHNOLOGIES PVT LTD", 18, "FTL"],
    [301, "AHMEDABAD", "WEST", 7000001, "25.10.2026", 1, "NOT FTL LTD", 5, "PTL"],       # other load type -> ignored
    [301, "AHMEDABAD", "WEST", 7147163, "25.10.2026", 840607, "STEEL & METALS", 1383, "FTL"],   # duplicate docket
    [301, "AHMEDABAD", "WEST", None, "25.10.2026", 1, "BLANK CN LTD", 5, "FTL"],         # blank CN (makes the column float)
    [302, "PUNE", "WEST", 5704261006500, "26.10.2026", 111, "ZETA CORP", 9, "FTL"],
]


def upload_ftl(c, headers=None, rows=None, load_type="ftl"):
    """Like the upload form: the report + the Load type choice (ftl / part / all)."""
    return c.post("/mail/ftl/upload", data={"csrf_token": tok(c), "load_type": load_type,
                                            "report_file": (sheet(headers or FTL_HEADERS, rows or FTL_ROWS), "report.xlsx")},
                  content_type="multipart/form-data")


@pytest.fixture(autouse=True)
def directory(monkeypatch):
    monkeypatch.setattr(directories, "load", lambda: DIRECTORY)


@pytest.fixture()
def me(admin):
    return admin


@pytest.fixture()
def ftl(me):
    assert upload_ftl(me).status_code == 302
    return me


def ftl_upload(c, rows=FTL_ROWS, load_type="ftl"):
    return upload_ftl(c, FTL_HEADERS, rows, load_type)


# ================================================================= upload / recipients
def test_ftl_upload_keeps_only_ftl_dockets_and_picks_incharge_billing_dbp(ftl):
    rows = status(ftl, "ftl")["rows"]
    assert set(rows) == {"301", "302"} and all(r["status"] == "DRAFT" for r in rows.values())
    page = ftl.get("/mail/ftl/preview").get_data(as_text=True)
    assert "inch@omlogistics.co.in" in page and "bill@omlogistics.co.in" in page and "dbp@omlogistics.co.in" in page
    assert "boy@omlogistics.co.in" not in page                                   # delivery boy is not part of FTL billing


def test_ftl_upload_tells_what_it_ignored(me):
    ftl_upload(me)
    page = me.get("/mail/ftl/preview").get_data(as_text=True)                   # the flash shows on the next page
    assert "6 FTL docket(s)" not in page and "4 FTL docket(s), 2 branch(es)" in page
    assert "1 row(s) FTL ki nahi thi - ignore" in page and "1 duplicate docket(s)" in page and "1 row(s) bina CN" in page


def test_ftl_headers_with_underscores_and_title_rows_are_understood(me, tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["FTL pending billing", "03-11-2026"])
    ws.append([])
    ws.append(FTL_HEADERS)
    ws.append(FTL_ROWS[0])
    path = tmp_path / "r.xlsx"
    wb.save(path)
    from mailflows import ftl_parser
    branches, stats = ftl_parser.parse_ftl_report(str(path))
    gr = branches["301"]["grs"][0]
    assert (gr["cn"], gr["party_code"], gr["party_name"], gr["cn_date"]) == ("7147163", "840607", "STEEL & METALS", "25.10.2026")
    assert branches["301"]["branch_name_sheet"] == "AHMEDABAD" and branches["301"]["region"] == "WEST" and stats["kept"] == 1


def test_ftl_rejects_a_report_without_ftl_dockets_or_without_cn_column(me):
    ftl_upload(me, rows=[[301, "AHMEDABAD", "WEST", 1, "25.10.2026", 1, "X", 1, "PTL"]])
    assert "koi FTL docket nahi mila" in me.get("/mail/ftl/").get_data(as_text=True)
    upload_ftl(me, headers=["foo", "bar"], rows=[[1, 2]])
    assert "CN_CN_NO" in me.get("/mail/ftl/").get_data(as_text=True)


# ================================================================= mail content
def test_ftl_mail_subject_text_and_html(ftl):
    m = api(ftl, "ftl", "mail/301", method="get").get_json()
    assert m["subject"] == "FTL PENDING BILL............. GR:-7147163/7147162 M/S- STEEL & METALS, AIM TECHNOLOGIES PVT LTD"
    text = m["text"]
    assert text.startswith("Dear Sir,\n\nKindly check the below FTL dockets and generate the bills at the earliest.\n\n")
    assert text.index("Kindly check") < text.index("B.CODE") < text.index("7147163\t25.10.2026") < text.index("If there is any issue")
    assert text.index("If there is any issue") < text.index("Please treat this as a priority") and text.endswith("Regards,\nAdmin")
    assert "NOT FTL LTD" not in text and "BLANK CN" not in text and text.count("7147163") == 1
    assert m["html"].count("<th ") == len(FTL_HEADERS) and "STEEL &amp; METALS" in m["html"] and m["attachment"] == ""
    assert api(ftl, "ftl", "mail/302", method="get").get_json()["subject"].endswith("GR:-5704261006500 M/S- ZETA CORP")


def test_ftl_numbers_have_no_dot_zero_even_when_a_column_has_blank_cells(ftl):
    text = api(ftl, "ftl", "mail/302", method="get").get_json()["text"]
    assert "5704261006500\t26.10.2026\t111\tZETA CORP\t9\tFTL" in text and ".0" not in text


# ================================================================= sending
def test_ftl_smtp_send_has_the_dockets_in_the_body_and_no_attachment(ftl, smtp_server):
    sink, port = smtp_server
    save_smtp(ftl, port)
    api(ftl, "ftl", "confirm", {"selected": ["301"]})
    r = api(ftl, "ftl", "send/301", {"email": "inch@omlogistics.co.in", "cc": "bill@omlogistics.co.in", "via": "smtp"}).get_json()
    assert r["ok"] and r["status"] == "sent" and status(ftl, "ftl")["rows"]["301"]["status"] == "SENT"
    content = sink.got[0].content
    assert b"FTL PENDING BILL" in content and b"text/html" in content and b"Content-Disposition: attachment" not in content


def test_a_row_that_is_already_sending_cannot_be_sent_again(app, ftl):
    info = code_of(ftl)
    api(ftl, "ftl", "confirm", {"selected": ["301"]})
    first = api(ftl, "ftl", "send/301", {"email": "inch@omlogistics.co.in", "via": "tb"}).get_json()
    assert first["status"] == "queued_thunderbird"
    again = api(ftl, "ftl", "send/301", {"email": "inch@omlogistics.co.in", "via": "tb"})
    assert again.status_code == 409 and "SENDING" in again.get_json()["error"]
    assert len(bq.recent(ME, 60)) == 1                                            # exactly one job was queued
    extension_finishes(app, info)
    assert status(ftl, "ftl")["rows"]["301"]["status"] == "SENT"
    assert api(ftl, "ftl", "send/301", {"email": "inch@omlogistics.co.in", "via": "tb"}).get_json()["status"] == "already_sent"


def test_send_all_skips_a_row_another_request_is_already_sending(ftl, monkeypatch):
    api(ftl, "ftl", "confirm", {"selected": ["301", "302"]})
    for c in ("301", "302"):
        api(ftl, "ftl", f"review/{c}", {"email": "x@omlogistics.co.in"})
    real = Flow.claim_for_send
    monkeypatch.setattr(Flow, "claim_for_send", lambda self, code: False if code == "301" else real(self, code))
    r = api(ftl, "ftl", "send_all", {"via": "tb", "items": {"301": {"email": "inch@omlogistics.co.in"}, "302": {"email": "z@omlogistics.co.in"}}}).get_json()
    assert r["ok"] and r["queued"] == 1 and "301" in r["skipped"]


def test_smtp_partial_delivery_is_sent_with_a_warning_not_failed(ftl, smtp_server, monkeypatch):
    sink, port = smtp_server
    save_smtp(ftl, port)
    monkeypatch.setattr(mail_sender, "send_batch", lambda cfg, mails: [{"status": "PARTIAL", "error": "Refused by server: {'bad@x.com': (550, b'no')}"}])
    api(ftl, "ftl", "confirm", {"selected": ["301"]})
    r = api(ftl, "ftl", "send/301", {"email": "inch@omlogistics.co.in", "via": "smtp"}).get_json()
    assert r["ok"] and r["status"] == "sent" and "Refused" in r["warning"]
    assert status(ftl, "ftl")["rows"]["301"]["status"] == "SENT"                         # a retry would have mailed the others twice
    assert api(ftl, "ftl", "retry_failed").get_json()["retried"] == []


def test_an_interrupted_smtp_send_ends_as_failed_with_a_check_sent_folder_note(tmp_path, monkeypatch):
    flow = Flow(Workspace(str(tmp_path), "u1"), "ftl", "a@b.com")
    br = {"301": dict(branch_code="301", branch_name_sheet="X", columns=["CN"], grs=[dict(cn="1", party_name="P", cells=["1"])], email="a@x.com")}
    flow.load(br, "r.xlsx", {"301": "fp"})
    flow.confirm(["301"])
    assert flow.claim_for_send("301") is True and flow.claim_for_send("301") is False      # second claim is refused
    assert flow.refresh_from_bridge()["301"]["status"] == "SENDING"                        # recent: still in progress
    monkeypatch.setattr(engine, "STALE_SENDING_SECONDS", -1)
    op = flow.refresh_from_bridge()["301"]
    assert op["status"] == "FAILED" and "Sent folder" in op["error"]


# ================================================================= pending mail is a daily reminder
def test_the_same_report_is_not_resent_the_same_day_but_is_a_fresh_draft_the_next_day(app, ftl, monkeypatch):
    info = code_of(ftl)
    api(ftl, "ftl", "confirm", {"selected": ["301"]})
    api(ftl, "ftl", "send/301", {"email": "inch@omlogistics.co.in", "via": "tb"})
    extension_finishes(app, info)
    ftl_upload(ftl)
    assert status(ftl, "ftl")["rows"]["301"]["status"] == "SENT"                          # same day, same data -> untouched

    class Tomorrow(datetime.date):
        @classmethod
        def today(cls):
            return datetime.date.today() + datetime.timedelta(days=1)
    monkeypatch.setattr(builders, "date", Tomorrow)
    ftl_upload(ftl)
    assert status(ftl, "ftl")["rows"]["301"]["status"] == "DRAFT"


# ================================================================= access
def test_ftl_needs_its_own_module(app, me):
    u = app.test_client()
    do_signup(u, "Ravi", "ravi@x.com")
    admin_post(me, f"/admin/users/{user_id_for('ravi@x.com')}/action", action="approve", modules=["tbb_mail"])
    do_login(u, "ravi@x.com")
    assert u.get("/mail/ftl/").status_code == 403 and api(u, "ftl", "status", method="get").status_code == 403
    admin_post(me, f"/admin/users/{user_id_for('ravi@x.com')}/action", action="save_modules", modules=["tbb_mail", "ftl_mail"])
    assert u.get("/mail/ftl/").status_code == 200


# ================================================================= TBB parser: whole numbers stay whole
def test_tbb_report_cn_has_no_dot_zero_when_the_column_has_a_blank_cell(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["B.CODE", "BRANCH", "CN NO", "PARTY CODE", "PARTY NAME", "CN DATE"])
    ws.append([301, "AHMEDABAD", 5704261006476, 685297, "STANMARK", datetime.datetime(2026, 9, 26)])
    ws.append([301, "AHMEDABAD", None, 685298, "ABC", datetime.datetime(2026, 9, 27)])
    path = tmp_path / "t.xlsx"
    wb.save(path)
    gr = directory_parser.parse_report(str(path))["301"]["grs"][0]
    assert gr["cn"] == "5704261006476" and gr["cn_date"] == "26-09-2026"


# ================================================================= Lorry Type = the FTL / PART-LOAD column
LORRY_HEADERS = ["B.CODE", "BRANCH", "CN", "CN Date", "Party Code", "Party Name", "Source Code", "Destination Code", "Lorry Type"]


def lorry_rows():
    return [[301, "AHMEDABAD", 7147163, "25.10.2026", 840607, "STEEL & METALS", 508, 301, "FTL"],
            [301, "AHMEDABAD", 7153900, "25.10.2026", 840607, "STEEL & METALS", 511, 1331, "PART-LOAD"],
            [301, "AHMEDABAD", 7147162, "25.10.2026", 641194, "AIM TECHNOLOGIES PVT LTD", 508, 826, "PART-LOAD\n"],   # stray newline in the cell
            [302, "PUNE", 5704261006500, "26.10.2026", 111, "ZETA CORP", 1012, 2105, " ftl "]]


def test_load_type_ftl_leaves_part_load_out(me):
    upload_ftl(me, LORRY_HEADERS, lorry_rows())
    page = me.get("/mail/ftl/preview").get_data(as_text=True)
    assert "2 FTL docket(s), 2 branch(es)" in page and "2 row(s) FTL ki nahi thi - ignore" in page
    m = api(me, "ftl", "mail/301", method="get").get_json()
    assert "7147163" in m["text"] and "7153900" not in m["text"] and "7147162" not in m["text"] and "PART-LOAD" not in m["text"]
    assert m["subject"].endswith("GR:-7147163 M/S- STEEL & METALS")


# ================================================================= recipients = B.CODE + Source Code + Destination Code
def test_recipients_come_from_all_three_codes_and_a_repeated_person_is_kept_once(me, monkeypatch):
    def p(name, des, email):
        return dict(branch_name="X", name=name, designation=des, emails=[email])
    monkeypatch.setattr(directories, "load", lambda: {
        "301": [p("Inch", "BRANCH INCHARGE", "inch@om.in"), p("Bill", "BILLING EXECUTIVE", "bill@om.in")],
        "508": [p("Inch", "BRANCH INCHARGE", "INCH@om.in"), p("Src", "DBP", "src@om.in")],       # Inch again (other case) -> once
        "1331": []})                                                                           # destination with nobody in the directory
    rows = [[301, "AHMEDABAD", 7147163, "25.10.2026", 1, "A", 508, 301, "FTL"],                 # destination == B.CODE -> looked up once
            [301, "AHMEDABAD", 7153900, "25.10.2026", 1, "A", 508, 1331, "FTL"]]
    upload_ftl(me, LORRY_HEADERS, rows)
    page = me.get("/mail/ftl/preview").get_data(as_text=True)
    assert 'value="inch@om.in, bill@om.in, src@om.in"' in page                                 # B.CODE people first, then source, each once
    assert "[B.CODE]" in page and "[Src 508]" in page and "1 duplicate hataya" in page and "directory me nahi: 1331" in page
    assert page.count("inch@om.in") == 1


def test_a_code_that_has_nobody_falls_back_like_before(me, monkeypatch):
    monkeypatch.setattr(directories, "load", lambda: {"301": [dict(branch_name="X", name="Inch", designation="MANAGER", emails=["m@om.in"])]})
    upload_ftl(me, LORRY_HEADERS, [[301, "AHMEDABAD", 1, "25.10.2026", 1, "A", 999, 998, "FTL"]])
    assert 'value="m@om.in"' in me.get("/mail/ftl/preview").get_data(as_text=True)               # single-contact fallback of the B.CODE


def test_a_long_recipient_list_is_never_cut_off(me):
    ftl_upload(me)
    long_to = ", ".join(f"person{i:02d}@omlogistics.co.in" for i in range(40))                  # ~1100 characters
    api(me, "ftl", "send_all", {"test": True, "test_to": "me@x.com", "items": {"301": {"email": long_to, "cc": "", "bcc": ""}}})
    assert "person39@omlogistics.co.in" in me.get("/mail/ftl/preview").get_data(as_text=True)


# ================================================================= CC / BCC for all rows at once
def rows_cc(c, slug="ftl"):
    html = c.get(f"/mail/{slug}/preview").get_data(as_text=True)
    import re
    return {m.group(1): (m.group(2), m.group(3)) for m in re.finditer(r'<tr data-code="(\w+)">.*?class="cc" value="([^"]*)".*?class="bcc" value="([^"]*)"', html, re.S)}


def test_bulk_cc_bcc_goes_to_every_row_adds_without_duplicates_and_can_replace(ftl):
    api(ftl, "ftl", "bulk_cc", {"cc": "boss@om.in", "bcc": "me@om.in"})
    assert rows_cc(ftl) == {"301": ("boss@om.in", "me@om.in"), "302": ("boss@om.in", "me@om.in")}
    api(ftl, "ftl", "bulk_cc", {"cc": "Boss@om.in, other@om.in"})                                # boss already there; bcc untouched
    assert rows_cc(ftl)["301"] == ("boss@om.in, other@om.in", "me@om.in")
    api(ftl, "ftl", "bulk_cc", {"cc": "only@om.in", "bcc": "", "replace": True})
    assert rows_cc(ftl)["302"] == ("only@om.in", "")                                              # replaced, bcc cleared
    r = api(ftl, "ftl", "bulk_cc", {"cc": "not-an-email"})
    assert r.status_code == 400 and "not-an-email" in r.get_json()["error"]
    assert api(ftl, "ftl", "bulk_cc", {"cc": "", "bcc": ""}).status_code == 400                   # nothing to apply


def test_bulk_cc_never_touches_a_row_that_is_already_sent(app, ftl):
    info = code_of(ftl)
    api(ftl, "ftl", "confirm", {"selected": ["301"]})
    api(ftl, "ftl", "send/301", {"email": "inch@omlogistics.co.in", "via": "tb"})
    extension_finishes(app, info)
    r = api(ftl, "ftl", "bulk_cc", {"cc": "boss@om.in"}).get_json()
    assert r["updated"] == 1 and list(r["rows"]) == ["302"]
    assert rows_cc(ftl)["301"][0] == ""


def test_saved_default_cc_bcc_is_filled_into_the_next_upload(ftl):
    api(ftl, "ftl", "bulk_cc", {"cc": "boss@om.in", "bcc": "me@om.in", "save_default": True})
    ftl_upload(ftl, rows=FTL_ROWS + [[303, "GOA", "WEST", 9000001, "27.10.2026", 5, "OMEGA", 1, "FTL"]])
    assert rows_cc(ftl)["303"] == ("boss@om.in", "me@om.in")
    page = ftl.get("/mail/ftl/preview").get_data(as_text=True)
    assert 'id="allCc" value="boss@om.in"' in page                                                  # the panel remembers it too
    api(ftl, "ftl", "bulk_cc", {"cc": "", "bcc": "", "save_default": True})                       # clear the default
    ftl_upload(ftl, rows=FTL_ROWS + [[304, "DIU", "WEST", 9000002, "27.10.2026", 5, "OMEGA", 1, "FTL"]])
    assert rows_cc(ftl)["304"] == ("", "")


def test_the_bulk_panel_is_not_shown_for_previous_mail_and_the_route_is_closed(me):
    assert api(me, "previous", "bulk_cc", {"cc": "a@b.com"}).status_code == 404


def test_cc_and_bcc_never_repeat_an_address_that_is_already_in_to(ftl, smtp_server):
    sink, port = smtp_server
    save_smtp(ftl, port)
    api(ftl, "ftl", "confirm", {"selected": ["301"]})
    r = api(ftl, "ftl", "send/301", {"email": "inch@om.in", "cc": "inch@om.in, boss@om.in", "bcc": "boss@om.in, me@om.in", "via": "smtp"}).get_json()
    assert r["ok"]
    env = sink.got[0]
    assert sorted(env.rcpt_tos) == ["boss@om.in", "inch@om.in", "me@om.in"]                        # each address exactly once
    assert b"Cc: boss@om.in" in env.content and b"inch@om.in, boss" not in env.content


# ================================================================= Load type chosen at upload: FTL / PART-LOAD / both
def test_load_type_part_load_keeps_only_part_load_but_the_mail_still_says_ftl(me):
    upload_ftl(me, LORRY_HEADERS, lorry_rows(), load_type="part")
    page = me.get("/mail/ftl/preview").get_data(as_text=True)
    assert "2 PART-LOAD docket(s), 1 branch(es)" in page and "2 row(s) PART-LOAD ki nahi thi - ignore" in page
    m = api(me, "ftl", "mail/301", method="get").get_json()
    assert m["subject"] == "FTL PENDING BILL............. GR:-7153900/7147162 M/S- STEEL & METALS, AIM TECHNOLOGIES PVT LTD"
    assert "Kindly check the below FTL dockets and generate the bills" in m["text"]                    # wording stays FTL
    assert "7147163" not in m["text"] and "\tFTL" not in m["text"] and "\tPART-LOAD" in m["text"]       # the FTL docket is not in it
    assert "Kindly check the below FTL dockets" in m["html"] and "PART-LOAD dockets" not in m["html"]
    assert set(status(me, "ftl")["rows"]) == {"301"}                                                # PUNE only had an FTL docket


def test_load_type_both_keeps_ftl_and_part_load_under_the_same_ftl_wording(me):
    upload_ftl(me, LORRY_HEADERS, lorry_rows(), load_type="all")
    m = api(me, "ftl", "mail/301", method="get").get_json()
    assert m["subject"] == "FTL PENDING BILL............. GR:-7147163/7147162 M/S- STEEL & METALS, AIM TECHNOLOGIES PVT LTD"
    assert "Kindly check the below FTL dockets" in m["text"] and "PART-LOAD dockets" not in m["text"]
    assert all(c in m["text"] for c in ("7147163", "7153900", "7147162")) and "FTL" in m["text"] and "PART-LOAD" in m["text"]
    assert set(status(me, "ftl")["rows"]) == {"301", "302"}


def test_without_a_choice_the_upload_takes_both(me):
    me.post("/mail/ftl/upload", data={"csrf_token": tok(me), "report_file": (sheet(LORRY_HEADERS, lorry_rows()), "r.xlsx")},
            content_type="multipart/form-data")
    assert api(me, "ftl", "mail/301", method="get").get_json()["subject"].startswith("FTL PENDING BILL.............")


def test_the_load_type_choice_is_on_the_ftl_upload_page_only(me):
    page = me.get("/mail/ftl/").get_data(as_text=True)
    assert 'name="load_type"' in page and "Sirf PART-LOAD" in page and "Dono" in page
    assert 'name="load_type"' not in me.get("/mail/tbb/").get_data(as_text=True)


def test_an_empty_load_type_result_names_what_was_looked_for(me):
    upload_ftl(me, LORRY_HEADERS, [[301, "AHMEDABAD", 1, "25.10.2026", 1, "A", 1, 2, "FTL"]], load_type="part")
    assert "koi PART-LOAD docket nahi mila" in me.get("/mail/ftl/").get_data(as_text=True)
