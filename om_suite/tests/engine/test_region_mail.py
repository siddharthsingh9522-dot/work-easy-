import sys, os, threading, time
import pandas as pd
from dbp import mail_recipients as mr, mail_sender as ms
from dbp.directory_loader import load_directory

DIR = os.path.join(os.path.dirname(__file__), "..", "..", "dbp", "masters_seed", "directory.xlsx")


def _dir():
    return pd.DataFrame([
        # branch 1: full set + area manager + sales manager
        dict(BRANCH_CODE=1, NAME="Inch", DESIGNATION="BRANCH  IN-CHARGE", EMAIL="inch@x.com"),
        dict(BRANCH_CODE=1, NAME="Mgr", DESIGNATION="BRANCH MANAGER", EMAIL="mgr@x.com"),
        dict(BRANCH_CODE=1, NAME="Sales", DESIGNATION="MANAGER- SALES & MARKETING", EMAIL="sales@x.com"),
        dict(BRANCH_CODE=1, NAME="Area", DESIGNATION="AREA MANAGER", EMAIL="area@x.com"),
        dict(BRANCH_CODE=1, NAME="Bill", DESIGNATION="BILLING", EMAIL="bill@x.com/bill2@x.com\n"),
        dict(BRANCH_CODE=1, NAME="Dbp", DESIGNATION="DBP", EMAIL="dbp@x.com"),
        # branch 2: only incharge + random staff (fallback)
        dict(BRANCH_CODE=2, NAME="Inch2", DESIGNATION="BRANCH INCHARGE", EMAIL="inch2@x.com"),
        dict(BRANCH_CODE=2, NAME="Exe", DESIGNATION="EXECUTIVE", EMAIL="exe@x.com"),
        # branch 3: no incharge
        dict(BRANCH_CODE=3, NAME="Cash", DESIGNATION="CASHIER", EMAIL="cash@x.com"),
    ])


def test_rules():
    r = mr.select_recipients(_dir(), 1, ["regdbp@x.com"])
    allm = r["to"] + r["cc"]
    assert r["to"] == ["inch@x.com"]
    assert "area@x.com" not in allm and "sales@x.com" not in allm
    assert {"mgr@x.com", "bill@x.com", "bill2@x.com", "dbp@x.com", "regdbp@x.com"} <= set(r["cc"])
    assert r["excluded_area_managers"] == ["Area"]
    r2 = mr.select_recipients(_dir(), 2, ["regdbp@x.com"])
    assert r2["fallback_used"] and "exe@x.com" in r2["cc"]
    r3 = mr.select_recipients(_dir(), 3, ["regdbp@x.com"])
    assert r3["blocked_reason"] and "Incharge" in r3["blocked_reason"]
    r4 = mr.select_recipients(_dir(), 1, [])
    assert "Region DBP" in r4["blocked_reason"]


def test_plan_and_smtp():
    from aiosmtpd.controller import Controller
    got = []

    class H:
        async def handle_DATA(self, server, session, envelope):
            got.append(envelope); return "250 OK"
    c = Controller(H(), hostname="127.0.0.1", port=8026); c.start()
    try:
        summ = pd.DataFrame({"B.NAME": ["A", "B", "C"], "B.CODE": [1, 2, 3], "PAID": [0, 0, 0],
                             "TO PAY": [0, 0, 0], "PENDING BILLING": [1, 1, 1],
                             "NET BUT NOT BILLED": [0, 0, 0], "DDR PENDING": [0, 0, 0], "TOTAL": [1, 1, 1]})
        cat = {"PENDING_BILLING": pd.DataFrame({"CN_NO": ["a", "b", "c"], "ADR BRANCH": [1, 2, 3]})}
        plan = mr.plan_region_mails(cat, summ, _dir(), "TEST REGION", ["regdbp@x.com"])
        st = {p["branch_code"]: p["status"] for p in plan}
        assert st == {1: "READY", 2: "READY", 3: "BLOCKED"}, st
        cfg = ms.SmtpConfig(host="127.0.0.1", port=8026, from_addr="dbp@om.com", security="none", delay_seconds=0)
        assert ms.test_connection(cfg)[0]
        res = ms.send_batch(cfg, [p["mail"] for p in plan if p["status"] == "READY"])
        assert [r["status"] for r in res] == ["SENT", "SENT"], res
        e = got[0]
        assert set(e.rcpt_tos) == {"inch@x.com", "mgr@x.com", "bill@x.com", "bill2@x.com", "dbp@x.com", "regdbp@x.com"}, e.rcpt_tos
        assert b"pending_data.xlsx" in e.content
    finally:
        c.stop()


