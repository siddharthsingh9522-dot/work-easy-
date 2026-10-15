# -*- coding: utf-8 -*-
"""Deterministic branch pending-mail generator.

No recipient is invented.  Contacts are selected only when a valid email
exists in the supplied directory.  The app shows missing roles instead of
guessing.

Mail layout: a summary table for the branch (same columns as the report's
SUMMARY row) + the branch's detailed data sheet as an .xlsx attachment.
No long docket-number list is written into the mail body.
"""
import io
import re
import html as _html
import pandas as pd
from .directory_loader import find_contacts


# ---------------------------------------------------------------- contacts
def _role_score(designation: str, role: str) -> int:
    # Spaces/hyphens/dots ignore karte hain: 'BRANCH IN-CHARGE',
    # 'BRANCH INCHARGE', 'Branch In - Charge' sab same maane jayein.
    dn = re.sub(r"[^A-Z]", "", str(designation or "").upper())
    if role == "branch":
        if "BRANCHINCHARGE" in dn: return 100
        if "BRANCHMANAGER" in dn: return 95
        if "BRANCHHEAD" in dn: return 90
    if role == "manager":
        if "OPERATIONMANAGER" in dn or "OPSMANAGER" in dn: return 100
        # Branch-level titles 'branch' slot ke hain -- unhe manager slot mein
        # dobara mat gino (warna 'manager missing' galat chhup jaata tha).
        if any(t in dn for t in ("BRANCHINCHARGE", "BRANCHMANAGER", "BRANCHHEAD")): return 0
        # AREAMANAGER pehle check hota hai; pehle 'MANAGER' isse dhak deta tha
        # aur 65 wala score kabhi lagta hi nahi tha.
        if "AREAMANAGER" in dn: return 65
        if "MANAGER" in dn: return 75
    if role == "billing":
        if "BILLING" in dn: return 100
        if "BILL" in dn: return 90
        if "ACCOUNT" in dn: return 80
        if "FINANCE" in dn: return 70
    return 0


def _valid_email(v) -> bool:
    s = "" if v is None else str(v).strip()
    return bool(s) and s.upper() not in ("NAN", "NONE") and "@" in s


def select_mail_contacts(directory_df: pd.DataFrame, branch_code):
    all_missing = {"to": [], "cc": [],
                   "missing": ["branch", "manager", "billing"], "chosen": {}}
    contacts = find_contacts(directory_df, branch_code)
    if contacts is None or contacts.empty:
        return all_missing
    if "EMAIL" not in contacts.columns or "DESIGNATION" not in contacts.columns:
        return all_missing

    # Bina valid email wale contact ko kabhi select nahi karte (pehle NaN email
    # select ho jaati thi aur "; ".join(...) TypeError deta tha).
    contacts = contacts[contacts["EMAIL"].apply(_valid_email)].copy()
    if contacts.empty:
        return all_missing
    contacts["EMAIL"] = contacts["EMAIL"].astype(str).str.strip()

    chosen = {}
    for role in ("branch", "manager", "billing"):
        scored = contacts.copy()
        scored["_score"] = scored["DESIGNATION"].apply(lambda x: _role_score(x, role))
        scored = scored[scored["_score"] > 0].sort_values("_score", ascending=False, kind="stable")
        if not scored.empty:
            chosen[role] = scored.iloc[0].to_dict()

    to, cc = [], []
    if "branch" in chosen:
        to.append(chosen["branch"]["EMAIL"])
    if "manager" in chosen and chosen["manager"]["EMAIL"] not in to:
        cc.append(chosen["manager"]["EMAIL"])
    if "billing" in chosen and chosen["billing"]["EMAIL"] not in to + cc:
        cc.append(chosen["billing"]["EMAIL"])
    missing = [r for r in ("branch", "manager", "billing") if r not in chosen]
    return {"to": to, "cc": cc, "missing": missing, "chosen": chosen}


# ------------------------------------------------------------- branch data
PENDING_CATEGORIES = ["PENDING_BILLING", "POD_NOT_BILLED", "DDR_PENDING"]

