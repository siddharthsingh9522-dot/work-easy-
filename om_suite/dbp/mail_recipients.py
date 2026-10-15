# -*- coding: utf-8 -*-
"""Region-wise mail recipient selection.

Rules (confirmed by user):
  * Har branch ke liye: Branch Incharge (COMPULSORY), Branch Manager,
    Billing person, aur branch ka DBP / ToPay-Retail-ToPay person.
  * Region ka DBP person COMPULSORY hai (UI mein chuna/likha jaata hai,
    alag se, branch-level DBP ke upar).
  * AREA MANAGER kisi bhi haal mein recipient nahi banta.
  * "MANAGER - SALES & MARKETING" Branch Manager nahi maana jaata.
  * Agar branch mein Manager AUR Billing dono na milein, to us branch ke
    jo bhi (valid email wale, non-area-manager) log hain unhe include karte
    hain -- branch skip nahi hota.

Koi email invent nahi hoti; sirf directory ki (ya user ke diye) emails.
"""
import re
import pandas as pd
from .directory_loader import branch_lookup

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-']+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Manager slot se bahar rakhne wale tokens (letters-only designation mein).
NOT_BRANCH_MANAGER_TOKENS = (
    "SALES", "MARKETING", "MKT", "RETAIL", "AREA", "ASST", "ASSISTANT",
    "ZONAL", "REGIONAL", "ACCOUNT", "FINANCE",
)
MAX_PER_ROLE = 3          # ek role ke max itne log (same top-score wale)
FALLBACK_MAX_PEOPLE = 3   # manager+billing dono na milne par extra log


def parse_emails(value) -> list:
    """Ek cell mein 'a@x.com/b@y.com\\n' jaisi multiple emails ho sakti hain."""
    if value is None:
        return []
    s = str(value)
    if s.strip().upper() in ("", "NAN", "NONE"):
        return []
    return [m.lower() for m in EMAIL_RE.findall(s)]


def _letters(designation) -> str:
    return re.sub(r"[^A-Z]", "", str(designation or "").upper())


def is_area_manager(designation) -> bool:
    up = re.sub(r"\s+", " ", str(designation or "").upper())
    dn = _letters(designation)
    # 'PA OF AREA MANAGER' / 'P.A. TO AREA MANAGER' area manager khud nahi hai.
    if re.match(r"\s*P\.?\s?A\.?(\s|-|/|$)", up):     # 'PA OF ...', 'PA- AREA MANAGER', 'P.A. TO ...'
        return False
    if "AREAMANAGER" in dn:
        return True
    return bool(re.search(r"\bAREA\b", up)) and "MANAGER" in dn


def role_scores(designation) -> dict:
    """{role: score} -- sirf jo role match ho. Area manager -> khaali dict."""
    dn = _letters(designation)
    if not dn or is_area_manager(designation):
        return {}
    out = {}
    if "BRANCHINCHARGE" in dn:
        out["incharge"] = 100
    if re.search(r"BRANCH(HUB)?MANAGER", dn):   # 'BRANCH MANAGER', 'BRANCH/HUB MANAGER', 'SR.BRANCH MANAGER'
        out["manager"] = 100
    elif "MANAGER" in dn and not any(t in dn for t in NOT_BRANCH_MANAGER_TOKENS):
        out["manager"] = 80
    if "BILLING" in dn:
        out["billing"] = 100
    if "DBP" in dn:
        out["dbp"] = 100
    elif any(t in dn for t in ("TOPAY", "PAYTOPAID", "PAIDTOPAY")):
        out["dbp"] = 60
    return out


