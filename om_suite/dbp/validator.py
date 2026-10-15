# -*- coding: utf-8 -*-
"""
validator.py
Data sahi hai ya nahi, calculation sahi hai ya nahi -- yeh sab yaha check
hota hai. Do tarah ke checks:

1. RULE-BASED (bina Gemini ke, instant, free) -- duplicate CN numbers,
   missing branch column, mixed text/number branch codes, unknown branch
   codes (directory mein nahi hai), aur agar file mein purani SUMMARY sheet
   bhi hai to uske saath reconcile (compare) karna.
2. AI-ASSISTED (Gemini se) -- upar ke rule-based issues ki list Gemini ko
   dekar ek plain-Hindi/English summary + priority likhwate hain, taaki
   user ko turant samajh aaye sabse pehle kya dekhna hai.
"""
import pandas as pd

from .config import CATEGORY_BRANCH_COLUMN, COUNTABLE_CATEGORIES
from .ingest import detect_branch_column
from .report_builder import count_ddr_by_branch


def check_missing_branch_columns(categorized_data: dict) -> list:
    """Har category ka CONFIRMED branch column sheet mein maujood hai ya nahi.
    NOTE: agar sheet section-based nikli (df mein '_SECTION_BRANCH' column
    hai -- ingest.py ne already branch resolve kar diya section-marker se),
    to yeh check skip hota hai -- column missing hona yahan problem NAHI hai,
    balki EXPECTED hai (branch section-marker se aaya, column se nahi)."""
    issues = []
    for cat, expected_col in CATEGORY_BRANCH_COLUMN.items():
        df = categorized_data.get(cat)
        if df is None or df.empty or "_SECTION_BRANCH" in df.columns:
            continue
        col, kind = detect_branch_column(df, cat)
        if col is None:
            issues.append({
                "severity": "HIGH",
                "type": "missing_column",
                "message": f"'{cat}' sheet mein expected column '{expected_col}' nahi mila. "
                           f"Available columns: {list(df.columns)[:10]}... "
                           f"Iske bina is category ka branch-wise count GALAT/khaali aayega."
            })
    return issues


def check_mixed_type_branch_codes(categorized_data: dict) -> list:
    """Branch-code column mein text aur number dono type mile (jaise '2605'
    string aur 2605 number) -- yeh Excel mein duplicate pivot rows ki wajah
    banta hai. Hum aage numeric normalize kar dete hain, lekin user ko batana
    zaroori hai ki source data mein yeh inconsistency hai (taaki woh apne
    ERP/export process mein fix karwa sakein)."""
    issues = []
    for cat, expected_col in CATEGORY_BRANCH_COLUMN.items():
        df = categorized_data.get(cat)
        if df is None or df.empty or "_SECTION_BRANCH" in df.columns:
            continue
        col, kind = detect_branch_column(df, cat)
        if col is None or kind != "code":
            continue
        raw = df[col].dropna()
        n_str = raw.apply(lambda v: isinstance(v, str)).sum()
        n_num = raw.apply(lambda v: isinstance(v, (int, float))).sum()
        if n_str > 0 and n_num > 0:
            issues.append({
                "severity": "MEDIUM",
                "type": "mixed_type_branch_code",
                "message": f"'{cat}' sheet ke '{col}' column mein {n_str} rows text-format "
                           f"aur {n_num} rows number-format mein hain (jaise '2605' vs 2605). "
                           f"Hum inko automatically merge karke count karte hain, lekin source "
                           f"system mein yeh consistent karwa lena chahiye."
            })
    return issues


