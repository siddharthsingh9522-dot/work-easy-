# -*- coding: utf-8 -*-
"""Data-grounded OM Report AI chatbot.

The chatbot first executes deterministic queries against the loaded data.
Gemini is optional and is used only to improve wording.  Numbers, branch
codes and docket lists are never invented by Gemini.
"""
import re
import pandas as pd
from .gemini_client import call_gemini, call_gemini_auto, GeminiError
from .directory_loader import branch_lookup
from .report_builder import CATEGORY_DISPLAY_NAMES, count_by_branch
from .ingest import resolve_ddr_branch, detect_branch_column

CATEGORY_ALIASES = {
    "PENDING_BILLING": ["pending billing", "adr", "adr made", "billing pending"],
    "POD_NOT_BILLED": ["pod on net", "net pod", "not billed", "net"],
    "DDR_PENDING": ["ddr", "delivery ddr"],
    "PAID": ["paid"],
    "TOPAY": ["to pay", "topay", "to-pay"],
}
PENDING_CATS = ["PENDING_BILLING", "POD_NOT_BILLED", "DDR_PENDING"]

SYSTEM_INSTRUCTION = (
    "Tum OM Logistics internal report assistant ho. FACTS ke bahar ka koi "
    "number, branch, docket, email ya recipient invent mat karo. User ko "
    "short Hinglish mein answer do. Agar data nahi mila to clearly bolo."
)


def _extract_codes(q):
    return [int(x) for x in re.findall(r"\b\d{3,5}\b", q or "")]


def _extract_names(q, branch_name_map):
    q = (q or "").upper()
    found = []
    for code, name in (branch_name_map or {}).items():
        if name and str(name).upper() in q:
            found.append(int(code))
    return found


def _detect_category(q):
    ql = (q or "").lower()
    # Most-specific first.
    if any(x in ql for x in ["pending billing", "adr made", "adr"]):
        return "PENDING_BILLING"
    if any(x in ql for x in ["pod on net", "net pod", "net"]):
        return "POD_NOT_BILLED"
    if "ddr" in ql:
        return "DDR_PENDING"
    if "to pay" in ql or "topay" in ql:
        return "TOPAY"
    if "paid" in ql:
        return "PAID"
    return None


def _is_pending_query(q):
    ql=(q or "").lower()
    return "pending" in ql or "pending matters" in ql


def _filter_branch(df, category, code):
    if df is None or df.empty:
        return pd.DataFrame()
    code = int(code)
    if "_SECTION_BRANCH" in df.columns:
        return df[pd.to_numeric(df["_SECTION_BRANCH"], errors="coerce") == code].copy()
    if category == "DDR_PENDING":
        cols={str(c).strip().upper():c for c in df.columns}
        fc=cols.get("DLY_CHALLAN_FROM") or cols.get("B. CODE") or cols.get("B.CODE")
        cc=cols.get("DLY_CHALLAN")
        if not fc: return pd.DataFrame()
        keep=[]
        for idx,r in df.iterrows():
            b,_=resolve_ddr_branch(r.get(fc), r.get(cc) if cc else None)
            if b==code: keep.append(idx)
        return df.loc[keep].copy()
    col,kind=detect_branch_column(df,category)
    if col is None: return pd.DataFrame()
    if kind=="code":
        return df[pd.to_numeric(df[col], errors="coerce")==code].copy()
    return pd.DataFrame()


def _dockets(categorized_data, code, category=None):
    cats=[category] if category else PENDING_CATS
    chunks=[]
    for cat in cats:
        part=_filter_branch(categorized_data.get(cat),cat,code)
        if not part.empty:
            part["_CATEGORY"]=cat
            chunks.append(part)
    if not chunks: return pd.DataFrame()
    out=pd.concat(chunks,ignore_index=True)
    cn=next((c for c in out.columns if str(c).strip().upper() in ("CN_NO","CN NO","CN_NO.")),None)
    if cn:
        out=out.drop_duplicates(subset=[cn],keep="first")
    return out


def _summary_row(summary_df, code):
    sdf=summary_df.copy()
    if "B.CODE" not in sdf.columns: return None
    sdf["_CODE_"]=pd.to_numeric(sdf["B.CODE"],errors="coerce")
    rows=sdf[sdf["_CODE_"]==int(code)]
    return rows.iloc[0] if not rows.empty else None


