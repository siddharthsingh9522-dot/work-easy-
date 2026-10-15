# -*- coding: utf-8 -*-
"""
ingest.py
Raw department export files (.xlsx) ko padhta hai, har sheet ko category
mein daalta hai (PAID/TOPAY/PENDING_BILLING/...), aur asli header row
dhundh kar clean pandas DataFrame banata hai (kyunki in files mein
header kabhi row 3 pe hota hai, kabhi row 6 pe — title/branch-code lines
upar likhi hoti hain).
"""
import re
import datetime
import pandas as pd
import openpyxl

from .config import (
    SHEET_CATEGORY_RULES, HEADER_MARKER_KEYWORDS, MAX_HEADER_SCAN_ROWS,
    BRANCH_CODE_COLUMNS, BRANCH_NAME_COLUMNS, CATEGORY_BRANCH_COLUMN,
    ADR_BRANCH_COLUMN_ALIASES, ADR_NO_DATE_BRANCH_COLUMN_ALIASES,
    BILL_BRANCH_CODE_COLUMN_ALIASES,
    OTHER_SHEET_BLOCK_TITLE_RULES, OTHER_SHEET_BLOCK_TITLE_EXACT_ALIASES,
    NET_SHEET_ANCHORS, ADR_SHEET_ANCHORS, SHEET_FAMILY_FILLER_WORDS,
    EXCLUDED_SHEET_KEYWORDS,
    DDR_CHALLAN_FROM_DECODE_THRESHOLD, DDR_CHALLAN_BRANCH_LENGTHS,
)


def dedupe_columns(columns):
    """Duplicate column names ko unique bana deta hai (REMARKS, REMARKS_2, ...).
    Excel exports mein kabhi ek hi header do baar aata hai; duplicate columns
    hone par pd.concat 'Reindexing only valid with uniquely valued Index
    objects' error deta hai. Pehla column apna original naam rakhta hai."""
    seen = {}
    out = []
    for c in columns:
        c = str(c)
        if c in seen:
            seen[c] += 1
            new = f"{c}_{seen[c]}"
            while new in seen:
                seen[c] += 1
                new = f"{c}_{seen[c]}"
            seen[new] = 1
            out.append(new)
        else:
            seen[c] = 1
            out.append(c)
    return out


def _normalize_colname(name) -> str:
    """Column header ko compare ke liye normalize karta hai -- case, spaces,
    underscores, dots, hyphens, slashes sab hata kar -- taaki 'ADR BRANCH',
    'ADR_BRANCH', 'Adr-Branch', 'adr  branch', 'ADR/BRANCH' sab EK HI maane
    jayein. Isse agar front-end/export style badal jaaye (spacing, case,
    punctuation), tab bhi data-column pehchaana ja sakta hai."""
    return re.sub(r"[^A-Z0-9]", "", str(name).strip().upper())


def _find_column_by_aliases(df: pd.DataFrame, aliases: list):
    """Diye gaye alias-names (config.py) ko normalize karke df.columns se
    match karta hai. Column header thoda bhi alag likha ho (space/underscore/
    hyphen/case/slash), phir bhi mil jaata hai."""
    norm_cols = {_normalize_colname(c): c for c in df.columns}
    for alias in aliases:
        hit = norm_cols.get(_normalize_colname(alias))
        if hit is not None:
            return hit
    return None