def check_duplicate_cn(categorized_data: dict) -> list:
    """Ek hi CN number ek hi category ke andar do baar to nahi aa gaya
    (data-entry / export duplication)."""
    issues = []
    for cat in COUNTABLE_CATEGORIES:
        df = categorized_data.get(cat)
        if df is None or df.empty:
            continue
        cn_col = None
        for c in df.columns:
            if str(c).strip().upper() in ("CN_NO", "CN NO"):
                cn_col = c
                break
        if cn_col is None:
            continue
        non_blank = df.dropna(subset=[cn_col])  # blank/NaN CN_NO ko duplicate mat maano
        dupes = non_blank[non_blank.duplicated(subset=[cn_col], keep=False)]
        if not dupes.empty:
            n_dup_cn = dupes[cn_col].nunique()
            issues.append({
                "severity": "MEDIUM",
                "type": "duplicate_cn",
                "message": f"'{cat}' sheet mein {n_dup_cn} CN number(s) repeat ho rahe hain "
                           f"(total {len(dupes)} duplicate rows). Yeh double-counting kar sakta hai."
            })
    return issues


def check_unknown_branch_codes(categorized_data: dict, directory_code_set: set,
                                branch_name_map: dict) -> list:
    """Jo branch codes report mein aaye, kya woh directory file mein bhi
    hain? Agar nahi, to ho sakta hai code galat type ho gaya ho ya naya
    branch ho jo directory mein add nahi hua."""
    if not directory_code_set:
        return []
    issues = []
    all_codes = set(branch_name_map.keys())
    for cat in COUNTABLE_CATEGORIES:
        df = categorized_data.get(cat)
        if df is None or df.empty:
            continue
        if "_SECTION_BRANCH" in df.columns:
            all_codes.update(pd.to_numeric(df["_SECTION_BRANCH"], errors="coerce").dropna().astype(int).unique().tolist())
            continue
        col, kind = detect_branch_column(df, cat)
        if col is None or kind != "code":
            continue
        codes = pd.to_numeric(df[col], errors="coerce").dropna().astype(int).unique().tolist()
        all_codes.update(codes)
    unknown = sorted(c for c in all_codes if c not in directory_code_set)
    if unknown:
        issues.append({
            "severity": "LOW",
            "type": "unknown_branch_code",
            "message": f"Yeh branch codes directory file mein nahi mile: {unknown}. "
                       f"Ho sakta hai naya branch ho, ya code galat type ho gaya ho."
        })
    return issues


def check_ddr_challan_decode(categorized_data: dict) -> list:
    """DDR PENDING mein DLY_CHALLAN_FROM > threshold (jaise 9994) wale rows
    DLY_CHALLAN se decode hote hain -- yeh check transparently batata hai
    kitne rows decode hue aur kitne UNRESOLVED reh gaye (jinka branch pata
    nahi chal paya, guess nahi kiya gaya -- REVIEW REQUIRED)."""
    issues = []
    df = categorized_data.get("DDR_PENDING")
    if df is None or df.empty:
        return issues
    _counts, unresolved, decoded = count_ddr_by_branch(df)
    if decoded:
        issues.append({
            "severity": "INFO",
            "type": "ddr_challan_decoded",
            "message": f"DDR PENDING: {decoded} row(s) mein DLY_CHALLAN_FROM > threshold tha, "
                       f"inka asli branch DLY_CHALLAN se decode kiya gaya."
        })
    if unresolved:
        issues.append({
            "severity": "HIGH",
            "type": "ddr_challan_unresolved",
            "message": f"DDR PENDING: {unresolved} row(s) ka branch decode NAHI ho paya "
                       f"(DLY_CHALLAN_FROM > threshold tha lekin DLY_CHALLAN se saaf branch+year "
                       f"pattern nahi mila, ya DLY_CHALLAN khaali tha). Yeh rows final count mein "
                       f"SHAAMIL NAHI hain -- REVIEW REQUIRED, manually check karo."
        })
    return issues


