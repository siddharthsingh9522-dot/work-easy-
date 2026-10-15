# -*- coding: utf-8 -*-
"""
report_builder.py
Categorized raw data (PAID/TOPAY/PENDING_BILLING/POD_NOT_BILLED/DDR_PENDING)
se aapke sample jaisi branch-wise SUMMARY table banata hai:

S.NO | B.NAME | B.CODE | PAID | TO PAY | PENDING BILLING | POD ON NET BUT
NOT BILLED | DDR PENDING | TOTAL

IMPORTANT: Maine sample file ke numbers reverse-engineer karke check kiye —
raw sheets ke 'BILL_BRANCH_CODE' / 'DESTINATION_BRANCH' jaise columns se
exact match nahi hue (matlab original SUMMARY kisi doosre pivot/system se
banti thi). Yeh module apna best-effort count deta hai (branch-code column
agar mile to wahi use karta hai, warna branch-name match). Har baar
validator.reconcile_with_pasted_summary() se apni purani SUMMARY sheet se
compare zaroor karo jab tak business rule 100% confirm na ho jaye.
"""
import re
import pandas as pd

from .config import COUNTABLE_CATEGORIES
from .ingest import detect_branch_column, resolve_ddr_branch

CATEGORY_DISPLAY_NAMES = {
    "PAID": "PAID",
    "TOPAY": "TO PAY",
    "PENDING_BILLING": "PENDING BILLING",
    "POD_NOT_BILLED": "POD ON NET BUT NOT BILLED",
    "DDR_PENDING": "DDR PENDING",
}


def count_ddr_by_branch(df: pd.DataFrame):
    """DDR branch count with one canonical numeric branch-code key.

    IMPORTANT: Excel/pivot kabhi 801 ko number aur "801" ko text samajh kar
    do buckets bana deta hai. Yahan dono ko int(801) mein normalize karke
    ek hi bucket banaya jata hai. DLY_CHALLAN_FROM > threshold hone par
    resolve_ddr_branch() se DLY_CHALLAN decode hota hai.
    """
    if df is None or df.empty:
        return pd.Series(dtype=int), 0, 0

    if "_SECTION_BRANCH" in df.columns:
        codes = pd.to_numeric(df["_SECTION_BRANCH"], errors="coerce")
        return codes.dropna().astype(int).value_counts(), 0, 0

    cols_upper = {str(c).strip().upper(): c for c in df.columns}
    from_col = cols_upper.get("DLY_CHALLAN_FROM")
    if from_col is None:
        # Kuch exports B.CODE dete hain. DDR mein bhi normalize karke use karo.
        from_col = cols_upper.get("B. CODE") or cols_upper.get("B.CODE")
    if from_col is None:
        return pd.Series(dtype=int), 0, 0
    challan_col = cols_upper.get("DLY_CHALLAN")

    branches, unresolved, decoded = [], 0, 0
    for _, row in df.iterrows():
        from_val = row.get(from_col)
        if pd.isna(from_val):
            continue
        challan_val = row.get(challan_col) if challan_col else None
        branch, status = resolve_ddr_branch(from_val, challan_val)
        if branch is None:
            unresolved += 1
            continue
        if status == "decoded":
            decoded += 1
        branches.append(int(branch))

    counts = pd.Series(branches, dtype="int64").value_counts() if branches else pd.Series(dtype=int)
    counts.index = counts.index.astype(int)
    return counts, unresolved, decoded


def ddr_audit_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Return raw Column-R counts alongside resolved branch counts.

    This makes cases like 9998 -> 811 visible instead of silently hiding the
    transformation. Raw source totals and resolved totals must have the same
    row total when no rows are unresolved.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["RAW DLY_CHALLAN_FROM", "RAW COUNT",
                                     "RESOLVED BRANCH", "RESOLVED COUNT", "METHOD"])
    cols = {str(c).strip().upper(): c for c in df.columns}
    from_col = cols.get("DLY_CHALLAN_FROM")
    challan_col = cols.get("DLY_CHALLAN")
    rows = []
    raw = df[from_col].dropna().astype(str).str.strip().value_counts() if from_col else pd.Series(dtype=int)
    for raw_code, n in raw.items():
        sample = df[df[from_col].astype(str).str.strip() == raw_code] if from_col else pd.DataFrame()
        resolved = []
        methods = []
        for _, r in sample.iterrows():
            b, method = resolve_ddr_branch(r.get(from_col), r.get(challan_col) if challan_col else None)
            resolved.append(b if b is not None else "")
            methods.append(method)
        if len(set(resolved)) == 1:
            rb = resolved[0]
            rc = len(resolved) if rb != "" else 0
            method = methods[0] if methods and len(set(methods)) == 1 else "mixed"
        else:
            rb = ", ".join(str(x) for x in sorted(set(x for x in resolved if x != "")))
            rc = sum(x != "" for x in resolved)
            method = "mixed"
        rows.append({"RAW DLY_CHALLAN_FROM": raw_code, "RAW COUNT": int(n),
                     "RESOLVED BRANCH": rb, "RESOLVED COUNT": int(rc), "METHOD": method})
    return pd.DataFrame(rows)