def _people(directory_df, branch_code) -> list:
    """Branch ke saare log, har email alag, area managers hata kar."""
    df = branch_lookup(directory_df, branch_code)
    people, seen = [], set()
    if df is None or df.empty:
        return people, {"excluded_area_managers": []}
    excluded_area = []
    for _, r in df.iterrows():
        desig = r.get("DESIGNATION", "")
        emails = parse_emails(r.get("EMAIL", ""))
        if not emails:
            continue
        if is_area_manager(desig):
            excluded_area.append(str(r.get("NAME", "")).strip())
            continue
        for em in emails:
            if em in seen:
                continue
            seen.add(em)
            people.append({
                "email": em,
                "name": str(r.get("NAME", "") or "").strip(),
                "designation": str(desig or "").strip(),
                "scores": role_scores(desig),
            })
    people_meta = {"excluded_area_managers": [x for x in excluded_area if x]}
    return people, people_meta


def _top(people, role):
    cand = [p for p in people if role in p["scores"]]
    if not cand:
        return []
    best = max(p["scores"][role] for p in cand)
    return [p for p in cand if p["scores"][role] == best][:MAX_PER_ROLE]


def select_recipients(directory_df, branch_code, region_dbp_emails=()) -> dict:
    """Return dict:
      to, cc          -> email lists (dedup, order preserved)
      chosen          -> {role: [person dicts]}
      missing         -> compulsory/normal roles jo directory mein nahi mile
      fallback_used   -> bool
      blocked_reason  -> None ya str (compulsory role missing)
      excluded_area_managers -> naam list (audit ke liye)
    """
    if directory_df is None or directory_df.empty:
        people, meta = [], {"excluded_area_managers": []}
    else:
        people, meta = _people(directory_df, branch_code)

    chosen = {r: _top(people, r) for r in ("incharge", "manager", "billing", "dbp")}

    # Real directory mein bahut se branches ka head 'BRANCH MANAGER' / 'BRANCH/HUB
    # MANAGER' likha hota hai, 'BRANCH INCHARGE' nahi. Aise branch mein branch-level
    # manager hi compulsory "incharge" slot bharta hai (To mein jaata hai).
    incharge_from_manager = False
    if not chosen["incharge"]:
        heads = [p for p in people if p["scores"].get("manager") == 100]
        if heads:
            chosen["incharge"] = heads[:MAX_PER_ROLE]
            incharge_from_manager = True

    fallback_used = False
    if not chosen["manager"] and not chosen["billing"]:
        already = {p["email"] for v in chosen.values() for p in v}
        extras = [p for p in people if p["email"] not in already]
        # Incharge-type designation pehle, baaki directory order mein.
        extras.sort(key=lambda p: 0 if "INCHARGE" in _letters(p["designation"]) else 1)
        extras = extras[:FALLBACK_MAX_PEOPLE]
        if extras:
            chosen["fallback"] = extras
            fallback_used = True

    region_dbp = []
    for e in region_dbp_emails or ():
        region_dbp += parse_emails(e)
    region_dbp = list(dict.fromkeys(region_dbp))

    to, cc = [], []

    def add(lst, email):
        if email not in to and email not in cc:
            lst.append(email)

    for p in chosen["incharge"]:
        add(to, p["email"])
    for role in ("manager", "billing", "dbp", "fallback"):
        for p in chosen.get(role, []):
            add(cc, p["email"])
    for e in region_dbp:
        add(cc, e)
    # Agar incharge nahi mila to To khaali na rahe (SMTP ko kam se kam ek To chahiye)
    if not to and cc:
        to.append(cc.pop(0))

    missing = [r for r in ("incharge", "manager", "billing", "dbp") if not chosen[r]]
    blocked = None
    if not chosen["incharge"]:
        blocked = "Branch Incharge ki email directory mein nahi mili (compulsory)"
    if not region_dbp:
        blocked = (blocked + "; " if blocked else "") + "Region DBP person select nahi kiya (compulsory)"

    return {
        "to": to, "cc": cc, "chosen": chosen, "missing": missing,
        "fallback_used": fallback_used, "incharge_from_manager": incharge_from_manager,
        "blocked_reason": blocked,
        "region_dbp": region_dbp,
        "excluded_area_managers": meta["excluded_area_managers"],
    }