def _extract_branch_from_combo(value):
    """'ADR_NO_DATE_BRANCH' jaisa combo-column ek hi cell mein teen values
    comma-separated deta hai: '<ADR NO>,<ADR DATE>,<ADR BRANCH>', jaise
    '19202517000399,29-JAN-26,530'. CONFIRMED: order hamesha fixed hai --
    LAST comma-separated token hi ADR BRANCH hai. Yeh function comma se
    split karke last non-empty token ko integer branch-code mein convert
    karta hai; parse na ho paye to None (guess nahi karte)."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.upper() in ("NAN", "NONE", ""):
        return None
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if not parts:
        return None
    last = parts[-1]
    try:
        return int(float(last))
    except (TypeError, ValueError):
        return None


def _candidate_year_codes() -> set:
    """Current, pichhla aur agla saal ke last-2-digits -- DLY_CHALLAN ke
    andar 'year marker' dhundhne ke liye (hardcoded '26' kahin nahi, taaki
    yeh agle saal bhi automatically chalta rahe)."""
    y = datetime.datetime.now().year % 100
    return {f"{(y - 1) % 100:02d}", f"{y:02d}", f"{(y + 1) % 100:02d}"}


def resolve_ddr_branch(dly_challan_from, dly_challan):
    """
    DDR PENDING branch-resolution rule (CONFIRMED):
      - Normally DLY_CHALLAN_FROM khud hi branch code hota hai.
      - Agar DLY_CHALLAN_FROM > DDR_CHALLAN_FROM_DECODE_THRESHOLD (confirmed
        range 5000-9999, jaise 9994), to woh branch code NAHI hai -- asli
        branch DLY_CHALLAN ke shuru ke digits se nikalna hai (year-code se
        pehle wala hissa, jaise '13122612003451' -> branch '1312', year '26').
    Return: (branch_code:int|None, status) jaha status ek hai:
      "direct"     -> DLY_CHALLAN_FROM seedha branch code hai
      "decoded"    -> DLY_CHALLAN se decode hua
      "unresolved" -> decode nahi ho paya (data missing/format samajh nahi aaya) —
                       isko GUESS nahi karte, caller isse REVIEW REQUIRED maanega.
    """
    try:
        from_val = int(float(str(dly_challan_from).strip()))
    except (TypeError, ValueError):
        return None, "unresolved"

    if from_val <= DDR_CHALLAN_FROM_DECODE_THRESHOLD:
        return from_val, "direct"

    if dly_challan is None or (isinstance(dly_challan, float) and pd.isna(dly_challan)):
        return None, "unresolved"
    raw = str(dly_challan).strip()
    if raw.endswith(".0"):
        raw = raw[:-2]
    raw = re.sub(r"\D", "", raw)  # sirf digits (CN/challan kabhi scientific-notation string na ban jaye)
    if not raw:
        return None, "unresolved"

    year_codes = _candidate_year_codes()
    for blen in DDR_CHALLAN_BRANCH_LENGTHS:
        if len(raw) > blen + 2:
            branch_part, year_part = raw[:blen], raw[blen:blen + 2]
            if year_part in year_codes:
                try:
                    return int(branch_part), "decoded"
                except ValueError:
                    continue
    return None, "unresolved"


def parse_ddr_sections(path: str, sheet_name: str):
    """
    DDR PENDING sheet ka SECTION-based structure parse karta hai: har branch
    ka apna chhota block hota hai
        <branch_code>                          <- marker row, SIRF 1 cell filled
        CN_NO | CN_DATE | ... | DLY_CHALLAN_FROM | ...   <- header row
        <data rows...>
        <blank row(s)>
    CONFIRMED (do alag golden reference files se cross-verify kiya gaya):
    section marker HI branch-of-truth hai -- chahe DLY_CHALLAN_FROM column
    mein value 9994/decode-needed ho ya direct branch-code ho, section ke
    andar ki saari rows usi marker ke branch mein count hoti hain (ek file
    mein branch 1312 'GREATER NOIDA' explicitly named/included milta hai
    total ke saath -- yeh 'undetermined bucket' nahi, ek asli branch hai).
    Row-level DLY_CHALLAN_FROM/DLY_CHALLAN sirf AUDIT info ke liye track
    hoti hai (kitni rows direct thi, kitni decode-required) -- ismein se
    koi bhi row EXCLUDE nahi hoti.

    Return: (valid_df: pd.DataFrame jisme extra '_SECTION_BRANCH' column hai,
             excluded_sections: list -- sirf genuine parsing-failure cases
             ke liye, jaise poora khaali block; normal business-data kabhi
             yahan nahi aata).
    Agar sheet mein koi single-cell marker row hi na mile (structure match na
    kare), khaali (empty_df, []) deta hai -- caller purane flat/column-based
    parsing par fallback kare.
    """
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet_name]
    all_rows = list(ws.iter_rows(values_only=True))
    n = len(all_rows)

    def _is_marker(row):
        non_null = [(idx, v) for idx, v in enumerate(row) if v is not None]
        if len(non_null) != 1:
            return None
        _, v = non_null[0]
        try:
            return int(float(str(v).strip()))
        except (TypeError, ValueError):
            return None

    sections = []  # (marker, header_row_idx, data_start, data_end) - sab 0-indexed
    i = 0
    while i < n:
        marker = _is_marker(all_rows[i])
        if marker is not None:
            header_i = None
            for j in range(i + 1, min(i + 6, n)):
                vals = [str(v).strip().upper() for v in all_rows[j] if v is not None]
                if any(mk in vals for mk in HEADER_MARKER_KEYWORDS):
                    header_i = j
                    break
            if header_i is None:
                i += 1
                continue
            k = header_i + 1
            blank_streak = 0
            while k < n:
                r = all_rows[k]
                if all(v is None for v in r):
                    blank_streak += 1
                    if blank_streak >= 2:
                        break
                else:
                    blank_streak = 0
                    if _is_marker(r) is not None:
                        break
                k += 1
            sections.append((marker, header_i, header_i + 1, k))
            i = k
            continue
        i += 1

    if not sections:
        return pd.DataFrame(), []

    valid_frames, excluded = [], []
    for marker, header_i, data_start, data_end in sections:
        header_full = [str(v).strip() if v is not None else f"COL{ci}"
                        for ci, v in enumerate(all_rows[header_i])]
        block_df = pd.DataFrame(all_rows[data_start:data_end], columns=dedupe_columns(header_full))
        block_df = block_df.dropna(how="all")
        if block_df.empty:
            continue
        block_df = block_df.copy()
        block_df["_SECTION_BRANCH"] = marker
        valid_frames.append(block_df)

    valid_df = pd.concat(valid_frames, ignore_index=True) if valid_frames else pd.DataFrame()
    return valid_df, excluded


def _family_category(text: str):
    """Sheet-naam / block-title ko ADR ya NET family mein daalta hai agar uske
    SAARE shabd config ke anchor+filler vocabulary mein hon (kam se kam ek
    anchor ke saath). 'NET POD', 'POD NET', 'NETPOD', 'NET-POD', 'NET (POD)',
    'POD', 'ADR' -> haan; 'NET ONE TECHNOLOGIES' (extra shabd) -> nahi."""
    toks = re.sub(r"[^A-Z0-9]+", " ", str(text or "").upper()).split()
    if not toks:
        return None
    net_a, adr_a = set(NET_SHEET_ANCHORS), set(ADR_SHEET_ANCHORS)
    filler = set(SHEET_FAMILY_FILLER_WORDS)
    if any(t not in net_a and t not in adr_a and t not in filler for t in toks):
        return None
    has_net = any(t in net_a for t in toks)
    has_adr = any(t in adr_a for t in toks)
    if has_net and not has_adr:
        return "POD_NOT_BILLED"
    if has_adr and not has_net:
        return "PENDING_BILLING"
    return None



def categorize_sheet(sheet_name: str) -> str:
    """Sheet ka naam dekh kar category return karta hai. Match na mile to
    'OTHER' (jaise client-specific sheets: 'VE COMMERCIAL VEHICLES LTD').
    EXCLUDED_SHEET_KEYWORDS (config.py — 'other end', 'dispute matter') sabse
    PEHLE check hote hain, taaki yeh sheets kabhi bhi normal category mein na
    phisal jaayein (spec: exclude but never silently discard).
    CONFIRMED: kuch files mein sheet ka naam seedha chhota alias hota hai
    ('ADR', 'NET' -- poora 'ADR MADE BUT NOT BILLED' nahi) -- EXACT match
    (substring nahi) se pehchanta hai, taaki koi lamba unrelated naam
    galti se match na ho jaaye."""
    name = sheet_name.strip().upper()
    for kw in EXCLUDED_SHEET_KEYWORDS:
        if kw.upper() in name:
            return "EXCLUDED"
    for category, keywords in SHEET_CATEGORY_RULES:
        for kw in keywords:
            if kw.upper() in name:
                return category
    for category, aliases in OTHER_SHEET_BLOCK_TITLE_EXACT_ALIASES:
        for alias in aliases:
            if name == alias.strip().upper():
                return category
    fam_cat = _family_category(name)
    if fam_cat:
        return fam_cat
    return "OTHER"



def parse_flat_summary_customer_rows(path: str, sheet_name: str) -> list:
    """Read customer/special rows from a flat SUMMARY table.

    Some reference reports intentionally place customer-level rows (for
    example BOSCH LTD, APTIV, TATA) above the branch rows with a blank B.CODE.
    These rows are business data and must be retained in the generated
    SUMMARY, but they are not branch entities and must not be mixed into the
    branch-code aggregation.
    """
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet_name]
    rows = list(ws.iter_rows(values_only=True))
    header_idx = None
    idxs = {}
    for i, row in enumerate(rows[:MAX_HEADER_SCAN_ROWS + 10]):
        found = {}
        for j, v in enumerate(row):
            n = str(v).strip().upper() if v is not None else ""
            if n in ("S.NO", "S NO"):
                found["sno"] = j
            elif n in ("B.NAME", "B NAME", "BRANCH NAME", "BNAME"):
                found["name"] = j
            elif n in ("B.CODE", "B CODE", "BRANCH CODE", "BCODE"):
                found["code"] = j
            elif n == "PAID":
                found["PAID"] = j
            elif n in ("TO PAY", "TOPAY"):
                found["TOPAY"] = j
            elif n in ("PENDING BILLING", "ADR"):
                found["PENDING_BILLING"] = j
            elif n in ("POD ON NET BUT NOT BILLED", "NET"):
                found["POD_NOT_BILLED"] = j
            elif n in ("DDR PENDING", "DDR"):
                found["DDR_PENDING"] = j
            elif n == "TOTAL":
                found["TOTAL"] = j
        if "name" in found and "code" in found and all(k in found for k in
                ("PAID","TOPAY","PENDING_BILLING","POD_NOT_BILLED","DDR_PENDING","TOTAL")):
            header_idx, idxs = i, found
            break
    if header_idx is None:
        return []

    out = []
    seen = set()
    for row in rows[header_idx + 1:]:
        name = row[idxs["name"]] if idxs["name"] < len(row) else None
        code = row[idxs["code"]] if idxs["code"] < len(row) else None
        if name is None or not str(name).strip():
            continue
        # Only blank-code rows are customer/special rows.
        if code is not None and str(code).strip() not in ("", "NAN", "NONE"):
            continue
        n = str(name).strip()
        if n.upper() in ("TOTAL", "GRAND TOTAL"):
            continue
        vals = {}
        for cat in ("PAID","TOPAY","PENDING_BILLING","POD_NOT_BILLED","DDR_PENDING"):
            v = row[idxs[cat]] if idxs[cat] < len(row) else 0
            try:
                vals[cat] = int(float(str(v).strip())) if v is not None else 0
            except (TypeError, ValueError):
                vals[cat] = 0
        key = n.upper()
        if key in seen:
            # Repeated customer labels are merged once, preventing duplicate
            # TATA/BOSCH/APTIV rows while preserving their category totals.
            prev = next(x for x in out if x["B.NAME"].strip().upper() == key)
            for cat in vals:
                prev[cat] += vals[cat]
            prev["TOTAL"] = sum(prev[c] for c in vals)
        else:
            vals["B.NAME"] = n
            vals["B.CODE"] = ""
            vals["TOTAL"] = sum(vals[c] for c in ("PAID","TOPAY","PENDING_BILLING","POD_NOT_BILLED","DDR_PENDING"))
            out.append(vals)
            seen.add(key)
    return out

def parse_summary_pivot_sheet(path: str, sheet_name: str) -> list:
    """
    Kuch companies 'SUMMARY' sheet mein pehle se hi branch-wise (kabhi
    customer-wise bhi) MONTH-BY-MONTH pivot table de dete hain, jaise:
        <title row: '...PENDING BILLING REPORT...(ADR MADE BUT NOT BILLED)'>
        B.CODE | BRANCH NAME | APRIL-25 | ... | TOTAL
        801    | TATA MOTORS | 0 ...    | 3
        801    | KOLKATA     | 0 ...    | 2337
        ...
        TOTAL  |             | ...      | 2444
        <blank>
        <title row: '...(POD ON NET MADE BUT NOT BILLED)'>
        ...
    Is function ek hi sheet mein aise SAB pivot-blocks dhundh kar list of
    dicts deta hai:
        {"branch_code": 801, "branch_name": "TATA MOTORS",
         "category": "PENDING_BILLING", "total": 3}
    Category har block ke apne TITLE text se detect hoti hai
    (_match_block_title_category reuse karta hai -- 'ADR'/'NET' short aur
    poora phrase, dono support karta hai). Column position FIXED nahi
    hai -- header text se dhundhta hai ('B.CODE'/'BRANCH NAME'/'TOTAL' jahan
    bhi ho), months ki ginti/naam se koi fark nahi padta (dynamic).
    Agar sheet mein aisa pattern na mile, khaali list deta hai.
    """
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet_name]
    all_rows = list(ws.iter_rows(values_only=True))
    n = len(all_rows)

    def norm(v):
        return str(v).strip().upper() if v is not None else ""

    entries = []
    i = 0
    current_cat = None
    while i < n:
        row = all_rows[i]

        code_idx = name_idx = total_idx = None
        for j, v in enumerate(row):
            nv = norm(v)
            if nv in ("B.CODE", "B CODE", "BRANCH CODE", "BCODE"):
                code_idx = j
            elif nv in ("BRANCH NAME", "B.NAME", "BNAME"):
                name_idx = j
            elif nv == "TOTAL":
                total_idx = j
        is_header_row = code_idx is not None and name_idx is not None and total_idx is not None

        if not is_header_row:
            # Sirf NON-header rows ko category-title ke liye scan karte hain.
            # (Header row mein khud bhi ek cell 'PAID' jaisa naam ho sakta
            # hai jo galti se category-match kar jaata -- isliye header row
            # ko yahan kabhi scan nahi karte, spec item 28 'do not blindly
            # guess' ke mutabik.)
            for v in row:
                if v is None:
                    continue
                text = str(v)
                paren_match = re.search(r"\(([^)]+)\)\s*$", text.strip())
                cat = _match_block_title_category(paren_match.group(1)) if paren_match else None
                if cat is None:
                    cat = _match_block_title_category(text)
                if cat is not None:
                    current_cat = cat
                    break
            i += 1
            continue

        if current_cat is None:
            # Header row mil gaya (B.CODE+BRANCH NAME+TOTAL), lekin isse
            # PEHLE koi category-title wali row nahi mili -- matlab yeh
            # asli MONTH-BY-MONTH pivot nahi hai, sirf ek generic flat
            # summary table hai (jaise humara apna already-built SUMMARY
            # output). Isko pivot ki tarah treat NAHI karte.
            i += 1
            continue

        if code_idx is not None and name_idx is not None and total_idx is not None and current_cat is not None:
            k = i + 1
            while k < n:
                r = all_rows[k]
                first_meaningful = next((v for v in r if v is not None), None)
                if first_meaningful is not None and norm(first_meaningful).startswith("TOTAL"):
                    k += 1
                    break
                if all(v is None for v in r):
                    k += 1
                    continue
                code_v = r[code_idx] if code_idx < len(r) else None
                name_v = r[name_idx] if name_idx < len(r) else None
                total_v = r[total_idx] if total_idx < len(r) else None
                branch_code = None
                if code_v is not None:
                    try:
                        branch_code = int(float(str(code_v).strip()))
                    except (TypeError, ValueError):
                        branch_code = None
                try:
                    total = int(float(str(total_v).strip())) if total_v is not None else 0
                except (TypeError, ValueError):
                    total = 0
                name = str(name_v).strip() if name_v is not None else ""
                if name:
                    entries.append({"branch_code": branch_code, "branch_name": name,
                                     "category": current_cat, "total": total})
                k += 1
            i = k
            continue
        i += 1
    return entries


def find_header_row(ws) -> int:
    """Excel sheet ke upar ke rows mein title/branch-code likha hota hai,
    asli column-header row dhundhte hain (jaha 'S.NO' ya 'CN_NO' mile)."""
    for r in range(1, MAX_HEADER_SCAN_ROWS + 1):
        try:
            row_vals = next(ws.iter_rows(min_row=r, max_row=r, values_only=True))
        except StopIteration:
            break
        row_str = [str(v).strip().upper() for v in row_vals if v is not None]
        if any(any(mk in v for mk in HEADER_MARKER_KEYWORDS) for v in row_str):
            return r
    return 1  # fallback


_HIDDEN_CHARS = ["\u200b", "\u200c", "\u200d", "\ufeff"]  # zero-width space/joiner/BOM


def _strip_hidden(v):
    """Normal .strip() NBSP (\xa0) hata deta hai, par zero-width/BOM jaise
    invisible characters nahi -- yeh un dono ko explicitly hata kar text
    ko exactly wahi banata hai jo Excel mein dikhta hai."""
    s = str(v)
    for ch in _HIDDEN_CHARS:
        s = s.replace(ch, "")
    return s.strip()


def _drop_structural_rows(df: pd.DataFrame):
    """REAL BUG (2026-10-10, BANGALORE export): ek sheet mein har branch-block
    ke upar 'BRANCH: 502 - JIGNI' marker-row aur uske neeche column-header ki
    REPEATED row hoti hai. Flat parsing mein yeh dono asli data ban jaati
    thi -- header-row ki value 'ADR BRANCH' / 'BILL_BRANCH_CODE' ek naya
    'branch' ban kar summary mein 13 / 12 galat pending jod deti thi.
    Yahan (1) repeated header rows (jin mein kam se kam 3 cells apne hi
    column-naam ke barabar hon aur aadhe se zyada non-empty cells match karein)
    aur (2) 'BRANCH: <code> - <naam>' marker rows (jin mein baaki cells khaali
    hon) hata di jaati hain. Asli docket rows ko kabhi nahi chhuti -- koi
    asli CN-row apne har column-naam ke barabar nahi hoti.
    Returns (clean_df, number_of_rows_dropped)."""
    if df is None or df.empty:
        return df, 0
    vals = df.astype(str).apply(lambda col: col.str.strip().str.upper())
    cols_up = pd.Series([str(c).strip().upper() for c in df.columns], index=df.columns)
    eq_cnt = vals.eq(cols_up, axis=1).sum(axis=1)
    nonempty = vals.ne("").sum(axis=1)
    header_like = (eq_cnt >= 3) & (eq_cnt * 2 >= nonempty)
    first = vals.iloc[:, 0]
    marker_like = first.str.match(r"^BRANCH\s*[:\-]") & (nonempty <= 2)
    drop = header_like | marker_like
    return df[~drop], int(drop.sum())


def load_sheet_as_df(path: str, sheet_name: str) -> pd.DataFrame:
    """Ek sheet ko sahi header row ke saath DataFrame mein load karta hai.

    dtype=str + keep_default_na=False, na_values=[]: pandas khud se kisi
    column ko NUMBER/DATE mein convert nahi karta, isliye:
      - CN number kabhi '...0' (float) ya scientific notation nahi banta
      - branch code jaisa '0501' apna leading zero nahi khota
        (agar source Excel mein khud NUMBER format mein 501 save hai, to
        zero file banate waqt hi kho chuka tha -- koi library use wapas
        nahi la sakti, yeh sirf real TEXT cells ke liye kaam karta hai)
      - literal text "NA"/"NULL"/"N/A" (agar woh kisi REMARKS/STATUS
        column ki asli value hai) silently blank mein nahi badalta
    Zero-width/BOM jaise invisible characters bhi yahin hata diye jaate
    hain taaki aage ka .strip()/.upper() match hamesha sahi chale.
    """
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet_name]
    hdr_row = find_header_row(ws)
    df = pd.read_excel(path, sheet_name=sheet_name, header=hdr_row - 1,
                       dtype=str, keep_default_na=False, na_values=[])
    # duplicate/blank column names hata do (excel mein kabhi khaali columns hote hain)
    df = df.loc[:, ~df.columns.astype(str).str.contains("^Unnamed", na=False)]
    df.columns = dedupe_columns([_strip_hidden(c) for c in df.columns])
    df = df.map(_strip_hidden) if hasattr(df, "map") else df.applymap(_strip_hidden)
    # pura-khaali rows hata do (blank ab "" hai, NaN nahi)
    df = df[~(df.eq("").all(axis=1))]
    df, n_struct = _drop_structural_rows(df)
    df = df.reset_index(drop=True)
    df.attrs["structural_rows_dropped"] = n_struct
    return df


def _looks_like_ddr_sheet(path: str, sheet_name: str) -> bool:
    """Generic/unrecognized sheet (jaise sirf 'DATA' naam ki, jo kayi real
    Gurgaon DDR exports mein milti hai) DDR data hai ya nahi -- CONTENT dekh
    kar pata karte hain (DLY_CHALLAN_FROM column dhundh kar), kyunki sheet
    ke naam se DDR ka pata nahi chal sakta."""
    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        ws = wb[sheet_name]
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= 20:
                break
            vals = {str(v).strip().upper() for v in row if v is not None}
            if "DLY_CHALLAN_FROM" in vals:
                return True
    except Exception:
        return False
    return False


def load_workbook_categorized(path: str) -> dict:
    """
    Ek uploaded .xlsx file ko poori tarah padh kar category-wise DataFrames
    ka dict return karta hai:
        { "PAID": df, "TOPAY": df, "PENDING_BILLING": df, ... ,
          "OTHER_BLOCKS": {sheet_name: [block_dict, ...], ...} }
    "OTHER" (jaise 'VE COMMERCIAL VEHICLES LTD') sheets flat table nahi
    hoti, multi-block hoti hain -> parse_multiblock_sheet() se parse hoti hain.
    """
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    result = {}
    other_blocks = {}
    excluded_blocks = {}
    other_blocks_data = {}
    excluded_blocks_data = {}
    for sheet_name in wb.sheetnames:
        cat = categorize_sheet(sheet_name)
        if cat == "DATA":
            # 'DATA' jaisa generic naam sheet-naam se kuch nahi batata --
            # content check karte hain ki yeh asal mein DDR data hai ya nahi.
            try:
                if _looks_like_ddr_sheet(path, sheet_name):
                    cat = "DDR_PENDING"
            except Exception:
                pass
        if cat in ("OTHER", "EXCLUDED"):
            try:
                blocks, block_dfs = parse_multiblock_sheet(path, sheet_name, include_data=True)
            except Exception as e:
                blocks, block_dfs = [{"error": str(e)}], []
            if blocks:
                (excluded_blocks if cat == "EXCLUDED" else other_blocks)[sheet_name] = blocks

            # Flat customer tabs (BOSCH LTD / APTIV / TATA etc.) are business
            # data too.  parse_multiblock_sheet() returning no blocks is the
            # signal that this is a normal flat sheet.  Keep it separately so
            # customer totals can be reproduced in SUMMARY.
            if cat == "OTHER" and not block_dfs:
                try:
                    customer_df = load_sheet_as_df(path, sheet_name)
                except Exception:
                    customer_df = pd.DataFrame()
                if not customer_df.empty:
                    result.setdefault("CUSTOMER_SHEETS", {})
                    result["CUSTOMER_SHEETS"][sheet_name] = customer_df
            if block_dfs:
                # Per-BLOCK split (na ki poore sheet par ek category) -- ek
                # customer/region sheet ('OTHER') ke ANDAR agar ek block
                # title 'DISPUTE MATTER'/'OTHER END' nikla (ab
                # _match_block_title_category EXCLUDED return karta hai),
                # to SIRF wahi block excluded_blocks_data mein jaata hai,
                # baaki blocks (ADR/NET/etc.) hamesha ki tarah
                # other_blocks_data mein -- varna ek dispute-block ki wajah
                # se poori sheet ka data galti se excluded/customer-total se
                # bahar/andar chala jaata.
                own_excluded = [d for b, d in zip(blocks, block_dfs)
                                 if b.get("category") == "EXCLUDED" and not d.empty]
                own_rest = [d for b, d in zip(blocks, block_dfs)
                            if b.get("category") != "EXCLUDED" and not d.empty]
                if cat == "EXCLUDED":
                    # poora sheet hi EXCLUDED-named hai -- sab kuch yahin
                    all_data = own_excluded + own_rest
                    if all_data:
                        excluded_blocks_data[sheet_name] = pd.concat(all_data, ignore_index=True)
                else:
                    if own_rest:
                        other_blocks_data[sheet_name] = pd.concat(own_rest, ignore_index=True)
                    if own_excluded:
                        prev = excluded_blocks_data.get(sheet_name)
                        new_part = pd.concat(own_excluded, ignore_index=True)
                        excluded_blocks_data[sheet_name] = (
                            pd.concat([prev, new_part], ignore_index=True) if prev is not None else new_part)
            continue
        if cat == "SUMMARY":
            # Kuch files 'SUMMARY' sheet mein pehle se branch/customer-wise
            # month-by-month pivot dete hain -- agar aisa pattern mile, usi
            # ko authoritative maan kar alag se store karte hain
            # (SUMMARY_PIVOT); yeh purani flat load_sheet_as_df() se bilkul
            # ALAG hai (jo pivot detect nahi kar sakti).
            try:
                customer_rows = parse_flat_summary_customer_rows(path, sheet_name)
            except Exception:
                customer_rows = []
            if customer_rows:
                result.setdefault("SUMMARY_CUSTOMERS", [])
                result["SUMMARY_CUSTOMERS"].extend(customer_rows)
            try:
                pivot_entries = parse_summary_pivot_sheet(path, sheet_name)
            except Exception:
                pivot_entries = []
            if pivot_entries:
                result.setdefault("SUMMARY_PIVOT", [])
                result["SUMMARY_PIVOT"].extend(pivot_entries)
                continue
            # pivot pattern nahi mila -> neeche generic 'SUMMARY' handling
            # (reconcile_with_pasted_summary validator mein) par chhod dete hain
        if cat == "DDR_PENDING":
            try:
                sect_df, excluded = parse_ddr_sections(path, sheet_name)
            except Exception:
                sect_df, excluded = pd.DataFrame(), []
            if not sect_df.empty:
                if "DDR_PENDING" in result:
                    result["DDR_PENDING"] = pd.concat([result["DDR_PENDING"], sect_df], ignore_index=True)
                else:
                    result["DDR_PENDING"] = sect_df
                if excluded:
                    result.setdefault("DDR_EXCLUDED", [])
                    for e in excluded:
                        e = dict(e)
                        e["sheet"] = sheet_name
                        e["source_file"] = path
                        result["DDR_EXCLUDED"].append(e)
                continue
            # section-structure match nahi hui -> neeche purane flat-parsing
            # (DLY_CHALLAN decode) path par fallback ho jayega
        if cat in ("PENDING_BILLING", "POD_NOT_BILLED"):
            # CONFIRMED (golden reference file se): yeh sheets bhi section-
            # based hoti hain (har branch ka apna '<code>|<title>' block),
            # bilkul DDR jaisa -- flat 'ADR BRANCH' column pe bharosa nahi
            # kar sakte (woh column kai files mein hota hi nahi / alag naam
            # se hota hai). parse_multiblock_sheet() yahan bhi reuse karte
            # hain, is baar seedha is TOP-LEVEL sheet par (na ki sirf
            # 'OTHER'/customer sheets par). include_data=True se poora
            # CN-level raw data bhi milta hai (data-sheet export ke liye).
            try:
                blocks, block_dfs = parse_multiblock_sheet(path, sheet_name, include_data=True)
            except Exception:
                blocks, block_dfs = [], []
            # Isi sheet ke andar koi block 'OTHER END'/'DISPUTE MATTER' nikla
            # ho (category == EXCLUDED) to usko summary-counting se poori
            # tarah bahar rakho, par data GAYAB nahi karte -- report mein
            # dikhane ke liye excluded_blocks_data mein save kar dete hain.
            own_excluded = [d for b, d in zip(blocks, block_dfs)
                             if b.get("category") == "EXCLUDED" and not d.empty]
            if own_excluded:
                new_part = pd.concat(own_excluded, ignore_index=True)
                prev = excluded_blocks_data.get(sheet_name)
                excluded_blocks_data[sheet_name] = (
                    pd.concat([prev, new_part], ignore_index=True) if prev is not None else new_part)
            own_pairs = [(b, d) for b, d in zip(blocks, block_dfs)
                         if b.get("category") == cat and b.get("branch_code") is not None]
            if own_pairs:
                sect_df = pd.concat([d for _, d in own_pairs], ignore_index=True)
                if cat in result:
                    result[cat] = pd.concat([result[cat], sect_df], ignore_index=True)
                else:
                    result[cat] = sect_df
                continue
            # section-structure match nahi hui (ya branch_code kahin nahi mila)
            # -> neeche purane flat column-based parsing par fallback
        try:
            df = load_sheet_as_df(path, sheet_name)
        except Exception as e:
            df = pd.DataFrame()
            df.attrs["load_error"] = str(e)
        if df.empty and not df.attrs.get("load_error"):
            continue
        if cat in result:
            # same category multiple sheets ho to concat kar do (rare)
            result[cat] = pd.concat([result[cat], df], ignore_index=True)
        else:
            result[cat] = df
    if other_blocks:
        result["OTHER_BLOCKS"] = other_blocks
    if excluded_blocks:
        result["EXCLUDED_BLOCKS"] = excluded_blocks
    if other_blocks_data:
        result["OTHER_BLOCKS_DATA"] = other_blocks_data
    if excluded_blocks_data:
        result["EXCLUDED_BLOCKS_DATA"] = excluded_blocks_data
    return result


def detect_branch_column(df: pd.DataFrame, category: str = None):
    """DataFrame mein se branch-code column dhundhta hai.

    CONFIRMED (2026-09-27, cross-checked against ground-truth 'BRANCH CODE'
    column already present in raw exports):
      - PENDING_BILLING (ADR): asli branch-of-truth column 'ADR BRANCH' hai.
        Agar yeh column export mein maujood na ho, to 'ADR_NO_DATE_BRANCH'
        jaise combo-column se (comma-separated, last token) derive karte
        hain -- kabhi BILL_BRANCH_CODE ya koi aur column guess nahi karte.
      - POD_NOT_BILLED (NET/POD): asli branch-of-truth column
        'BILL_BRANCH_CODE' hai (CUSTOMER_BILL_LOC nahi -- woh sirf ~60%
        match karta tha, jabki BILL_BRANCH_CODE ~89%).
    Dono ke liye alias-list normalize-matching use hoti hai (config.py:
    ADR_BRANCH_COLUMN_ALIASES / ADR_NO_DATE_BRANCH_COLUMN_ALIASES /
    BILL_BRANCH_CODE_COLUMN_ALIASES) -- taaki header ka spacing/underscore/
    case/hyphen thoda bhi badle, column phir bhi mil jaaye.
    Baaki categories ke liye purana behaviour (config.CATEGORY_BRANCH_COLUMN
    exact match, phir generic BRANCH_CODE_COLUMNS/BRANCH_NAME_COLUMNS
    fallback) bilkul waisa hi hai -- unhe yahan CHHEDA nahi gaya hai.
    """
    if category == "PENDING_BILLING":
        col = _find_column_by_aliases(df, ADR_BRANCH_COLUMN_ALIASES)
        if col is not None:
            return col, "code"
        combo_col = _find_column_by_aliases(df, ADR_NO_DATE_BRANCH_COLUMN_ALIASES)
        if combo_col is not None:
            derived = "_RESOLVED_ADR_BRANCH"
            if derived not in df.columns:
                df[derived] = df[combo_col].apply(_extract_branch_from_combo)
            return derived, "code"
        # na 'ADR BRANCH' mila na 'ADR_NO_DATE_BRANCH' -- caller ko pata
        # chalna chahiye (validator warning banayega), guess nahi karte.
        return None, None

    if category == "POD_NOT_BILLED":
        col = _find_column_by_aliases(df, BILL_BRANCH_CODE_COLUMN_ALIASES)
        if col is not None:
            return col, "code"
        return None, None

    cols_upper = {c.upper(): c for c in df.columns}
    if category and category in CATEGORY_BRANCH_COLUMN:
        confirmed = CATEGORY_BRANCH_COLUMN[category].upper()
        if confirmed in cols_upper:
            return cols_upper[confirmed], "code"
        # confirmed column expected but missing -> caller ko pata chalna
        # chahiye (validator isko warning banayega), None hi return karo
        return None, None
    for cand in BRANCH_CODE_COLUMNS:
        if cand.upper() in cols_upper:
            return cols_upper[cand.upper()], "code"
    for cand in BRANCH_NAME_COLUMNS:
        if cand.upper() in cols_upper:
            return cols_upper[cand.upper()], "name"
    return None, None


def _match_keywords_only(t: str):
    """Substring-keyword match against OTHER_SHEET_BLOCK_TITLE_RULES only
    (no exact-alias check) -- used internally so multi-line titles can be
    tried segment-by-segment before falling back to the full text."""
    for category, keywords in OTHER_SHEET_BLOCK_TITLE_RULES:
        for kw in keywords:
            if kw in t:
                return category
    return None


def _match_block_title_category(title: str):
    if not title:
        return None
    t = str(title).strip().lower()
    # CONFIRMED by user (2026-09-29): 'OTHER END' / 'DISPUTE MATTER' jaisa
    # block-title mila to EXCLUDED category return karo -- yeh sabse PEHLE
    # check hota hai (category-keyword rules se bhi pehle), taaki aisa block
    # bhool se PENDING_BILLING/POD_NOT_BILLED mein na gin liya jaaye, aur
    # (zyaada zaroori) taaki yeh row ek PEHCHANA-GAYA block-marker bane --
    # warna _detect_block_marker() ise marker hi nahi maanta, aur uske neeche
    # ki saari rows chupke se PICHLE (galat) block mein jud kar uska count
    # badha deti hain. Isi EXCLUDED_SHEET_KEYWORDS list ko sheet-level
    # exclusion (categorize_sheet) bhi use karta hai -- ek hi jagah se dono
    # control hote hain.
    for kw in EXCLUDED_SHEET_KEYWORDS:
        if kw in t:
            return "EXCLUDED"
    # FIX: kuch files ka title generic report-header boilerplate + asli
    # category-line ek saath rakhte hain, jaise:
    #   "SCM PITHAMPUR PENDING BILLING SUMMARY TILL-31-JULY-2026
    #    POD ON NET BUT NOT BILLED"
    # Yahan boilerplate mein hi "PENDING BILLING" shabd aa jaata hai, jo
    # poore text par match karne se HAMESHA PENDING_BILLING de deta tha --
    # asli suffix line ("POD ON NET BUT NOT BILLED") ko kabhi mauka hi
    # nahi milta tha. Isliye jab title multi-line ho, sabse LAST line
    # (asli category-suffix) ko PEHLE try karo; single-line titles ka
    # behaviour bilkul waisa hi rehta hai jaisa pehle tha.
    if "\n" in t:
        last_line = t.rsplit("\n", 1)[-1].strip()
        cat = _match_keywords_only(last_line)
        if cat is not None:
            return cat
    cat = _match_keywords_only(t)
    if cat is not None:
        return cat
    # chhote alias ('ADR', 'NET') -- EXACT match, substring nahi (safety)
    for category, aliases in OTHER_SHEET_BLOCK_TITLE_EXACT_ALIASES:
        for alias in aliases:
            if t == alias.strip().lower():
                return category
    # family-rule: 'NET POD', 'POD NET', 'NETPOD', 'NET (POD)' etc. (config)
    return _family_category(t)


def _detect_block_marker(row):
    """Ek row 'block marker' hai ya nahi check karta hai. Do patterns support
    karta hai (dono golden reference files mein mile):
      1. <branch_code> | <TITLE>   (jaise APTIV, VE COMMERCIAL)
      2. <TITLE>                   (jaise BOSCH, TATA MOTORS -- inka koi
         fixed branch nahi hota, isliye sirf title cell hoti hai, code nahi)
    Return: (branch_code:int|None, title:str, category:str) agar row mein
    koi cell OTHER_SHEET_BLOCK_TITLE_RULES se match kare, warna None.
    """
    non_null = [(idx, v) for idx, v in enumerate(row) if v is not None]
    if not non_null or len(non_null) > 3:
        return None
    branch_code, title, cat = None, None, None
    for idx, v in non_null:
        maybe_cat = _match_block_title_category(str(v))
        if maybe_cat is not None:
            title, cat = str(v).strip(), maybe_cat
            continue
        try:
            branch_code = int(float(str(v).strip()))
        except (TypeError, ValueError):
            pass
    if cat is None:
        return None
    return branch_code, title, cat


def parse_multiblock_sheet(path: str, sheet_name: str, include_data: bool = False):
    """
    Client-specific sheets (jaise 'VE COMMERCIAL VEHICLES LTD', 'BOSCH ',
    'APTIV') ek hi sheet mein kai chhote reports stack karke rakhti hain:
        [<branch_code>] | <TITLE>          <- block marker row (code optional --
        <blank>                               BOSCH/TATA jaisi customer sheets
        S.NO | CN_NO | ... (data header)      mein sirf TITLE hoti hai, code nahi)
        1 | ...
        2 | ...
        <blank / next marker>
    Yeh function har block dhundh kar list of dicts deta hai:
        {"branch_code": 2601 ya None, "title": "ADR MADE BUT NOT BILLED",
         "category": "PENDING_BILLING", "row_count": 16}
    include_data=True hone par, blocks list ke saath-saath har block ka
    poora raw row-level DataFrame bhi (list mein, blocks ke saath 1:1 index
    match) return hota hai (column '_SECTION_BRANCH' = branch_code,
    '_BLOCK_TITLE' = title) -- data-sheet export/audit ke liye.
    Return: blocks (include_data=False) ya (blocks, block_dfs) (True).
    """
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet_name]
    all_rows = list(ws.iter_rows(values_only=True))

    blocks = []
    block_dfs = [] if include_data else None
    i = 0
    n = len(all_rows)
    while i < n:
        marker = _detect_block_marker(all_rows[i])
        if marker is not None:
            branch_code, title, cat = marker
            # header row dhundo (agla non-blank row jisme 'S.NO'/'CN_NO' ho)
            header_i = None
            for j in range(i + 1, min(i + 6, n)):
                vals = [str(v).strip().upper() for v in all_rows[j] if v is not None]
                if any(mk in vals for mk in HEADER_MARKER_KEYWORDS):
                    header_i = j
                    break
            if header_i is None:
                i += 1
                continue
            # data rows count karo jab tak 2 consecutive blank rows ya
            # naya marker row na mile
            data_count = 0
            data_rows = [] if include_data else None
            k = header_i + 1
            while k < n:
                r = all_rows[k]
                if all(v is None for v in r):
                    # khaali row -- sirf skip karo, block khatam mat maano
                    # (spec item 8: sub-heading se pehle/baad blank rows aa
                    # sakti hain, yeh naya block nahi hota)
                    k += 1
                    continue
                if _detect_block_marker(r) is not None:
                    break
                non_null_r = [v for v in r if v is not None]
                if len(non_null_r) <= 1:
                    # akeli sub-heading jaise 'CODE ISSUE'/'PROCESSING
                    # BILLING'/'STAMP ISSUE' -- SAME branch/category ka
                    # hissa hai (spec item 8), naya block nahi -- skip karo.
                    k += 1
                    continue
                data_count += 1
                if include_data:
                    data_rows.append(r)
                k += 1
            blocks.append({
                "branch_code": branch_code,
                "title": title,
                "category": cat,
                "row_count": data_count,
            })
            if include_data:
                # ws.max_column kabhi-kabhi asli data se bahut zyada hota hai
                # (formatting artifact) -- sirf LAST non-None header cell tak
                # hi trim karte hain, warna hazaaron khaali 'COLn' columns
                # ban jaate hain.
                header_row_vals = all_rows[header_i]
                last_real = max((idx for idx, v in enumerate(header_row_vals) if v is not None), default=-1)
                trimmed_len = last_real + 1
                header_full = [str(v).strip() if v is not None else f"COL{ci}"
                                for ci, v in enumerate(header_row_vals[:trimmed_len])]
                trimmed_data_rows = [r[:trimmed_len] for r in data_rows]
                bdf = pd.DataFrame(trimmed_data_rows, columns=dedupe_columns(header_full))
                bdf["_SECTION_BRANCH"] = branch_code
                bdf["_BLOCK_TITLE"] = title
                block_dfs.append(bdf)
            i = k
            continue
        i += 1
    if include_data:
        return blocks, block_dfs
    return blocks


def merge_all_uploaded(paths: list) -> dict:
    """Multiple uploaded files (jaise ek hi region ke alag-alag exports) ko
    ek hi categorized dict mein merge kar deta hai."""
    merged = {}
    for p in paths:
        cat_data = load_workbook_categorized(p)
        for cat, val in cat_data.items():
            if cat == "OTHER_BLOCKS":
                merged.setdefault("OTHER_BLOCKS", {})
                merged["OTHER_BLOCKS"].update(val)
                continue
            if cat in ("OTHER_BLOCKS_DATA", "EXCLUDED_BLOCKS_DATA"):
                merged.setdefault(cat, {})
                merged[cat].update(val)
                continue
            if cat == "EXCLUDED_BLOCKS":
                merged.setdefault("EXCLUDED_BLOCKS", {})
                merged["EXCLUDED_BLOCKS"].update(val)
                continue
            if cat == "DDR_EXCLUDED":
                merged.setdefault("DDR_EXCLUDED", [])
                merged["DDR_EXCLUDED"].extend(val)
                continue
            if cat == "SUMMARY_PIVOT":
                merged.setdefault("SUMMARY_PIVOT", [])
                merged["SUMMARY_PIVOT"].extend(val)
                continue
            if cat == "SUMMARY_CUSTOMERS":
                merged.setdefault("SUMMARY_CUSTOMERS", [])
                merged["SUMMARY_CUSTOMERS"].extend(val)
                continue
            if cat == "CUSTOMER_SHEETS":
                merged.setdefault("CUSTOMER_SHEETS", {})
                for sheet_name, df in (val or {}).items():
                    # Merge customer tabs with the same logical name across
                    # uploaded files instead of silently overwriting one.
                    key = re.sub(r"\s+", " ", str(sheet_name).strip()).upper()
                    existing_name = next((k for k in merged["CUSTOMER_SHEETS"]
                                          if re.sub(r"\s+", " ", str(k).strip()).upper() == key), None)
                    if existing_name is None:
                        merged["CUSTOMER_SHEETS"][sheet_name] = df.copy()
                    else:
                        merged["CUSTOMER_SHEETS"][existing_name] = pd.concat(
                            [merged["CUSTOMER_SHEETS"][existing_name], df], ignore_index=True
                        ).drop_duplicates().reset_index(drop=True)
                continue
            if cat in merged:
                merged[cat] = pd.concat([merged[cat], val], ignore_index=True)
            else:
                merged[cat] = val
    return merged


def load_branch_master(path: str) -> dict:
    """Branch-master file (simple 2-column: 'B.CODE' + 'BRANCH NAME' jaisi)
    padh kar {code:int -> name:str} dict deta hai. Header row auto-detect
    hota hai (top 5 rows mein dhundta hai), column-naam alias-tolerant hai
    (B.CODE/B CODE/BRANCH CODE/CODE, BRANCH NAME/B.NAME/NAME/BRANCH).
    Yeh directory_loader.load_directory() se ALAG hai -- woh multi-sheet
    employee-directory (ANUJ jaisi) ke liye hai, yeh sirf seedhi
    code-to-name lookup file ke liye hai."""
    code_aliases = {"B.CODE", "B_CODE", "BCODE", "BRANCH CODE", "CODE"}
    name_aliases = {"BRANCH NAME", "B.NAME", "B_NAME", "BNAME", "NAME", "BRANCH"}

    try:
        from .directory_loader import _ensure_xlsx
        path = _ensure_xlsx(path)
    except Exception:
        pass
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheet = wb.sheetnames[0]
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))

    header_i, code_idx, name_idx = None, None, None
    for i, r in enumerate(rows[:5]):
        for j, v in enumerate(r):
            if v is None:
                continue
            norm = str(v).strip().upper()
            if norm in code_aliases:
                code_idx = j
            elif norm in name_aliases:
                name_idx = j
        if code_idx is not None and name_idx is not None:
            header_i = i
            break

    result = {}
    if header_i is None:
        return result
    for r in rows[header_i + 1:]:
        if code_idx >= len(r) or name_idx >= len(r):
            continue
        code_v, name_v = r[code_idx], r[name_idx]
        if code_v is None or name_v is None:
            continue
        try:
            code = int(float(str(code_v).strip()))
        except (TypeError, ValueError):
            continue
        name = str(name_v).strip()
        if name:
            result[code] = name
    return result