def reconcile_with_pasted_summary(path: str, computed_summary: pd.DataFrame) -> list:
    """Agar uploaded file mein already ek 'SUMMARY' sheet hai (purani/manual
    banayi hui), to usse hamare naye computed summary se compare karo aur
    farak dikhao branch-wise."""
    import openpyxl
    issues = []
    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception:
        return issues
    if "SUMMARY" not in wb.sheetnames:
        return issues
    try:
        old_df = pd.read_excel(path, sheet_name="SUMMARY", header=2)
    except Exception:
        return issues

    old_df.columns = [str(c).strip().upper() for c in old_df.columns]
    code_col = next((c for c in old_df.columns if "B.CODE" in c or c == "B.CODE"), None)
    if code_col is None:
        return issues

    computed = computed_summary.copy()
    computed.columns = [str(c).strip().upper() for c in computed.columns]
    if "B.CODE" not in computed.columns:
        return issues

    old_df["_CODE_"] = pd.to_numeric(old_df[code_col], errors="coerce")
    computed["_CODE_"] = pd.to_numeric(computed["B.CODE"], errors="coerce")

    common_cats = [c for c in ["PAID", "TO PAY", "PENDING BILLING",
                                "POD ON NET BUT NOT BILLED", "DDR PENDING", "TOTAL"]
                    if c in old_df.columns and c in computed.columns]

    name_col = next((c for c in old_df.columns if "B.NAME" in c or c == "B.NAME"), None)
    mismatches = []
    ambiguous = []
    for _, row in computed.dropna(subset=["_CODE_"]).iterrows():
        code = row["_CODE_"]
        old_candidates = old_df[old_df["_CODE_"] == code]
        if old_candidates.empty:
            continue
        if len(old_candidates) > 1 and name_col:
            # jab ek hi B.CODE do rows mein ho (jaise ek client-sheet aur ek
            # regular branch dono 2601 share karte hain) -- naam se match karo
            name_match = old_candidates[old_candidates[name_col].astype(str).str.strip().str.upper()
                                         == str(row.get("B.NAME", "")).strip().upper()]
            if len(name_match) == 1:
                old_row = name_match.iloc[0]
            else:
                ambiguous.append(int(code))
                continue
        else:
            old_row = old_candidates.iloc[0]
        for cat in common_cats:
            new_v = row.get(cat)
            old_v = old_row.get(cat)
            try:
                if pd.notna(new_v) and pd.notna(old_v) and int(new_v) != int(old_v):
                    mismatches.append(f"Branch {int(code)}: {cat} = {int(new_v)} (naya) vs {int(old_v)} (purana)")
            except (TypeError, ValueError):
                continue

    if mismatches:
        issues.append({
            "severity": "HIGH",
            "type": "reconciliation_mismatch",
            "message": f"Purani SUMMARY sheet se {len(mismatches)} jagah farak mila:\n" +
                       "\n".join(mismatches[:25]) +
                       ("\n... aur bhi hain" if len(mismatches) > 25 else "")
        })
    elif not ambiguous:
        issues.append({
            "severity": "INFO",
            "type": "reconciliation_ok",
            "message": "Purani SUMMARY sheet se sab numbers match ho gaye — koi farak nahi mila."
        })
    if ambiguous:
        issues.append({
            "severity": "LOW",
            "type": "reconciliation_ambiguous",
            "message": f"Branch code(s) {sorted(set(ambiguous))} purani SUMMARY mein ek se zyada baar "
                       f"(jaise ek regular branch + ek client-sheet dono same code share karte hain) "
                       f"mile — naam match nahi hua isliye compare skip kiya. Manually check kar lena."
        })
    return issues


def check_excluded_sheets(categorized_data: dict) -> list:
    """OTHER END / DISPUTE MATTER (ya config.EXCLUDED_SHEET_KEYWORDS mein jo
    bhi ho) jaanbujh kar FINAL summary se bahar rakhe jaate hain -- yeh check
    unko transparently dikhata hai (spec: exclude but never silently
    discard). Ismein DDR ke 'undetermined' sections (DDR_EXCLUDED) bhi
    shaamil hote hain."""
    issues = []
    excluded_blocks = categorized_data.get("EXCLUDED_BLOCKS", {})
    for sheet_name, blocks in excluded_blocks.items():
        total_rows = sum(b.get("row_count", 0) for b in blocks if "error" not in b)
        cats = sorted({b.get("category") for b in blocks if "error" not in b and b.get("category")})
        if total_rows:
            issues.append({
                "severity": "INFO",
                "type": "sheet_excluded",
                "message": f"'{sheet_name.strip()}' sheet ({total_rows} rows, category: {', '.join(cats)}) "
                           f"jaanbujh kar FINAL summary se EXCLUDE ki gayi hai (business rule) -- "
                           f"data yahan audit ke liye hai, final total mein nahi jodi gayi."
            })
    ddr_excluded = categorized_data.get("DDR_EXCLUDED", [])
    for e in ddr_excluded:
        issues.append({
            "severity": "INFO",
            "type": "ddr_section_excluded",
            "message": f"DDR PENDING: branch-marker {e.get('branch_marker')} ka poora section "
                       f"({e.get('row_count')} rows, sheet '{e.get('sheet')}') final count se "
                       f"EXCLUDE kiya gaya — {e.get('reason')}"
        })
    return issues