def region_dbp_candidates(directory_df, branch_codes) -> pd.DataFrame:
    """Region ke branches mein jo DBP-designation wale log hain (UI multiselect
    ke liye). Region ka DBP ek hi hoga ya kai, yeh user chunta hai."""
    rows = []
    for code in branch_codes:
        people, _ = _people(directory_df, code)
        for p in people:
            if "dbp" in p["scores"]:
                rows.append({"BRANCH_CODE": int(code), "NAME": p["name"],
                             "DESIGNATION": p["designation"], "EMAIL": p["email"]})
    df = pd.DataFrame(rows, columns=["BRANCH_CODE", "NAME", "DESIGNATION", "EMAIL"])
    return df.drop_duplicates(subset=["EMAIL"]).reset_index(drop=True)


def plan_region_mails(categorized_data, summary_df, directory_df, region_name,
                      region_dbp_emails, category=None,
                      skip_no_rows=True, allow_missing_incharge=False) -> list:
    """Region ke SAARE branches (SUMMARY ke B.CODE) ke liye mail plan.

    Har item: {branch_code, branch_name, status, reason, recips, mail}
      status: READY | BLOCKED | SKIPPED
    Jo preview mein dikhta hai wahi bhejta hai -- plan ek baar banta hai,
    send usi plan se hota hai.
    """
    from . import mail_generator
    codes = sorted(set(pd.to_numeric(summary_df["B.CODE"], errors="coerce")
                       .dropna().astype(int).tolist()))
    plan = []
    for code in codes:
        recips = select_recipients(directory_df, code, region_dbp_emails)
        mail = mail_generator.build_mail(
            categorized_data, summary_df, directory_df, code, category,
            region_name, with_attachment=True, contacts_override=recips)
        item = {"branch_code": code, "branch_name": mail["branch_name"],
                "recips": recips, "mail": mail, "status": "READY", "reason": ""}
        if skip_no_rows and mail["docket_count"] == 0:
            item.update(status="SKIPPED", reason="Is branch ke pending rows nahi hain")
        elif not recips["region_dbp"]:
            item.update(status="BLOCKED", reason="Region DBP person select nahi kiya (compulsory)")
        elif not recips["chosen"]["incharge"] and not allow_missing_incharge:
            item.update(status="BLOCKED", reason="Branch Incharge ki email directory mein nahi mili (compulsory)")
        elif not mail["to_list"]:
            item.update(status="BLOCKED", reason="Koi valid recipient nahi mila")
        plan.append(item)
    return plan


# ------------------------------------------------------------------ full DBP report mail
def branch_all_people(directory_df, branch_code) -> dict:
    """Branch ke SAARE log (valid email wale). Area managers alag (CC ke liye)."""
    df = branch_lookup(directory_df, branch_code)
    staff, area, seen = [], [], set()
    if df is None or df.empty:
        return {"staff": staff, "area": area}
    for _, r in df.iterrows():
        desig = r.get("DESIGNATION", "")
        for em in parse_emails(r.get("EMAIL", "")):
            if em in seen:
                continue
            seen.add(em)
            rec = {"email": em, "name": str(r.get("NAME", "") or "").strip(),
                   "designation": str(desig or "").strip()}
            (area if is_area_manager(desig) else staff).append(rec)
    return {"staff": staff, "area": area}


