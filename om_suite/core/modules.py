"""
Single registry of every tool in the suite. Dashboard cards, sidebar navigation,
per-user access control (admin panel) and the "coming soon" pages all read from here.

status:  live      -> working in this build
         soon      -> planned (phase says when)
         external  -> separate deployment, opened in a new tab (own login)
"""

GROUPS = ["Reports", "Communication", "Lookup & Automation", "Other apps", "Administration"]

PTP_MODES = [
    ("dashboard", "Overview"),
    ("monthwise", "Month-wise Report"),
    ("topaygen", "Generate ToPay Report"),
    ("paidgen", "Generate Paid Report"),
    ("combinedgen", "Paid & ToPay Report"),
    ("regionwise", "Region-wise Report"),
    ("automail", "Auto Mail (Region-wise)"),
    ("cnquery", "CN Query Tool"),
]

BILLING_MODES = [
    ("query", "1. ADR / NET Query"),
    ("branch", "2. Branch-wise Data"),
    ("region", "3. Region Creation"),
]

OM_MODES = [
    ("home", "Overview"),
    ("cn", "CN Query"),
    ("gst", "GST Query"),
    ("party", "Party Code Query"),
    ("master", "Master (CN → GST)"),
]

MODULES = [
    dict(id="paid_topay", name="Paid / ToPay Suite", icon="chart", group="Reports", status="live",
         endpoint="paid_topay.index",
         desc="Month-wise B.CODE report, ToPay/Paid report generators, region split, auto mail aur CN query.",
         children=PTP_MODES),
    dict(id="adr_net_suite", name="ADR / NET Billing Suite", icon="calendar", group="Reports", status="live", endpoint="billing.index",
         desc="LR export se ADR / Remaining LR, branch-wise sheets aur region workbook (month-wise SUMMARY) - audited, files browser me hi process hoti hain.",
         children=BILLING_MODES),
    dict(id="dbp_report", name="DBP Report Builder", icon="file", group="Reports", status="live", endpoint="dbp.report",
         desc="Excel exports upload karo, branch-wise DBP report (SUMMARY, PAID, TO PAY, PENDING BILLING, DDR...) banao."),
    dict(id="month_summary", name="Month-wise Summary", icon="calendar", group="Reports", status="live", endpoint="dbp.month",
         desc="ADR ya NET raw data se branch x month pending matrix, ya Party + Branch full region report."),
    dict(id="ai_chatbot", name="AI Chatbot", icon="bot", group="Reports", status="live", endpoint="dbp.chatbot_page",
         desc="Loaded report data se seedha sawaal-jawaab (numbers hamesha data se, AI se nahi)."),
    dict(id="validation", name="Validation & Audit", icon="check", group="Reports", status="live", endpoint="dbp.validation",
         desc="Duplicate/missing branch, DDR resolution aur reconciliation checks."),
    dict(id="mail_center", name="Mail Center", icon="mail", group="Communication", status="live", endpoint="mail.index",
         desc="Branch-wise verified pending mails, region auto-mail aur Thunderbird/SMTP sending."),
    dict(id="tbb_mail", name="TBB Party Mail", icon="mail", group="Communication", status="live", endpoint="flows.tbb_index",
         desc="Branch-wise GR verification mail: upload TBB report, review, select, send."),
    dict(id="bill_mail", name="Bill Generated Mail", icon="mail", group="Communication", status="live", endpoint="flows.bill_index",
         desc="Branch-wise Auto Generated Freight Bill mail."),
    dict(id="previous_mail", name="Previous Mail & Format", icon="mail", group="Communication", status="live",
         endpoint="flows.previous_index",
         desc="Purani mail ka format + naya data sheet: har branch/group ko uska mail, recipients purani mail se."),
    dict(id="branch_finder", name="Branch Detail Finder", icon="building", group="Lookup & Automation",
         status="live", endpoint="branch.index", desc="Branch code lookup (single + bulk Excel)."),
    dict(id="customer_search", name="Customer Search", icon="search", group="Lookup & Automation",
         status="live", endpoint="customer.index", desc="Customer search (manual + bulk Excel)."),
    dict(id="om_automation", name="OM Automation", icon="truck", group="Lookup & Automation",
         status="live", endpoint="om.index", desc="CN, GST (captcha), Party Code aur Master (CN → Party → GSTIN) lookups - manual ya Excel batch.",
         children=OM_MODES),
    dict(id="gr_auto", name="GR Auto Modification", icon="link", group="Other apps", status="external",
         desc="Separate app with its own login - opens in a new tab."),
    dict(id="directory", name="Directory & Masters", icon="book", group="Administration", status="live",
         endpoint="dbp.directory", admin_only=True, desc="Directory aur branch master update/search (sirf admin)."),
    dict(id="admin_users", name="Users & Access", icon="users", group="Administration", status="live",
         endpoint="admin.users", admin_only=True,
         desc="Signup approve karo, block/unblock, password reset aur har user ko tool access do."),
]

BY_ID = {m["id"]: m for m in MODULES}
ASSIGNABLE = [m for m in MODULES if not m.get("admin_only") and m["status"] != "external"]


def can_access(user, module_id: str) -> bool:
    m = BY_ID.get(module_id)
    if not user or not m:
        return False
    if user.get("role") == "admin":
        return True
    if m.get("admin_only"):
        return False
    if m["status"] == "external":
        return True
    mods = user.get("modules") or []
    return "*" in mods or module_id in mods


def grouped_for(user):
    """[(group, [modules...]), ...] containing only what this user may see."""
    out = []
    for g in GROUPS:
        items = [m for m in MODULES if m["group"] == g and can_access(user, m["id"])]
        if items:
            out.append((g, items))
    return out