def check_summary_duplicate_branch_codes(computed_summary: pd.DataFrame) -> list:
    """Final SUMMARY mein ek B.CODE ek hi baar hona chahiye."""
    if computed_summary is None or computed_summary.empty or "B.CODE" not in computed_summary.columns:
        return []
    codes = pd.to_numeric(computed_summary["B.CODE"], errors="coerce").dropna().astype(int)
    dup = codes[codes.duplicated(keep=False)].unique().tolist()
    if dup:
        return [{
            "severity": "HIGH",
            "type": "summary_duplicate_branch_code",
            "message": f"Final SUMMARY mein duplicate B.CODE mila: {dup}. "
                       "Ek branch code ke liye sirf ek consolidated row honi chahiye."
        }]
    return []

def check_blank_or_unparseable_branch_codes(categorized_data: dict) -> list:
    """ADVANCED CHECK (user-requested): kisi category-sheet ke confirmed
    branch-code column mein agar kuch rows ka value BLANK/unreadable hai,
    to woh rows kisi bhi branch ke total mein count NAHI hoti (silently
    dropped) -- exactly wahi symptom jo 'sheet mein pending hai lekin
    SUMMARY mein kam dikhta hai' ka sabse common reason hota hai."""
    issues = []
    for cat, expected_col in CATEGORY_BRANCH_COLUMN.items():
        df = categorized_data.get(cat)
        if df is None or df.empty or "_SECTION_BRANCH" in df.columns:
            continue  # section-based sheets ka branch already resolved hai
        col, kind = detect_branch_column(df, cat)
        if col is None or kind != "code":
            continue
        total_rows = len(df)
        blank_count = int(pd.to_numeric(df[col], errors="coerce").isna().sum())
        if blank_count > 0:
            pct = round(100 * blank_count / total_rows, 1) if total_rows else 0
            issues.append({
                "severity": "HIGH",
                "type": "blank_branch_code_rows_dropped",
                "message": f"'{cat}' sheet ki {total_rows} rows mein se {blank_count} rows "
                           f"({pct}%) ka '{col}' column blank/unreadable hai — yeh rows KISI "
                           f"bhi branch ke total mein count nahi ho rahi (silently dropped)."
            })
    return issues