CATEGORY_LABEL = {
    "PENDING_BILLING": "Pending Billing (ADR)",
    "POD_NOT_BILLED": "POD on NET but not billed",
    "DDR_PENDING": "DDR Pending",
}


def _detail_parts(categorized_data, branch_code, category=None):
    """Branch ki CN-level rows, category-wise: ({cat: DataFrame}, skipped_cats).
    Har category apni original columns ke saath alag rehti hai (concat nahi),
    taaki data sheet mein doosri category ke khaali columns na aayein.
    Jo category skip hui (branch column nahi mila) woh skipped mein aati hai --
    chup-chaap gayab nahi hoti."""
    from .ingest import resolve_ddr_branch, detect_branch_column
    code = int(branch_code)
    cats = [category] if category else PENDING_CATEGORIES
    parts, skipped = {}, []
    for cat in cats:
        df = categorized_data.get(cat)
        if df is None or df.empty:
            continue
        if cat == "DDR_PENDING":
            # Section-based DDR mein section marker hi authoritative branch hai.
            if "_SECTION_BRANCH" in df.columns:
                mask = pd.to_numeric(df["_SECTION_BRANCH"], errors="coerce") == code
            else:
                cols = {str(c).upper(): c for c in df.columns}
                from_col = cols.get("DLY_CHALLAN_FROM")
                challan_col = cols.get("DLY_CHALLAN")
                if from_col is None:
                    skipped.append(cat)
                    continue
                flags = []
                for _, r in df.iterrows():
                    b, _status = resolve_ddr_branch(
                        r.get(from_col), r.get(challan_col) if challan_col else None)
                    flags.append(b == code)
                mask = pd.Series(flags, index=df.index)
        elif "_SECTION_BRANCH" in df.columns:
            mask = pd.to_numeric(df["_SECTION_BRANCH"], errors="coerce") == code
        else:
            col, kind = detect_branch_column(df, cat)
            if col is None or kind != "code":
                skipped.append(cat)
                continue
            mask = pd.to_numeric(df[col], errors="coerce") == code
        part = df.loc[mask].copy()
        if not part.empty:
            parts[cat] = part
    return parts, skipped


def _concat_parts(parts: dict) -> pd.DataFrame:
    frames = [p.assign(_CATEGORY=c) for c, p in parts.items()]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _detail_rows(categorized_data, branch_code, category=None):
    """Purana interface: saari categories ki rows ek DataFrame mein
    ('_CATEGORY' column ke saath). Skipped list result.attrs['skipped'] mein."""
    parts, skipped = _detail_parts(categorized_data, branch_code, category)
    out = _concat_parts(parts)
    out.attrs["skipped"] = skipped
    return out


def build_data_sheet(parts: dict):
    """{category: df} -> (.xlsx bytes, sheet names). Har category ki apni
    sheet, apni original columns ke saath. Internal '_' columns
    (_SECTION_BRANCH, _BLOCK_TITLE, _RESOLVED_ADR_BRANCH ...) file mein nahi
    jaate. Rows nahi hain to (None, [])."""
    parts = {c: p for c, p in (parts or {}).items() if p is not None and not p.empty}
    if not parts:
        return None, []
    buf = io.BytesIO()
    sheets = []
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for cat, part in parts.items():
            keep = [c for c in part.columns if not str(c).startswith("_")]
            part[keep].to_excel(xw, sheet_name=str(cat)[:31], index=False)
            sheets.append(str(cat))
    return buf.getvalue(), sheets


# ----------------------------------------------------------- summary table
_SUMMARY_COLUMNS = [
    ("B.NAME", ("BNAME", "BRANCHNAME")),
    ("B.CODE", ("BCODE", "BRANCHCODE")),
    ("PAID", ("PAID",)),
    ("TO PAY", ("TOPAY",)),
    ("PENDING BILLING", ("PENDINGBILLING", "ADRMADEBUTNOTBILLED", "ADR")),
    ("NET BUT NOT BILLED", ("NETBUTNOTBILLED", "PODONNETBUTNOTBILLED",
                            "PODNOTBILLED", "NET")),
    ("DDR PENDING", ("DDRPENDING", "DDR")),
    ("TOTAL", ("TOTAL",)),
]