def test_branch_manager_fills_incharge_slot():
    d = pd.DataFrame([
        dict(BRANCH_CODE=9, NAME="Head", DESIGNATION="BRANCH/HUB MANAGER", EMAIL="head@x.com"),
        dict(BRANCH_CODE=9, NAME="Sales", DESIGNATION="MANAGER SALES & MKT", EMAIL="s@x.com"),
        dict(BRANCH_CODE=9, NAME="Ops", DESIGNATION="OPERATION MANAGER", EMAIL="ops@x.com"),
    ])
    r = mr.select_recipients(d, 9, ["r@x.com"])
    assert r["incharge_from_manager"] and r["to"] == ["head@x.com"]
    assert "s@x.com" not in r["to"] + r["cc"]
    assert r["blocked_reason"] is None


def test_full_report_recipients():
    d = pd.DataFrame([
        dict(BRANCH_CODE=1, NAME="A", DESIGNATION="BRANCH INCHARGE", EMAIL="a@x.com"),
        dict(BRANCH_CODE=1, NAME="B", DESIGNATION="EXECUTIVE", EMAIL="b@x.com/b2@x.com"),
        dict(BRANCH_CODE=1, NAME="AM", DESIGNATION="AREA MANAGER", EMAIL="am@x.com"),
        dict(BRANCH_CODE=1, NAME="PA", DESIGNATION="PA OF AREA MANAGER", EMAIL="pa@x.com"),
        dict(BRANCH_CODE=1, NAME="PA2", DESIGNATION="PA- AREA MANAGER", EMAIL="pa2@x.com"),
        dict(BRANCH_CODE=2, NAME="C", DESIGNATION="CASHIER", EMAIL="c@x.com"),
        dict(BRANCH_CODE=2, NAME="AM2", DESIGNATION="AREA  MANAGER(OPERATION-RETAIL)", EMAIL="am2@x.com"),
    ])
    summ = pd.DataFrame({"B.NAME": ["A", "B"], "B.CODE": [1, 2], "TOTAL": [3, 4]})
    r = mr.plan_full_report_mails(summ, d, b"xlsx", "R.xlsx", "REG", extra_cc=["dbp@x.com"], max_recipients=400)
    m = r["mails"][0]
    assert set(m["to_list"]) == {"a@x.com", "b@x.com", "b2@x.com", "pa@x.com", "pa2@x.com", "c@x.com"}, m["to_list"]
    assert set(m["cc_list"]) == {"am@x.com", "am2@x.com", "dbp@x.com"}, m["cc_list"]
    # chunking: 5 staff, cap 3 recipients incl. 3 cc -> room=1 -> 5 mails, each with area managers CC
    r2 = mr.plan_full_report_mails(summ, d, b"x", "R.xlsx", "REG", max_recipients=4)
    assert len(r2["mails"]) == 3 and all("am@x.com" in x["cc_list"] for x in r2["mails"])
    r3 = mr.plan_full_report_mails(summ, d, b"x", "R.xlsx", "REG", bcc_staff=True)
    assert r3["mails"][0]["bcc_list"] and "a@x.com" not in r3["mails"][0]["to_list"]