def plan_full_report_mails(summary_df, directory_df, report_bytes, report_name, region_name,
                           extra_cc=(), max_recipients=400, bcc_staff=False) -> dict:
    """Poori DBP report ek saath: report mein jitni branches hain un sabke saare log
    (To) + unke Area Managers (CC). Recipients limit (mail server ke per-mail limit)
    se zyada hon to mails chunks mein bantti hain; har chunk mein Area Managers CC rehte hain.

    Returns {mails:[mail dict], branches:[per-branch info], staff:int, area:int}
    """
    import html as _html
    from datetime import datetime
    codes = sorted(set(pd.to_numeric(summary_df["B.CODE"], errors="coerce").dropna().astype(int).tolist()))
    staff, area, per_branch = [], [], []
    seen = set()
    names = {}
    if "B.NAME" in summary_df.columns:
        for _, r in summary_df.iterrows():
            c = pd.to_numeric(r.get("B.CODE"), errors="coerce")
            if pd.notna(c):
                names[int(c)] = str(r.get("B.NAME") or "")
    for code in codes:
        ppl = branch_all_people(directory_df, code)
        n_s = n_a = 0
        for p in ppl["staff"]:
            if p["email"] not in seen:
                seen.add(p["email"]); staff.append(p["email"]); n_s += 1
        for p in ppl["area"]:
            if p["email"] not in seen:
                seen.add(p["email"]); area.append(p["email"]); n_a += 1
        per_branch.append({"B.CODE": code, "Branch": names.get(code, ""),
                           "People (To)": len(ppl["staff"]), "Area Managers (CC)": len(ppl["area"]),
                           "Note": "" if (ppl["staff"] or ppl["area"]) else "directory mein koi email nahi"})
    cc_extra = [e for x in extra_cc for e in parse_emails(x) if e not in seen]
    cc_all = list(dict.fromkeys(area + cc_extra))

    # summary table (poori region)
    cols = [c for c in summary_df.columns if not str(c).startswith("_")]
    def _cell(v):
        s = "" if v is None else str(v)
        if s.lower() == "nan": return ""
        try:
            f = float(s)
            if f == int(f): return str(int(f))
        except (ValueError, OverflowError):
            pass
        return s
    th = "".join('<th style="border:1px solid #808080;padding:3px 7px;background:#d9e1f2">%s</th>' % _html.escape(str(c)) for c in cols)
    trs = "".join("<tr>" + "".join('<td style="border:1px solid #808080;padding:3px 7px;text-align:center">%s</td>' % _html.escape(_cell(v)) for v in row) + "</tr>"
                  for row in summary_df[cols].itertuples(index=False))
    table = ('<table style="border-collapse:collapse;font-family:Calibri,Arial,sans-serif;font-size:10pt">'
             f"<tr>{th}</tr>{trs}</table>")
    today = datetime.now().strftime("%d-%b-%Y")
    subject = f"{region_name} - DBP Report - {today}"
    intro = f"Please find attached the complete DBP report of {region_name} ({len(codes)} branches)."
    closing = ["Kindly review the pending matters of your branch and arrange necessary action at the earliest.",
               "Please update the status once the pending items are resolved."]
    body = "\n".join(["Dear Team,", "", intro, "", "Summary is given in the attached report (SUMMARY sheet).", ""] + closing + ["", "Regards,", "DBP Team"])
    html_body = ('<div style="font-family:Calibri,Arial,sans-serif;font-size:11pt">'
                 f"<p>Dear Team,</p><p>{_html.escape(intro)}</p>{table}"
                 f"<p>{'<br>'.join(_html.escape(x) for x in closing)}</p><p>Regards,<br>DBP Team</p></div>")

    room = max(1, int(max_recipients) - len(cc_all))
    chunks = [staff[i:i + room] for i in range(0, len(staff), room)] or [[]]
    mails = []
    for i, chunk in enumerate(chunks, 1):
        subj = subject if len(chunks) == 1 else f"{subject} ({i}/{len(chunks)})"
        m = {"to_list": [] if bcc_staff else list(chunk), "cc_list": list(cc_all),
             "bcc_list": list(chunk) if bcc_staff else [],
             "subject": subj, "body": body, "html_body": html_body,
             "attachment_bytes": report_bytes, "attachment_name": report_name,
             "branch_code": f"ALL-{i}", "branch_name": f"Full report ({len(chunk)} people)", "label": f"ALL-{i}"}
        if not m["to_list"]:                      # To khaali na rahe (BCC mode / sirf area managers)
            if m["cc_list"]:
                m["to_list"] = [m["cc_list"][0]]; m["cc_list"] = m["cc_list"][1:]
        mails.append(m)
    return {"mails": mails, "branches": per_branch, "staff": len(staff), "area": len(cc_all)}