def _norm(name) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(name).upper())


def _fmt(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if s.lower() == "nan":
        return ""
    try:
        f = float(s)
        if f == int(f):
            return str(int(f))
    except (TypeError, ValueError, OverflowError):
        pass
    return s


def _summary_pairs(summary_df, code, name, categorized_data):
    """[(label, value), ...] -- branch ki SUMMARY row jaisi. Return
    (pairs, warning|None). Branch SUMMARY mein na mile to 3 pending
    categories ki ginti detail se nikaalte hain (guess nahi)."""
    sdf = summary_df.copy() if summary_df is not None else pd.DataFrame()
    srow = pd.DataFrame()
    if "B.CODE" in sdf.columns:
        sdf["_CODE_"] = pd.to_numeric(sdf["B.CODE"], errors="coerce")
        srow = sdf[sdf["_CODE_"] == code]

    if not srow.empty:
        row = srow.iloc[0]
        colmap = {_norm(c): c for c in sdf.columns}
        pairs = []
        for label, aliases in _SUMMARY_COLUMNS:
            src = next((colmap[a] for a in aliases if a in colmap), None)
            if src is None:
                continue
            val = str(code) if label == "B.CODE" else _fmt(row[src])
            pairs.append((label, val))
        return pairs, None

    allparts, _skipped = _detail_parts(categorized_data, code, None)
    pairs = [("B.NAME", name), ("B.CODE", str(code))]
    for cat, label in (("PENDING_BILLING", "PENDING BILLING"),
                       ("POD_NOT_BILLED", "NET BUT NOT BILLED"),
                       ("DDR_PENDING", "DDR PENDING")):
        pairs.append((label, str(len(allparts[cat])) if cat in allparts else "0"))
    warn = ("Branch SUMMARY mein nahi mila -- PAID/TO PAY nahi dikhaye, "
            "baaki counts detail data se liye gaye.")
    return pairs, warn


def _text_table(pairs) -> list:
    w = max(len(k) for k, _ in pairs)
    return [f"  {k.ljust(w)} : {v}" for k, v in pairs]


def _html_table(pairs) -> str:
    th = "".join(
        f'<th style="border:1px solid #808080;padding:4px 8px;background:#d9e1f2;'
        f'text-align:center;white-space:nowrap">{_html.escape(k)}</th>' for k, _ in pairs)
    td = "".join(
        f'<td style="border:1px solid #808080;padding:4px 8px;text-align:center">'
        f'{_html.escape(v)}</td>' for _, v in pairs)
    return ('<table style="border-collapse:collapse;font-family:Calibri,Arial,sans-serif;'
            f'font-size:11pt"><tr>{th}</tr><tr>{td}</tr></table>')


# -------------------------------------------------------------------- mail
def build_mail(categorized_data, summary_df, directory_df, branch_code, category=None,
               region_name="Region", max_dockets=100, with_attachment=True,
               contacts_override=None):
    """max_dockets purane signature ke saath compatible rakha hai, ab use nahi
    hota (docket list body mein nahi jaati; poora data sheet attach hoti hai)."""
    code = int(float(str(branch_code).strip()))
    if contacts_override is not None:
        # Region-wise auto-mail: recipients mail_recipients.select_recipients se aate hain.
        contacts = {"to": list(contacts_override["to"]), "cc": list(contacts_override["cc"]),
                    "missing": list(contacts_override.get("missing", []))}
    else:
        contacts = select_mail_contacts(directory_df, code)

    name = f"Branch {code}"
    if summary_df is not None and "B.CODE" in summary_df.columns and "B.NAME" in summary_df.columns:
        sd = summary_df.copy()
        sd["_CODE_"] = pd.to_numeric(sd["B.CODE"], errors="coerce")
        hit = sd[sd["_CODE_"] == code]
        if not hit.empty and _fmt(hit.iloc[0]["B.NAME"]):
            name = _fmt(hit.iloc[0]["B.NAME"])

    parts, skipped = _detail_parts(categorized_data, code, category)
    detail = _concat_parts(parts)
    warnings = [f"{c}: branch column nahi mila, is category ki rows attach nahi hui"
                for c in skipped]

    cn_col = None
    docket_count = 0
    if not detail.empty:
        cn_col = next((c for c in detail.columns
                       if str(c).strip().upper() in ("CN_NO", "CN NO", "CN_NO.")), None)
        if cn_col:
            docket_count = int(detail[cn_col].dropna().astype(str).str.strip()
                               .replace("", pd.NA).dropna().nunique())
        else:
            docket_count = len(detail)

    pairs, warn = _summary_pairs(summary_df, code, name, categorized_data)
    if warn:
        warnings.append(warn)

    subject_cat = CATEGORY_LABEL.get(category, "Pending Matters")
    subject = f"{region_name} - {name} ({code}) - {subject_cat} - Action Required"

    xlsx_bytes, sheets = build_data_sheet(parts)
    if not with_attachment:
        xlsx_bytes, sheets = None, []
    attach_name = None
    if xlsx_bytes:
        safe = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_") or "branch"
        attach_name = f"{code}_{safe}_pending_data.xlsx"

    intro = f"Please find below the pending matters summary for {name} (Branch Code: {code})."
    if xlsx_bytes:
        data_line = ("The detailed data sheet is attached "
                     f"({', '.join(CATEGORY_LABEL.get(s, s) for s in sheets)}).")
    else:
        data_line = ("No detailed rows were found in the loaded data for this "
                     "branch/category, so no data sheet is attached.")
    closing = [
        "Kindly review these pending matters and arrange necessary action at the earliest.",
        "Please update the status once the pending items are resolved.",
    ]

    text_lines = ["Dear Team,", "", intro, ""] + _text_table(pairs) + ["", data_line, ""]
    text_lines += closing + ["", "Regards,", "DBP Team"]

    esc = _html.escape
    html_body = (
        '<div style="font-family:Calibri,Arial,sans-serif;font-size:11pt">'
        f"<p>Dear Team,</p><p>{esc(intro)}</p>{_html_table(pairs)}"
        f"<p>{esc(data_line)}</p><p>{'<br>'.join(esc(x) for x in closing)}</p>"
        "<p>Regards,<br>DBP Team</p></div>"
    )

    return {
        "to": "; ".join(contacts["to"]),
        "cc": "; ".join(contacts["cc"]),
        "missing_roles": contacts["missing"],
        "subject": subject,
        "body": "\n".join(text_lines),
        "html_body": html_body,
        "summary": pairs,
        "attachment_name": attach_name,
        "attachment_bytes": xlsx_bytes,
        "attachment": xlsx_bytes,   # app.py Mail Center isi key se download deta hai
        "check": [],                # app.py ye key padhta hai (count-mismatch warnings)
        "to_list": list(contacts["to"]),
        "cc_list": list(contacts["cc"]),
        "warnings": warnings,
        "docket_count": docket_count,
        "branch_name": name,
        "branch_code": code,
    }


def as_eml(mail):
    """Simple RFC-822 .eml draft (Outlook/Thunderbird): plain + HTML body
    (summary table) aur data sheet attachment."""
    import email.message
    msg = email.message.EmailMessage()
    if mail.get("to"): msg["To"] = mail["to"]
    if mail.get("cc"): msg["Cc"] = mail["cc"]
    msg["Subject"] = mail["subject"]
    msg["X-Generated-By"] = "OM Report AI"
    msg.set_content(mail["body"])
    if mail.get("html_body"):
        msg.add_alternative(mail["html_body"], subtype="html")
    if mail.get("attachment_bytes"):
        msg.add_attachment(
            mail["attachment_bytes"],
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=mail.get("attachment_name") or "pending_data.xlsx",
        )
    return msg.as_bytes()