def count_by_branch(df: pd.DataFrame, category: str, branch_name_map: dict = None) -> pd.Series:
    """Ek category ki DataFrame lekar branch-code -> row-count Series
    deta hai, CONFIRMED column (config.CATEGORY_BRANCH_COLUMN) use karke.
    NOTE: branch codes yaha TEXT/NUMBER dono ho sakte hain (Excel data-entry
    inconsistency) -- isliye hamesha numeric mein normalize karte hain taaki
    '2605' (text) aur 2605 (number) do alag rows na ban jayein (yeh hi bug
    tha jo aapke DDR PENDING pivot mein duplicate rows dikha raha tha).

    DDR_PENDING ke liye yeh count_ddr_by_branch() ko delegate karta hai
    (DLY_CHALLAN_FROM > threshold wala decode-rule handle karne ke liye)."""
    if df is None or df.empty:
        return pd.Series(dtype=int)
    if "_SECTION_BRANCH" in df.columns:
        # section-parsed data (DDR/PENDING_BILLING/POD_NOT_BILLED, jab sheet
        # golden-reference jaisi section-based nikli) -- yeh already
        # branch-of-truth hai, seedha count karo.
        return pd.to_numeric(df["_SECTION_BRANCH"], errors="coerce").dropna().astype(int).value_counts()
    if category == "DDR_PENDING":
        counts, _unresolved, _decoded = count_ddr_by_branch(df)
        return counts
    col, kind = detect_branch_column(df, category)
    if col is None:
        return pd.Series(dtype=int)
    if kind == "code":
        codes = pd.to_numeric(df[col], errors="coerce")
        return codes.dropna().astype(int).value_counts()
    if not branch_name_map:
        return pd.Series(dtype=int)
    name_to_code = {str(v).strip().upper(): k for k, v in branch_name_map.items() if pd.notna(v)}
    names = df[col].astype(str).str.strip().str.upper()
    codes = names.map(name_to_code)
    return codes.dropna().astype(int).value_counts()