def deterministic_answer(question, summary_df, branch_name_map, categorized_data, directory_df=None):
    q=question or ""
    codes=set(_extract_codes(q))
    codes.update(_extract_names(q,branch_name_map))
    cat=_detect_category(q)

    # "branch-wise" / "all branches" queries.
    if not codes and any(x in q.lower() for x in ["branch wise","branch-wise","all branch","sab branch","top branch","highest"]):
        if cat:
            counts=count_by_branch(categorized_data.get(cat),cat,branch_name_map)
            lines=[]
            for code,val in counts.sort_values(ascending=False).head(15).items():
                name=(branch_name_map or {}).get(int(code),"UNKNOWN BRANCH")
                lines.append(f"{int(code)} - {name}: {int(val)}")
            return f"{CATEGORY_DISPLAY_NAMES.get(cat,cat)} branch-wise count:\n" + "\n".join(lines)
        return "Branch-wise report loaded hai. Kisi category ka naam bhi batao, jaise 'branch wise DDR pending'."

    if not codes:
        return None

    answers=[]
    for code in sorted(codes):
        row=_summary_row(summary_df,code)
        name=(row.get("B.NAME") if row is not None else None) or (branch_name_map or {}).get(code) or "UNKNOWN BRANCH"
        if cat:
            detail=_filter_branch(categorized_data.get(cat),cat,code)
            count=len(detail.drop_duplicates(subset=[next((c for c in detail.columns if str(c).strip().upper() in ("CN_NO","CN NO")), detail.columns[0])]) if not detail.empty else detail)
            # For category counts, use summary when available; detail is useful
            # for docket-level questions.
            display_col=CATEGORY_DISPLAY_NAMES.get(cat,cat)
            val=int(row.get(display_col,0)) if row is not None and display_col in row else int(count)
            answers.append(f"Branch {code} ({name}) — {display_col}: {val}")
        elif _is_pending_query(q):
            vals={}
            if row is not None:
                for c in ["PENDING BILLING","POD ON NET BUT NOT BILLED","DDR PENDING"]:
                    vals[c]=int(row.get(c,0) or 0)
            total=sum(vals.values())
            answers.append(f"Branch {code} ({name}) — total pending: {total}; " +
                           ", ".join(f"{k}: {v}" for k,v in vals.items()))
        else:
            if row is not None:
                cols=[c for c in summary_df.columns if c not in ("S.NO","B.NAME","B.CODE","_CODE_")]
                answers.append(f"Branch {code} ({name}) — " + ", ".join(f"{c}: {row.get(c)}" for c in cols))
            else:
                answers.append(f"Branch {code}: summary row nahi mila.")

        # Docket list requested.
        if any(x in q.lower() for x in ["docket","cn no","cn_no","consignment","kaun kaun","kon kon","list"]):
            detail=_dockets(categorized_data,code,cat)
            if detail.empty:
                answers.append("Docket-level data nahi mila.")
            else:
                cn=next((c for c in detail.columns if str(c).strip().upper() in ("CN_NO","CN NO","CN_NO.")),None)
                if cn:
                    vals=detail[cn].dropna().astype(str).drop_duplicates().tolist()
                    preview=vals[:100]
                    answers.append(f"Dockets ({len(vals)}): " + ", ".join(preview) +
                                   (f" ... +{len(vals)-100} more" if len(vals)>100 else ""))
                else:
                    answers.append("CN_NO column source data mein nahi mila.")

        # Contacts requested.
        if any(x in q.lower() for x in ["contact","email","mail","branch manager","incharge"]):
            con=branch_lookup(directory_df,code) if directory_df is not None else pd.DataFrame()
            if not con.empty:
                ccols=[c for c in ["NAME","DESIGNATION","EMAIL","MOBILE"] if c in con.columns]
                con=con[ccols].drop_duplicates()
                answers.append("Contacts:\n"+con.head(10).to_string(index=False))
            else:
                answers.append("Directory mein is branch ka contact nahi mila.")

    return "\n\n".join(answers)


def build_facts_for_question(question, summary_df, branch_name_map, directory_df=None, categorized_data=None):
    ans=deterministic_answer(question,summary_df,branch_name_map,categorized_data or {},directory_df)
    return ans or "Question ke liye deterministic facts nahi mile."


def answer_question(question, summary_df, branch_name_map, api_key=None, model=None,
                    directory_df=None, categorized_data=None):
    # Mail request: deterministic draft, no AI-generated recipient guessing.
    if any(x in (question or "").lower() for x in ["mail banao", "mail bana", "email banao", "email bana"]):
        codes=set(_extract_codes(question))
        codes.update(_extract_names(question, branch_name_map))
        if codes and categorized_data is not None:
            from .mail_generator import build_mail
            parts=[]
            for code in sorted(codes):
                mail=build_mail(categorized_data,summary_df,directory_df,code,None,
                                "Region DBP")
                parts.append(
                    f"**{mail['branch_name']} ({code})**\n"
                    f"**To:** {mail['to'] or 'NOT FOUND'}\n"
                    f"**CC:** {mail['cc'] or 'NOT FOUND'}\n"
                    f"**Subject:** {mail['subject']}\n\n"
                    f"{mail['body']}"
                )
            return "\n\n---\n\n".join(parts)
    facts=build_facts_for_question(question,summary_df,branch_name_map,directory_df,categorized_data)
    prompt=f"FACTS:\n{facts}\n\nUSER QUESTION: {question}\n\nFACTS ko badle bina answer do:"
    try:
        # api_key diya ho (explicit override) to wahi use hoti hai, warna
        # admin ki hidden shared key + auto model-fallback khud sambhal leta hai.
        # Koi bhi AI service na mile to bhi user ko deterministic facts mil jaate
        # hain - chatbot kabhi poori tarah "band" nahi hota.
        return call_gemini_auto(prompt, system_instruction=SYSTEM_INSTRUCTION,
                                 user_api_key=api_key, user_model=model)
    except GeminiError:
        return facts