def audit_summary_matches_source_data(categorized_data: dict, computed_summary: pd.DataFrame) -> list:
    """ADVANCED CHECK (user-requested): har category ke liye, SOURCE sheets
    (main category-sheet + customer/OTHER_BLOCKS sheets) mein asal mein
    kitne pending items maujood hain -- aur SUMMARY mein us category ka
    grand-total kitna dikha -- dono ko compare karta hai.

    Agar farak hai, to woh EXACTLY batata hai kitne items 'gayab' ho gaye
    (ya double-count ho gaye) -- yeh manual eyeballing se zyada reliable
    hai kyunki yeh HAR generate ke saath automatically chalega."""
    issues = []
    if computed_summary is None or computed_summary.empty:
        return issues
    from .report_builder import CATEGORY_DISPLAY_NAMES  # local import: avoid circular import at module load

    sdf = computed_summary.copy()
    sdf.columns = [str(c).strip().upper() for c in sdf.columns]
    total_rows = sdf[sdf["B.CODE"].astype(str).str.strip().str.upper() == "TOTAL"]
    if total_rows.empty:
        return issues
    total_row = total_rows.iloc[0]

    other_blocks = categorized_data.get("OTHER_BLOCKS", {}) or {}
    customer_sheets = categorized_data.get("CUSTOMER_SHEETS", {}) or {}

    for cat in COUNTABLE_CATEGORIES:
        display = CATEGORY_DISPLAY_NAMES[cat].upper()
        if display not in sdf.columns:
            continue
        summary_total = int(total_row.get(display, 0) or 0)

        # 1) main category-sheet ki saari rows (blank-branch wali bhi --
        #    woh bhi 'pending' hi hai, chahe unattributed ho)
        main_df = categorized_data.get(cat)
        true_total = len(main_df) if main_df is not None else 0

        # 2) customer/OTHER_BLOCKS sheets ka is category ka contribution
        for _sheet, blocks in other_blocks.items():
            for b in (blocks or []):
                if isinstance(b, dict) and b.get("category") == cat:
                    true_total += int(b.get("row_count", 0) or 0)

        # 3) flat CUSTOMER_SHEETS (jinme apna "CATEGORY" column hota hai)
        for _sheet, cdf in customer_sheets.items():
            if cdf is None or cdf.empty:
                continue
            cat_col = next((c for c in cdf.columns if str(c).strip().upper() == "CATEGORY"), None)
            if cat_col is None:
                continue
            for value in cdf[cat_col].dropna():
                text = str(value).strip().upper()
                hit = ("PENDING BILLING" in text or "ADR" in text) if cat == "PENDING_BILLING" else \
                      ("POD ON NET" in text or "NET" in text) if cat == "POD_NOT_BILLED" else \
                      (text == "PAID") if cat == "PAID" else \
                      ("TO PAY" in text or text == "TOPAY") if cat == "TOPAY" else \
                      ("DDR" in text) if cat == "DDR_PENDING" else False
                if hit:
                    true_total += 1

        gap = true_total - summary_total
        if gap != 0:
            issues.append({
                "severity": "HIGH",
                "type": "summary_source_mismatch",
                "message": f"'{CATEGORY_DISPLAY_NAMES[cat]}': source sheets mein total {true_total} "
                           f"pending items hain, lekin SUMMARY mein {summary_total} dikhaye gaye "
                           f"(farak: {gap:+d}). Ho sakta hai blank branch-code, ya kisi block ka "
                           f"category-match fail ho gaya ho."
            })
        else:
            issues.append({
                "severity": "INFO",
                "type": "summary_source_match_ok",
                "message": f"'{CATEGORY_DISPLAY_NAMES[cat]}': SUMMARY ka total ({summary_total}) "
                           f"source sheets se exact match karta hai."
            })
    return issues


def run_all_checks(categorized_data: dict, directory_code_set: set = None,
                    branch_name_map: dict = None, source_path_for_reconcile: str = None,
                    computed_summary: pd.DataFrame = None) -> list:
    """Sabhi checks ek saath chala kar ek combined issue-list deta hai,
    severity ke hisaab se sorted (HIGH pehle)."""
    issues = []
    issues += check_missing_branch_columns(categorized_data)
    issues += check_blank_or_unparseable_branch_codes(categorized_data)
    issues += check_mixed_type_branch_codes(categorized_data)
    issues += check_duplicate_cn(categorized_data)
    issues += check_ddr_challan_decode(categorized_data)
    issues += check_excluded_sheets(categorized_data)
    if directory_code_set:
        issues += check_unknown_branch_codes(categorized_data, directory_code_set, branch_name_map or {})
    if computed_summary is not None:
        issues += check_summary_duplicate_branch_codes(computed_summary)
        issues += audit_summary_matches_source_data(categorized_data, computed_summary)
    if source_path_for_reconcile and computed_summary is not None:
        issues += reconcile_with_pasted_summary(source_path_for_reconcile, computed_summary)

    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
    issues.sort(key=lambda x: order.get(x.get("severity", "INFO"), 9))
    return issues