def _dedupe_category_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Remove repeated source rows without changing legitimate category separation.

    Primary key is CN_NO/CN NO when available.  If no docket column exists,
    only exact duplicate rows are removed.  This prevents the same pending
    docket being counted twice when an export contains repeated blocks/files.
    """
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df.copy()
    out = df.copy()
    cn = next((c for c in out.columns
               if str(c).strip().upper() in ("CN_NO", "CN NO", "CN_NO.")), None)
    if cn:
        key = out[cn].astype(str).str.strip()
        nonblank = key.ne("") & key.ne("NAN") & key.ne("NONE")
        if nonblank.any():
            keep_idx = out.loc[nonblank].drop_duplicates(subset=[cn], keep="first").index
            blank_idx = out.loc[~nonblank].drop_duplicates(keep="first").index
            return pd.concat([out.loc[keep_idx], out.loc[blank_idx]], axis=0).sort_index().reset_index(drop=True)
    return out.drop_duplicates().reset_index(drop=True)



_PARTY_NOISE_WORDS = {
    "LTD", "LIMITED", "LLP", "PVT", "PRIVATE", "CO", "COMPANY",
    "CORP", "CORPORATION", "INC", "INDIA",
    "MOTORS", "MOTOR", "COMMERCIAL", "VEHICLES", "VEHICLE",
    "AUTO", "AUTOMOTIVE", "AUTOMOBILES", "AUTOMOBILE",
}


def _canonical_party_key(name: str) -> str:
    """CONFIRMED by user (2026-10-07): ek hi party kabhi 'TATA MOTOR', kabhi
    'TATA MOTORS', kabhi sirf 'TATA' likhi jaati hai -- in sabko EK hi party
    maana jaana chahiye (summary mein ek hi row, export mein ek hi tab).
    Corporate suffix/noise words (LTD/MOTORS/COMMERCIAL/VEHICLES/etc.) hata
    kar core naam (jaise 'TATA', ya 'VE' VE COMMERCIAL VEHICLES LTD se)
    nikal lete hain -- yahi matching ki chaabi hai."""
    s = re.sub(r"[^A-Z0-9 ]", " ", str(name or "").strip().upper())
    tokens = [t for t in s.split() if t and t not in _PARTY_NOISE_WORDS]
    key = " ".join(tokens).strip()
    return key or re.sub(r"\s+", " ", str(name or "").strip().upper())


def _party_display_name_map(raw_names) -> dict:
    """{original_sheet_name: chosen_display_name} banata hai -- same
    canonical-key wale saare naam (TATA MOTOR / TATA MOTORS / TATA) ek
    group mein, display ke liye us group ka SABSE LAMBA/descriptive variant
    chunta hai (taaki report mein poora naam dikhe, short-form nahi).
    Yahi mapping summary-row (_customer_rows_from_other_blocks) aur
    data-tab-export (_merge_customer_blocks) dono mein use hota hai, isliye
    B.NAME aur sheet-tab-naam hamesha ek jaise rehte hain."""
    groups = {}
    for raw in raw_names:
        raw = str(raw).strip()
        if not raw:
            continue
        groups.setdefault(_canonical_party_key(raw), []).append(raw)
    out = {}
    for variants in groups.values():
        display = max(variants, key=len)
        for v in variants:
            out[v] = display
    return out


def _collect_owned_from_sheets(sheets: dict, owned: dict, customer_names: set):
    """Shared helper: scan a {sheet_name: df} dict for CN_NO + category/title,
    marking each docket as customer-owned so it is excluded from branch counts."""
    for sheet_name, df in (sheets or {}).items():
        if df is None or df.empty:
            continue
        customer_names.add(re.sub(r"\s+", " ", str(sheet_name).strip()).upper())
        customer_names.add(_canonical_party_key(sheet_name))
        cn_col = next((c for c in df.columns
                       if str(c).strip().upper() in ("CN_NO", "CN NO", "CN_NO.")), None)
        if cn_col is None:
            continue
        cat_col = next((c for c in df.columns if str(c).strip().upper() == "CATEGORY"), None)
        title_col = next((c for c in df.columns if str(c).strip().upper() in ("_BLOCK_TITLE", "BLOCK_TITLE")), None)
        for _, row in df.iterrows():
            cn = str(row.get(cn_col, "")).strip()
            if not cn or cn.upper() in ("NAN", "NONE"):
                continue
            text = ""
            if cat_col is not None:
                text = str(row.get(cat_col, "")).strip().upper()
            if not text and title_col is not None:
                text = str(row.get(title_col, "")).strip().upper()
            if "PENDING BILLING" in text or "ADR" in text:
                owned["PENDING_BILLING"].add(cn)
            elif "POD ON NET" in text or "NET" in text:
                owned["POD_NOT_BILLED"].add(cn)
            elif text in ("PAID",):
                owned["PAID"].add(cn)
            elif "TO PAY" in text or text == "TOPAY":
                owned["TOPAY"].add(cn)
            elif "DDR" in text:
                owned["DDR_PENDING"].add(cn)


def _customer_ownership(categorized_data: dict):
    """Return customer-owned docket keys by business category.

    Business rule: once a docket is present in a customer-specific sheet
    (TATA/BOSCH/APTIV/etc.), that docket belongs to that customer report and
    must not be counted again in a branch summary.  CN_NO is the safest
    ownership key; blank/unknown CNs are not guessed.

    FIX: real customer sheets (BOSCH LTD/TATA/APTIV) almost always have
    block-markers, so their row data lands in "OTHER_BLOCKS_DATA" (from
    parse_multiblock_sheet), NOT "CUSTOMER_SHEETS" (which is only for the
    rare zero-block flat case). Both sources are scanned now, otherwise a
    docket that also appears under its branch in the main detail sheet
    would get counted twice (once for the customer, once for the branch).
    """
    owned = {cat: set() for cat in COUNTABLE_CATEGORIES}
    customer_names = set()
    _collect_owned_from_sheets(categorized_data.get("CUSTOMER_SHEETS", {}), owned, customer_names)
    _collect_owned_from_sheets(categorized_data.get("OTHER_BLOCKS_DATA", {}), owned, customer_names)
    return owned, customer_names

def _remove_customer_owned_rows(df: pd.DataFrame, category: str, owned: dict) -> pd.DataFrame:
    """Remove only dockets explicitly owned by a customer sheet."""
    if df is None or df.empty or not owned.get(category):
        return df.copy() if df is not None else pd.DataFrame()
    cn_col = next((c for c in df.columns
                   if str(c).strip().upper() in ("CN_NO", "CN NO", "CN_NO.")), None)
    if cn_col is None:
        return df.copy()
    keys = df[cn_col].astype(str).str.strip()
    return df.loc[~keys.isin(owned[category])].copy().reset_index(drop=True)

def _category_count_sources(categorized_data: dict, branch_name_map: dict):
    """Choose one authoritative count source per category.

    If an input workbook already contains a month-by-month SUMMARY pivot, that
    pivot is authoritative for the categories it contains.  We do NOT add the
    same category again from its detail sheet, because that was the source of
    inflated/double pending counts.  Categories absent from the pivot use
    deduplicated detail data.
    """
    owned, customer_names = _customer_ownership(categorized_data)
    pivot = categorized_data.get("SUMMARY_PIVOT", []) or []
    pivot_counts = {}
    for e in pivot:
        cat = e.get("category")
        if cat not in COUNTABLE_CATEGORIES:
            continue
        try:
            code = int(float(str(e.get("branch_code")).strip()))
            total = int(float(str(e.get("total") or 0).strip()))
        except (TypeError, ValueError):
            continue
        # If the source pivot explicitly labels a row with a customer-sheet
        # name (e.g. 801 | TATA MOTORS), it is customer-owned and must not
        # become branch 801. The customer total is retained separately.
        pivot_name = re.sub(r"\s+", " ", str(e.get("branch_name") or "").strip()).upper()
        if pivot_name in customer_names or _canonical_party_key(pivot_name) in customer_names:
            continue
        pivot_counts.setdefault(cat, {})
        pivot_counts[cat][code] = pivot_counts[cat].get(code, 0) + total

    counts = {}
    for cat in COUNTABLE_CATEGORIES:
        if cat in pivot_counts and pivot_counts[cat]:
            counts[cat] = pd.Series(pivot_counts[cat], dtype="int64")
        else:
            cleaned = _dedupe_category_rows(categorized_data.get(cat))
            cleaned = _remove_customer_owned_rows(cleaned, cat, owned)
            # Keep the cleaned, customer-excluded frame available to downstream
            # export/chatbot so the same docket cannot leak back into a branch
            # count later in the pipeline.
            if cleaned is not None and not cleaned.empty:
                categorized_data[cat] = cleaned
            counts[cat] = count_by_branch(cleaned, cat, branch_name_map)
    return counts



def _customer_rows_from_customer_sheets(categorized_data: dict) -> list:
    """Create customer SUMMARY rows from flat customer detail tabs.

    The reference report includes customer totals (BOSCH LTD/APTIV/TATA)
    even though these rows have no branch code. CATEGORY in those tabs tells
    whether each docket belongs to ADR/Pending Billing or NET/POD.
    """
    result = []
    for sheet_name, df in (categorized_data.get("CUSTOMER_SHEETS", {}) or {}).items():
        if df is None or df.empty:
            continue
        name = str(sheet_name).strip()
        if not name:
            continue
        row = {"B.NAME": name, "B.CODE": "", "PAID": 0, "TOPAY": 0,
               "PENDING_BILLING": 0, "POD_NOT_BILLED": 0, "DDR_PENDING": 0}
        cat_col = next((c for c in df.columns if str(c).strip().upper() == "CATEGORY"), None)
        if cat_col is None:
            continue
        for value in df[cat_col].dropna():
            text = str(value).strip().upper()
            if "PENDING BILLING" in text or "ADR" in text:
                row["PENDING_BILLING"] += 1
            elif "POD ON NET" in text or "NET" in text:
                row["POD_NOT_BILLED"] += 1
        row["TOTAL"] = sum(row[c] for c in ("PAID","TOPAY","PENDING_BILLING","POD_NOT_BILLED","DDR_PENDING"))
        if row["TOTAL"] > 0:
            result.append(row)
    return result


def _customer_rows_from_other_blocks(categorized_data: dict) -> list:
    """OTHER_BLOCKS se customer SUMMARY rows banata hai.

    FIX (Gurgaon BOSCH LTD/APTIV/TATA missing-from-SUMMARY bug): jab ek
    'OTHER'-category sheet (jaise BOSCH LTD, TATA, APTIV) ke andar
    branch-code+title YA sirf title-only block markers milte hain, to
    parse_multiblock_sheet() unhe already blocks ke roop mein nikaal leta
    hai -- is data ko "OTHER_BLOCKS" mein daal diya jata hai, "CUSTOMER_SHEETS"
    mein NAHI (woh sirf zero-block flat sheets ke liye hai). Isliye purana
    _customer_rows_from_customer_sheets() BOSCH/APTIV/TATA ko kabhi dekh hi
    nahi paata tha -- woh sirf detail/audit tab ban kar reh jaate the,
    SUMMARY mein unki row hi nahi banti thi.

    NOTE: block ke andar chahe branch_code kuch bhi ho (jaise APTIV ka
    1318), customer-sheet ka NAAM hi is row ki identity hai -- reference
    report mein bhi APTIV ek alag '*' row hai, GURGAON(1318) branch mein
    merge nahi hui. Isliye yaha branch_code ko IGNORE karke, sirf category
    row-counts sheet-wise sum kiye jaate hain."""
    other_blocks = categorized_data.get("OTHER_BLOCKS", {}) or {}
    # Short-form party names (TATA MOTOR / TATA MOTORS / TATA) ek hi row
    # mein merge -- display name us group ka sabse lamba/descriptive naam.
    name_map = _party_display_name_map(other_blocks.keys())
    rows = {}
    for sheet_name, blocks in other_blocks.items():
        sheet_name = str(sheet_name).strip()
        if not sheet_name:
            continue
        display = name_map.get(sheet_name, sheet_name)
        row = rows.setdefault(display, {"B.NAME": display, "B.CODE": "", "PAID": 0, "TOPAY": 0,
                                         "PENDING_BILLING": 0, "POD_NOT_BILLED": 0, "DDR_PENDING": 0})
        for b in blocks or []:
            if not isinstance(b, dict) or "error" in b:
                continue
            cat = b.get("category")
            if cat in row:
                row[cat] += int(b.get("row_count", 0) or 0)
    result = []
    for row in rows.values():
        row["TOTAL"] = sum(row[c] for c in ("PAID","TOPAY","PENDING_BILLING","POD_NOT_BILLED","DDR_PENDING"))
        if row["TOTAL"] > 0:
            result.append(row)
    return result

def build_summary(categorized_data: dict, branch_name_map: dict = None,
                   directory_code_to_name: dict = None) -> pd.DataFrame:
    """Build the business SUMMARY: exactly one row per B.CODE.

    IMPORTANT:
    - B.CODE is the only identity key; 801 and "801" can never create two rows.
    - Customer/client sheets such as BOSCH/TATA MOTORS/APTIV are audit/detail
      tabs, NOT extra branch rows in SUMMARY.
    - If a source workbook already contains a SUMMARY pivot, its category totals
      are used once; detail sheets are not added a second time.
    """
    branch_name_map = {int(k): v for k, v in (branch_name_map or {}).items()
                       if pd.notna(k) and str(k).strip() != ""}
    directory_code_to_name = {int(k): v for k, v in (directory_code_to_name or {}).items()
                              if pd.notna(k) and str(k).strip() != ""}

    counts_per_cat = _category_count_sources(categorized_data, branch_name_map)

    entities = {}

    def norm_code(v):
        try:
            return int(float(str(v).strip()))
        except (TypeError, ValueError):
            return None

    def get_entity(code):
        if code not in entities:
            entities[code] = {"name": "", "code": code}
            for c in COUNTABLE_CATEGORIES:
                entities[code][c] = 0
        return entities[code]

    # Use pivot branch names only as a fallback.  Never let a customer-sheet
    # name replace the canonical directory/master branch name.
    for e in categorized_data.get("SUMMARY_PIVOT", []) or []:
        code = norm_code(e.get("branch_code"))
        if code is None:
            continue
        ent = get_entity(code)
        src_name = str(e.get("branch_name") or "").strip()
        if src_name and not ent["name"]:
            ent["name"] = src_name

    for cat, series in counts_per_cat.items():
        for raw_code, value in series.items():
            code = norm_code(raw_code)
            if code is None:
                continue
            ent = get_entity(code)
            ent[cat] = int(value)  # authoritative count, never add twice

    # Canonical branch name: branch master > directory > source fallback.
    for code, ent in entities.items():
        ent["name"] = str(
            directory_code_to_name.get(code)
            or branch_name_map.get(code)
            or ent.get("name")
            or "UNKNOWN BRANCH"
        ).strip()

    branch_rows = []
    for code, ent in entities.items():
        row = {"B.NAME": ent["name"], "B.CODE": code}
        total = 0
        for cat in COUNTABLE_CATEGORIES:
            v = int(ent[cat])
            row[CATEGORY_DISPLAY_NAMES[cat]] = v
            total += v
        row["TOTAL"] = total
        branch_rows.append(row)

    cols = ["S.NO", "B.NAME", "B.CODE"] + \
           [CATEGORY_DISPLAY_NAMES[c] for c in COUNTABLE_CATEGORIES] + ["TOTAL"]
    # FIX (2026-10-07): pehle yahan branch_rows khaali hone par turant
    # pd.DataFrame(columns=cols) return ho jaata tha -- isse neeche ka
    # customer_rows (TATA/BOSCH/etc. party rows) merge-step kabhi chalta hi
    # nahi tha. Matlab: agar kisi file mein SIRF party/customer sheets hon
    # (koi seedha branch-level PENDING BILLING/POD/etc sheet na ho), to
    # poori SUMMARY khaali ban jaati thi aur party ka data chupke se gayab
    # ho jaata tha. Ab branch_rows khaali ho tab bhi aage badhte hain --
    # customer/party rows (jo neeche merge hote hain) tab bhi SUMMARY mein
    # aa jaate hain.
    if branch_rows:
        out = pd.DataFrame(branch_rows, columns=cols[1:]).sort_values(
            ["TOTAL", "B.CODE"], ascending=[False, True], kind="stable"
        ).reset_index(drop=True)
        out.insert(0, "S.NO", range(1, len(out) + 1))
    else:
        out = pd.DataFrame(columns=cols)

    # Preserve customer/special rows from the input SUMMARY (e.g. BOSCH LTD,
    # APTIV, TATA). They intentionally have no B.CODE, so they are kept
    # separate from branch aggregation and appear exactly once.
    customer_rows = []
    seen_customer = set()
    for item in categorized_data.get("SUMMARY_CUSTOMERS", []) or []:
        name = str(item.get("B.NAME") or "").strip()
        if not name or name.upper() in seen_customer:
            continue
        seen_customer.add(name.upper())
        # FIX: use INTERNAL category keys (PAID/TOPAY/PENDING_BILLING/...)
        # here, matching _customer_rows_from_customer_sheets() and
        # _customer_rows_from_other_blocks(). Yeh row DISPLAY-name keys
        # ("PENDING BILLING" with space) use kar rahi thi, jabki neeche ka
        # normalize-step INTERNAL keys expect karta hai ("PENDING_BILLING"
        # underscore) -- mismatch ki wajah se .get() hamesha default 0
        # deta tha, isliye TATA MOTOR/customer row ka PENDING BILLING aur
        # POD ON NET BUT NOT BILLED column final SUMMARY mein 0 dikhta tha
        # (TOTAL sahi rehta tha kyunki uska apna alag "TOTAL" key hai).
        row = {"S.NO": "", "B.NAME": name, "B.CODE": ""}
        total = 0
        for cat in COUNTABLE_CATEGORIES:
            v = int(item.get(cat, 0) or 0)
            row[cat] = v
            total += v
        row["TOTAL"] = int(item.get("TOTAL", total) or total)
        customer_rows.append(row)

    # If the source SUMMARY itself did not carry customer rows, recover them
    # from flat customer detail tabs AND from block-structured OTHER sheets
    # (BOSCH LTD/APTIV/TATA jaisi sheets, jinke apne block-markers hote hain --
    # yahi asli bug tha; dono sources merge karna zaroori hai taaki koi
    # customer chhoote nahi). Yeh keeps BOSCH/APTIV/TATA in the final
    # SUMMARY for generated reports.
    if not customer_rows:
        merged = _customer_rows_from_customer_sheets(categorized_data)
        seen_names = {str(r.get("B.NAME", "")).strip().upper() for r in merged}
        for extra in _customer_rows_from_other_blocks(categorized_data):
            key = str(extra.get("B.NAME", "")).strip().upper()
            if key and key not in seen_names:
                merged.append(extra)
                seen_names.add(key)
        customer_rows = merged
        seen_customer = {str(x.get("B.NAME","")).strip().upper() for x in customer_rows}

    total_row = {"S.NO": "", "B.NAME": "", "B.CODE": "TOTAL"}
    for cat in COUNTABLE_CATEGORIES:
        # FIX: grand-total mein customer rows (BOSCH/APTIV/TATA) ka contribution
        # bhi jodna hai -- pehle sirf 'out' (branch rows) ka sum liya jaata tha,
        # isliye TOTAL row customer sheets ka data miss kar deti thi (reference
        # report mein TOTAL customer + branch dono ka sum hota hai).
        branch_sum = int(out[CATEGORY_DISPLAY_NAMES[cat]].sum())
        customer_sum = sum(int(item.get(cat, 0) or 0) for item in customer_rows)
        total_row[CATEGORY_DISPLAY_NAMES[cat]] = branch_sum + customer_sum
    total_row["TOTAL"] = int(out["TOTAL"].sum()) + sum(int(item.get("TOTAL", 0) or 0) for item in customer_rows)
    if customer_rows:
        # Normalize internal category keys (PAID/TOPAY/PENDING_BILLING/...)
        # into the exact display-column names used by SUMMARY.  Without this
        # conversion, recovered flat customer sheets could appear with blank
        # category cells even though TOTAL was correct.
        normalized_customer_rows = []
        for item in customer_rows:
            row = {"S.NO": "", "B.NAME": str(item.get("B.NAME") or "").strip(), "B.CODE": ""}
            total = 0
            for cat in COUNTABLE_CATEGORIES:
                display = CATEGORY_DISPLAY_NAMES[cat]
                v = int(item.get(cat, 0) or 0)
                row[display] = v
                total += v
            row["TOTAL"] = int(item.get("TOTAL", total) or total)
            normalized_customer_rows.append(row)
        customer_df = pd.DataFrame(normalized_customer_rows, columns=cols)
        out = pd.concat([customer_df, out], ignore_index=True)
        # Re-number only real branch rows; customer rows intentionally keep
        # blank S.NO just like the reference workbook.
        branch_mask = out["B.CODE"].apply(lambda x: str(x).strip().upper() != "" and
                                          str(x).strip().upper() != "TOTAL")
        out.loc[branch_mask, "S.NO"] = range(1, int(branch_mask.sum()) + 1)
    return pd.concat([out, pd.DataFrame([total_row])], ignore_index=True)


def branch_name_map_from_paid_topay(categorized_data: dict) -> dict:
    """PAID/TOPAY sheets mein B.CODE + BRANCH dono columns hote hain — is se
    seedha ek reliable code->name map ban jaata hai, jo baaki (naam-only)
    sheets ko map karne ke liye use hota hai."""
    m = {}
    for cat in ("PAID", "TOPAY"):
        df = categorized_data.get(cat)
        if df is None or df.empty:
            continue
        cols_upper = {c.upper(): c for c in df.columns}
        code_col = cols_upper.get("B.CODE") or cols_upper.get("B_CODE")
        name_col = cols_upper.get("BRANCH")
        if code_col is None or name_col is None:
            continue
        sub = df[[code_col, name_col]].dropna()
        for _, r in sub.iterrows():
            try:
                code = int(r[code_col])
            except (TypeError, ValueError):
                continue
            m[code] = str(r[name_col]).strip()
    return m


def _write_summary_sheet(writer, summary_df: pd.DataFrame, title: str):
    """Ek already-khuli ExcelWriter mein SUMMARY sheet likhta/style karta hai
    (golden reference GURGAON_REGION...DBP_REPORT.xlsx ki SUMMARY jaisi):
      - Title row: yellow, bold, sabhi columns mein merged
      - Header row: halka olive-green (E8F2A1), bold, centered, hair border
      - Customer/special rows (jinka B.CODE khaali hai): peach (FFDBB6) fill
      - Normal branch rows: white fill, bold, centered, hair border
      - TOTAL row: yellow, bold, centered, hair border
    """
    from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
    from openpyxl.utils import get_column_letter

    cols = list(summary_df.columns)
    n_cols = len(cols)

    hair = Side(style="hair")
    border_all = Border(left=hair, right=hair, top=hair, bottom=hair)
    center = Alignment(horizontal="center", vertical="center")

    fill_title = PatternFill("solid", fgColor="FFFF00")
    fill_header = PatternFill("solid", fgColor="E8F2A1")
    fill_customer = PatternFill("solid", fgColor="FFDBB6")
    fill_normal = PatternFill("solid", fgColor="FFFFFF")
    fill_total = PatternFill("solid", fgColor="FFFF00")

    summary_df.to_excel(writer, sheet_name="SUMMARY", index=False, startrow=2, header=False)
    ws = writer.sheets["SUMMARY"]

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n_cols)
    title_cell = ws.cell(row=1, column=1, value=title)
    title_cell.font = Font(bold=True, size=13)
    title_cell.fill = fill_title
    title_cell.alignment = center
    ws.row_dimensions[1].height = 22

    for j, col_name in enumerate(cols, start=1):
        c = ws.cell(row=2, column=j, value=col_name)
        c.font = Font(bold=True, size=12)
        c.fill = fill_header
        c.alignment = center
        c.border = border_all
    ws.row_dimensions[2].height = 27

    bcode_col_idx = cols.index("B.CODE") + 1 if "B.CODE" in cols else None
    for i in range(len(summary_df)):
        excel_row = i + 3
        is_total = str(summary_df.iloc[i].get("B.CODE", "")).strip().upper() == "TOTAL"
        bcode_val = summary_df.iloc[i].get("B.CODE", "") if bcode_col_idx else ""
        is_customer = (not is_total) and (bcode_val == "" or pd.isna(bcode_val))
        row_fill = fill_total if is_total else (fill_customer if is_customer else fill_normal)
        for j in range(1, n_cols + 1):
            c = ws.cell(row=excel_row, column=j)
            c.font = Font(bold=True, size=12)
            c.fill = row_fill
            c.alignment = center
            c.border = border_all

    width_map = {"S.NO": 8, "B.NAME": 32, "B.CODE": 12, "TOTAL": 11}
    for j, col_name in enumerate(cols, start=1):
        letter = get_column_letter(j)
        ws.column_dimensions[letter].width = width_map.get(col_name, 16)
    ws.freeze_panes = "A3"


def export_summary_to_excel(summary_df: pd.DataFrame, out_path: str,
                             title: str = "SUMMARY PENDING MATTERS DBP REPORT"):
    """SIRF SUMMARY sheet likhta hai (koi raw-data sheet nahi) -- backward
    compatible helper. Poori report (SUMMARY + raw data sheets) ke liye
    export_full_report_to_excel() use karo."""
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        _write_summary_sheet(writer, summary_df, title)


def _write_data_sheet(writer, df: pd.DataFrame, sheet_name: str):
    """Ek raw-data DataFrame ko clean, readable formatting (bold header,
    freeze panes, reasonable column widths) ke saath likhta hai."""
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    if df is None or df.empty:
        return
    safe_name = re.sub(r'[\\/*?:\[\]]', '', str(sheet_name))[:31] or "Sheet"
    n = 1
    base_name = safe_name
    while safe_name in writer.sheets:
        n += 1
        safe_name = f"{base_name[:28]}_{n}"
    df.to_excel(writer, sheet_name=safe_name, index=False)
    ws = writer.sheets[safe_name]
    header_fill = PatternFill("solid", fgColor="D9D9D9")
    for j, col in enumerate(df.columns, start=1):
        c = ws.cell(row=1, column=j)
        c.font = Font(bold=True)
        c.fill = header_fill
        try:
            sample_len = df[col].astype(str).str.len().clip(upper=40).mean()
            width = min(max(12, int(sample_len + 4)), 40)
        except Exception:
            width = 14
        ws.column_dimensions[get_column_letter(j)].width = width
    ws.freeze_panes = "A2"


def _friendly_block_category_label(title: str) -> str:
    """Block title text (jaise 'ADR MADE BUT NOT BILLED') ko ek saaf,
    turant-samajh-aane-wala label deta hai, taaki BOSCH/TATA/APTIV jaisi
    sheets mein ADR vs NET rows turant pehchaani ja sakein."""
    t = (title or "").strip().upper()
    if "ADR" in t or "PENDING BILLING" in t:
        return "PENDING BILLING (ADR)"
    if "NET" in t or "POD" in t:
        return "POD ON NET BUT NOT BILLED (NET)"
    return title or ""


def _prepare_block_display_df(df: pd.DataFrame) -> pd.DataFrame:
    """OTHER_BLOCKS_DATA/EXCLUDED_BLOCKS_DATA ka raw block-DataFrame lekar
    saaf banata hai: 'CATEGORY' column (jaise 'PENDING BILLING (ADR)')
    sabse AAGE rakhta hai, aur ADR/NET rows ko group karke sort karta hai --
    taaki sheet khulte hi turant pata chale kaunsi rows ADR hain, kaunsi NET."""
    display_df = df.rename(columns={"_SECTION_BRANCH": "BRANCH_CODE"})
    if "_BLOCK_TITLE" in display_df.columns:
        display_df["CATEGORY"] = df["_BLOCK_TITLE"].apply(_friendly_block_category_label)
        display_df = display_df.drop(columns=["_BLOCK_TITLE"])
    cols = list(display_df.columns)
    front = [c for c in ["BRANCH_CODE", "CATEGORY"] if c in cols]
    rest = [c for c in cols if c not in front]
    display_df = display_df[front + rest]
    if "CATEGORY" in display_df.columns:
        display_df = display_df.sort_values("CATEGORY", kind="stable").reset_index(drop=True)
    return display_df


def _sheet_safe_name(name: str, fallback: str = "Sheet") -> str:
    s = re.sub(r'[\\/*?:\[\]]', '', str(name or "").strip())
    return (s or fallback)[:31]


def _write_category_source_sheet(writer, categorized_data: dict, category: str, sheet_name: str):
    """Write the actual category source data under the requested business name."""
    df = _dedupe_category_rows(categorized_data.get(category))
    if df is None or df.empty:
        return
    rename_map = {
        "_SECTION_BRANCH": "RESOLVED_BRANCH",
        "_DDR_RESOLUTION": "RESOLUTION_METHOD",
        "_DDR_SECTION_MARKER": "SECTION_MARKER",
    }
    _write_data_sheet(
        writer,
        df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns}),
        sheet_name,
    )


def _merge_customer_blocks(categorized_data: dict, excluded: bool = False) -> dict:
    """Return normalized customer-sheet data keyed by canonical display name.

    Sheet-name whitespace/case variations are merged so e.g. 'TATA MOTORS',
    'TATA MOTORS ' and 'tata motors' cannot create three output tabs.
    For real PARTY tabs (excluded=False) this also merges short-form naming
    (TATA MOTOR / TATA MOTORS / TATA) via _party_display_name_map() -- the
    SAME grouping _customer_rows_from_other_blocks() uses for the summary
    row, so the exported tab's row-count always matches the summary count
    for that party. excluded=True (OTHER END/DISPUTE MATTER tabs) keeps the
    simpler whitespace/case-only merge since those aren't party names.
    """
    source_key = "EXCLUDED_BLOCKS_DATA" if excluded else "OTHER_BLOCKS_DATA"
    source = categorized_data.get(source_key, {}) or {}
    name_map = {} if excluded else _party_display_name_map(source.keys())
    merged = {}
    for raw_name, df in source.items():
        raw_name = str(raw_name).strip()
        if not raw_name:
            continue
        key = name_map.get(raw_name) or re.sub(r"\s+", " ", raw_name).upper()
        if key in merged:
            merged[key] = pd.concat([merged[key], df], ignore_index=True)
        else:
            merged[key] = df.copy()
    for key, df in list(merged.items()):
        merged[key] = df.drop_duplicates().reset_index(drop=True)
    return merged


def export_full_report_to_excel(categorized_data: dict, summary_df: pd.DataFrame, out_path: str,
                                 title: str = "SUMMARY PENDING MATTERS DBP REPORT",
                                 include_paid_topay: bool = True,
                                 include_ddr: bool = True):
    """Create the complete operational workbook.

    Output order:
      SUMMARY
      PAID
      TO PAY
      PENDING BILLING
      POD ON NET BUT NOT BILLED
      DDR PENDING
      customer sheets (e.g. BOSCH, TATA MOTORS, APTIV)
      OTHER END / DISPUTE MATTER
      DDR EXCLUDED (audit note)
      DDR AUDIT

    Customer sheets are detail/audit tabs only. Their rows are NEVER appended
    to SUMMARY, preventing customer names such as TATA MOTORS from appearing
    as a second branch row.
    """
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        _write_summary_sheet(writer, summary_df, title)

        # Business tabs: keep the user's terminology instead of renaming ADR/NET.
        if include_paid_topay:
            _write_category_source_sheet(writer, categorized_data, "PAID", "PAID")
            _write_category_source_sheet(writer, categorized_data, "TOPAY", "TO PAY")
        _write_category_source_sheet(writer, categorized_data, "PENDING_BILLING", "PENDING BILLING")
        _write_category_source_sheet(writer, categorized_data, "POD_NOT_BILLED", "POD ON NET BUT NOT BILLED")
        if include_ddr:
            _write_category_source_sheet(writer, categorized_data, "DDR_PENDING", "DDR PENDING")

        # Client-specific sheets are preserved as their own tabs.
        other = _merge_customer_blocks(categorized_data, excluded=False)
        excluded = _merge_customer_blocks(categorized_data, excluded=True)

        used_names = set(writer.book.sheetnames)
        for display_name, df in other.items():
            if df is None or df.empty:
                continue
            tab = _sheet_safe_name(display_name, "CUSTOMER")
            # Avoid collisions with reserved business tabs.
            if tab.upper() in {x.upper() for x in used_names}:
                tab = _sheet_safe_name("CUST_" + display_name, "CUSTOMER")
            _write_data_sheet(writer, _prepare_block_display_df(df), tab)
            used_names.add(tab)

        # Explicit excluded sheets remain visible, not silently dropped.
        for display_name, df in excluded.items():
            if df is None or df.empty:
                continue
            tab = _sheet_safe_name(display_name, "EXCLUDED")
            if tab.upper() in {x.upper() for x in used_names}:
                tab = _sheet_safe_name("EXCL_" + display_name, "EXCLUDED")
            _write_data_sheet(writer, _prepare_block_display_df(df), tab)
            used_names.add(tab)

        # DDR_EXCLUDED is an audit note, not a pending count.
        ddr_excl = categorized_data.get("DDR_EXCLUDED", []) or []
        if ddr_excl:
            rows = [{
                "SOURCE SHEET": e.get("sheet"),
                "BRANCH CODE / MARKER": e.get("branch_marker"),
                "ROW COUNT": e.get("row_count", 0),
                "REASON": e.get("reason", ""),
            } for e in ddr_excl]
            _write_data_sheet(writer, pd.DataFrame(rows), "DDR EXCLUDED")
            used_names.add("DDR EXCLUDED")

        # DDR AUDIT sheet REMOVED (user request) — raw-vs-resolved DDR audit
        # tab no longer written to final output.

        # Stable reference-style order, then customer/excluded/audit tabs.
        preferred = [
            "SUMMARY", "PAID", "TO PAY", "PENDING BILLING",
            "POD ON NET BUT NOT BILLED", "DDR PENDING",
            "BOSCH", "TATA MOTORS", "APTIV", "OTHER END", "DISPUTE MATTER",
            "DDR EXCLUDED"
        ]
        current = list(writer.book.sheetnames)
        ordered = []
        for wanted in preferred:
            match = next((s for s in current if s.upper() == wanted.upper()), None)
            if match and match not in ordered:
                ordered.append(match)
        ordered += [s for s in current if s not in ordered]
        writer.book._sheets = [writer.book[s] for s in ordered]


def export_full_report(categorized_data: dict, summary_df: pd.DataFrame, out_path: str,
                       title: str = "SUMMARY PENDING MATTERS DBP REPORT"):
    """Backward-compatible alias."""
    return export_full_report_to_excel(categorized_data, summary_df, out_path, title=title)
