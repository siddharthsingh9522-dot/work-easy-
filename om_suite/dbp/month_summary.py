# -*- coding: utf-8 -*-
"""
month_summary.py
ADR (Pending Billing) ka RAW data lekar month-wise branch summary banata hai
-- jaisa user ne reference image diya tha: B.NAME | B.CODE | <har month> |
TOTAL.

Design (CONFIRMED by user, 2026-10-02):
  1. Branch resolution ingest.detect_branch_column() se hi hota hai -- isi
     logic ka reuse jo pehle se "ADR BRANCH" (ya uske alias/combo-fallback)
     use karta hai aur OTHER END/DISPUTE MATTER ko already bahar rakhta hai.
     Isliye yeh month-wise summary tool automatically wahi saari fixes
     inherit karta hai, dobara likhne ki zaroorat nahi.
  2. Har row ki ADR DATE: pehle explicit date column (config.
     ADR_DATE_COLUMN_ALIASES), na mile to ADR_NO_DATE_BRANCH jaise
     combo-column ke andar se date-shaped token dhoond lete hain (position
     fixed nahi rakhi -- jo bhi token valid date ban jaye wahi).
  3. Jin rows ki date kahin se na mile, unhe user-selected "current/report
     month" mein daala jaata hai (caller/UI yeh month poochta hai -- is
     module mein sirf parameter hai, default aaj ka month).
"""
import datetime as _dt
import re
import warnings

import pandas as pd

from . import config
from .ingest import _find_column_by_aliases, detect_branch_column, _match_block_title_category
from .report_builder import _party_display_name_map


_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
_RE_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_RE_DMY_TXT = re.compile(r"(?<!\d)(\d{1,2})[-/. ]+([A-Za-z]{3,9})[-/. ,]+(\d{2,4})(?!\d)")
_RE_DMY_NUM = re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})(?!\d)")


def _mk_date(y, m, d):
    try:
        if y < 100:
            y += 2000 if y < 70 else 1900
        if not (2000 <= y <= 2100):
            return pd.NaT
        return pd.Timestamp(_dt.date(y, m, d))
    except (ValueError, OverflowError):
        return pd.NaT


def parse_date_value(v):
    """REAL BUG (2026-10-10, NET export): CN_DELIVERY_DATE aisi aati hai
    '14-JUL-26 03.23.58.000000 AM' (Oracle timestamp, time mein '.' aur
    microseconds) -- pandas.to_datetime isko NaT bana deta tha, isliye poora
    NET data galti se 'current month' mein gir jaata tha. Yeh parser har
    tarah ki date pehchanta hai (text ke andar kahin bhi): datetime object,
    ISO 2026-08-21, 21-AUG-26 (+time), 21-AUGUST-2026, 06.06.2026 /
    06/06/26 (day-first), 'DELIVERED-ON-15-JUN-26 12:56', aur Excel serial
    number. Valid na ho (ya saal 2000-2100 ke bahar ho) to NaT."""
    if v is None:
        return pd.NaT
    if isinstance(v, (pd.Timestamp, _dt.datetime, _dt.date)):
        ts = pd.Timestamp(v)
        return ts.normalize() if (pd.notna(ts) and 2000 <= ts.year <= 2100) else pd.NaT
    s = str(v).strip()
    if not s or s.upper() in ("NAN", "NONE", "NAT"):
        return pd.NaT
    m = _RE_ISO.search(s)
    if m:
        r = _mk_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if pd.notna(r):
            return r
    for m in _RE_DMY_TXT.finditer(s):
        mon = _MONTHS.get(m.group(2)[:3].upper())
        if mon:
            r = _mk_date(int(m.group(3)), mon, int(m.group(1)))
            if pd.notna(r):
                return r
    for m in _RE_DMY_NUM.finditer(s):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if mo > 12 and d <= 12:
            d, mo = mo, d
        r = _mk_date(y, mo, d)
        if pd.notna(r):
            return r
    try:
        f = float(s)
        if 36526 <= f <= 73415:   # Excel serial (2000-01-01 .. 2100-01-01)
            return pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(f))
    except ValueError:
        pass
    return pd.NaT


def parse_dates_series(series: pd.Series) -> pd.Series:
    """parse_date_value ko poori Series par lagata hai (unique values par ek
    baar -- bade sheets mein tez rehta hai). Returns datetime64 Series."""
    uniq = series.drop_duplicates()
    mapping = {u: parse_date_value(u) for u in uniq}
    out = series.map(mapping)
    return pd.to_datetime(out, errors="coerce")


def _extract_date_from_combo(value):
    """'<ADR NO>,<ADR DATE>,<ADR BRANCH>' jaisa combo-cell se date-shaped
    token nikalta hai -- position HARDCODE nahi ki, jo bhi comma-part ek
    plausible date (2000-2100) ban jaye wahi use karte hain, taaki token
    order kabhi badle to bhi na toote."""
    if value is None:
        return pd.NaT
    s = str(value).strip()
    if not s or s.upper() in ("NAN", "NONE", ""):
        return pd.NaT
    for part in (p.strip() for p in s.split(",") if p.strip()):
        dt = parse_date_value(part)
        if pd.notna(dt):
            return dt
    return pd.NaT


def _looks_like_dates(series: pd.Series) -> bool:
    """Naam se 'ADR DATE'/'DATE' match ho gaya, iska matlab yeh nahi ki usme
    SACH MEIN dates hain -- kuch real exports mein columns galat naam se aate
    hain (ek asli file mein 'DATE' naam wale column mein branch-code tha aur
    'ADR' naam wale column mein asli date thi!). Isliye naam par poora
    bharosa nahi karte -- parse karke confirm karte hain ki kam se kam aadhi
    non-empty values ek plausible (2000-2100) date ban rahi hain; nahi to
    yeh column reject karke combo-column fallback par chale jaate hain."""
    if series is None or len(series) == 0:
        return False
    non_null = series.notna() & (series.astype(str).str.strip() != "")
    if non_null.sum() == 0:
        return False
    parsed = parse_dates_series(series[non_null])
    return (parsed.notna().sum() / non_null.sum()) >= 0.5


def resolve_dates(df: pd.DataFrame, date_aliases: list, combo_aliases: list) -> pd.Series:
    """Har row ke liye date resolve karta hai (explicit column pehle --
    SIRF agar usme sach mein dates lagti hain -- phir combo-column
    fallback). Jahan kuch na mile wahan pd.NaT rehta hai -- caller
    current-month mein bucket karega."""
    dates = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    date_col = _find_column_by_aliases(df, date_aliases)
    if date_col is not None and _looks_like_dates(df[date_col]):
        dates = parse_dates_series(df[date_col])
    missing = dates.isna()
    if missing.any() and combo_aliases:
        combo_col = _find_column_by_aliases(df, combo_aliases)
        if combo_col is not None:
            filled = df.loc[missing, combo_col].apply(_extract_date_from_combo)
            dates.loc[missing] = pd.to_datetime(filled, errors="coerce")
    return dates


def till_text(period: pd.Period) -> str:
    """'TILL 31 AUGUST 2026' (reference report ke title jaisa)."""
    return f"TILL {period.end_time.day} {period.strftime('%B').upper()} {period.year}"


def month_label(period: pd.Period) -> str:
    return period.strftime("%B - %y")   # e.g. "August - 26"


def _empty_matrix():
    return pd.DataFrame(columns=["B.CODE", "B.NAME", "TOTAL"])


def _norm_code(v):
    """Branch code ko canonical int-string banata hai ('0503'/503.0/'503' ->
    '503'); numeric na ho (jaise header-text 'ADR BRANCH') to None."""
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    if f != f or f != int(f) or f < 0:
        return None
    return str(int(f))


def build_month_wise_summary(df: pd.DataFrame, branch_col: str, date_aliases: list,
                              combo_aliases: list, branch_name_map: dict = None,
                              current_month: pd.Period = None, region_branches: list = None):
    """Returns (matrix_df, n_missing_dates, month_labels_used).
    matrix_df columns: B.CODE, B.NAME, <month1>, <month2>, ..., TOTAL
    current_month: pd.Period(freq='M') -- date-less rows isi month mein
    girte hain. None -> aaj ka month.
    region_branches: us region ki branch-codes ki ORDERED list -- in sab ki
    row hamesha aati hai (zero pending ho tab bhi, reference report jaisa),
    isi order mein; data mein mili baaki branches uske baad numeric order mein
    judti hain (koi data kabhi gira nahi diya jaata). None -> sirf data wali
    branches, numeric ascending (pehle string-sort tha: '1802' < '501' galat).
    matrix_df.attrs['audit'] mein counting-audit milta hai (rows in/counted/
    dropped + dropped values ke namune) -- kuch bhi chupke se gayab nahi."""
    audit = {"rows_in": 0, "rows_counted": 0, "rows_dropped_bad_branch": 0,
             "dropped_samples": [], "rows_missing_date": 0}
    codes_region = []
    for c in (region_branches or []):
        nc = _norm_code(c)
        if nc is not None and nc not in codes_region:
            codes_region.append(nc)

    have_rows = df is not None and not df.empty and branch_col in df.columns
    cm = current_month or pd.Period(pd.Timestamp.now(), freq="M")
    work = pd.DataFrame({"branch": [], "period": []})
    n_missing = 0
    if have_rows:
        audit["rows_in"] = int(len(df))
        dates = resolve_dates(df, date_aliases, combo_aliases)
        n_missing = int(dates.isna().sum())
        audit["rows_missing_date"] = n_missing
        periods = dates.dt.to_period("M")
        periods = periods.where(periods.notna(), cm)
        raw_b = df[branch_col].astype(str).str.strip()
        norm_b = raw_b.map(_norm_code)
        bad = norm_b.isna()
        audit["rows_dropped_bad_branch"] = int(bad.sum())
        audit["dropped_samples"] = sorted(set(raw_b[bad].tolist()))[:10]
        work = pd.DataFrame({"branch": norm_b[~bad], "period": periods[~bad]})
        audit["rows_counted"] = int(len(work))

    if work.empty and not codes_region:
        out = _empty_matrix()
        out.attrs["audit"] = audit
        return out, n_missing, []

    if work.empty:
        all_periods = [cm]
    else:
        pmin, pmax = work["period"].min(), work["period"].max()
        # continuous range (beech ke zero-wale months bhi), cm tak extend
        # sirf tab jab caller ne explicit current_month diya ho
        end_p = max(pmax, current_month) if current_month is not None else pmax
        all_periods = list(pd.period_range(pmin, end_p, freq="M"))
    labels = [month_label(p) for p in all_periods]

    if work.empty:
        pivot = pd.DataFrame(0, index=[], columns=all_periods)
    else:
        pivot = work.pivot_table(index="branch", columns="period", aggfunc="size", fill_value=0)
        pivot = pivot.reindex(columns=all_periods, fill_value=0)

    data_codes = sorted(pivot.index, key=lambda x: int(x))
    ordered = list(codes_region) + [c for c in data_codes if c not in codes_region]
    pivot = pivot.reindex(ordered, fill_value=0)
    pivot["TOTAL"] = pivot.sum(axis=1)
    out = pivot.reset_index().rename(columns={"index": "B.CODE", "branch": "B.CODE"})
    out.columns = ["B.CODE"] + labels + ["TOTAL"]

    name_map = branch_name_map or {}
    def _lookup_name(code):
        for key in (code, str(code)):
            if key in name_map:
                return name_map[key]
        try:
            ic = int(float(code))
            if ic in name_map:
                return name_map[ic]
        except (TypeError, ValueError):
            pass
        return ""
    out.insert(1, "B.NAME", out["B.CODE"].map(_lookup_name).astype(str).str.strip())

    total_row = {"B.CODE": "TOTAL", "B.NAME": ""}
    for c in labels + ["TOTAL"]:
        total_row[c] = int(out[c].sum())
    out = pd.concat([out, pd.DataFrame([total_row])], ignore_index=True)
    out.attrs["audit"] = audit
    return out, n_missing, labels


def build_adr_month_summary(categorized_data: dict, branch_name_map: dict = None,
                             current_month: pd.Period = None, region_branches: list = None):
    """ADR (PENDING_BILLING) ke liye -- categorized_data woh dict hai jo
    ingest.load_workbook_categorized()/merge_all_uploaded() deta hai."""
    df = (categorized_data or {}).get("PENDING_BILLING")
    branch_col = None
    if df is not None and not df.empty:
        branch_col, _ = detect_branch_column(df, "PENDING_BILLING")
    if branch_col is None:
        df = None
        branch_col = "ADR BRANCH"
    return build_month_wise_summary(df, branch_col, config.ADR_DATE_COLUMN_ALIASES,
                                     config.ADR_NO_DATE_BRANCH_COLUMN_ALIASES,
                                     branch_name_map, current_month, region_branches)


def build_party_month_matrix(categorized_data: dict, category: str, date_aliases: list,
                              combo_aliases: list, current_month: pd.Period = None):
    """Party/customer sheets (OTHER_BLOCKS_DATA -- TATA MOTOR, BOSCH LTD,
    APTIV etc.) se, ek category (PENDING_BILLING ya POD_NOT_BILLED) ke
    blocks chun kar unka month-wise matrix banata hai. Short-form party
    naam (TATA MOTOR / TATA MOTORS / TATA) automatically merge hote hain
    (report_builder._party_display_name_map() -- wahi logic jo SUMMARY
    build karte waqt use hoti hai, isliye dono jagah ek jaisa result).
    OTHER END/DISPUTE MATTER party-sheet mein ho bhi to woh EXCLUDED_BLOCKS_DATA
    mein already alag ho chuka hota hai (ingest.py fix) -- yahan touch nahi hota.
    Returns (matrix_df [B.NAME + months + TOTAL, no B.CODE/S.NO], n_missing).
    """
    other = (categorized_data or {}).get("OTHER_BLOCKS_DATA", {}) or {}
    if not other:
        return pd.DataFrame(columns=["B.NAME", "TOTAL"]), 0
    name_map = _party_display_name_map(other.keys())
    frames = []
    for sheet_name, df in other.items():
        if df is None or df.empty or "_BLOCK_TITLE" not in df.columns:
            continue
        display = name_map.get(str(sheet_name).strip(), str(sheet_name).strip())
        cats = df["_BLOCK_TITLE"].apply(_match_block_title_category)
        sub = df[cats == category]
        if sub.empty:
            continue
        sub = sub.copy()
        sub["_PARTY"] = display
        frames.append(sub)
    if not frames:
        return pd.DataFrame(columns=["B.NAME", "TOTAL"]), 0

    combined = pd.concat(frames, ignore_index=True)
    dates = resolve_dates(combined, date_aliases, combo_aliases)
    n_missing = int(dates.isna().sum())
    cm = current_month or pd.Period(pd.Timestamp.now(), freq="M")
    periods = dates.dt.to_period("M")
    periods = periods.where(periods.notna(), cm)

    work = pd.DataFrame({"party": combined["_PARTY"], "period": periods})
    all_periods = sorted(work["period"].unique())
    pivot = work.pivot_table(index="party", columns="period", aggfunc="size", fill_value=0)
    pivot = pivot.reindex(columns=all_periods, fill_value=0).sort_index()
    pivot["TOTAL"] = pivot.sum(axis=1)
    labels = [month_label(p) for p in all_periods]
    out = pivot.reset_index().rename(columns={"party": "B.NAME"})
    out.columns = ["B.NAME"] + labels + ["TOTAL"]
    return out, n_missing


def build_full_month_wise_report(categorized_data: dict, branch_name_map: dict,
                                  current_month_adr: pd.Period = None,
                                  current_month_net: pd.Period = None,
                                  region_branches: list = None):
    """CONFIRMED by user (2026-10-08): party rows (BOSCH, TATA MOTOR, etc.)
    aur branch rows EK HI month-wise table mein saath dikhne chahiye --
    party rows upar (bina S.NO/B.CODE ke), phir numbered branch rows, phir
    TOTAL row -- jaisa unke reference report mein hai. Dono category (ADR
    aur NET) ke liye alag-alag combined tables banata hai.
    Returns dict: {"PENDING_BILLING": (df, labels, n_missing),
                   "POD_NOT_BILLED": (df, labels, n_missing)}
    Har df ke columns: S.NO, B.CODE, B.NAME, <months...>, TOTAL -- party
    rows ke S.NO/B.CODE khaali (empty string) hote hain.
    """
    specs = [
        ("PENDING_BILLING", config.ADR_DATE_COLUMN_ALIASES, build_adr_month_summary, current_month_adr),
        ("POD_NOT_BILLED", config.NET_DATE_COLUMN_ALIASES, build_net_month_summary, current_month_net),
    ]
    # CONFIRMED by user (2026-10-08): ADR aur NET apni-apni ALAG continuous
    # month-range dikhayenge -- jis mahine se us category ka PEHLA asli data
    # shuru hota hai wahi se (ADR ka pehla data July-25 ho to ADR table
    # April/May/June-25 nahi dikhayega), beech ke zero-wale months bhi
    # continuous dikhte hain, par doosri category ke start se match karne
    # ke liye range badhai nahi jaati.
    computed = {}
    for cat, date_aliases, branch_fn, cm in specs:
        branch_mat, n_missing_b, labels_b = branch_fn(categorized_data, branch_name_map, cm, region_branches)
        combo = config.ADR_NO_DATE_BRANCH_COLUMN_ALIASES if cat == "PENDING_BILLING" else []
        party_mat, n_missing_p = build_party_month_matrix(
            categorized_data, cat, date_aliases, combo, cm)
        occurring = set(labels_b) | (set(party_mat.columns) - {"B.NAME", "TOTAL"}
                                      if not party_mat.empty else set())
        if occurring:
            periods_occ = [pd.Period(datetime_strptime_month(lbl), freq="M") for lbl in occurring]
            start = min(periods_occ)
            end = max(periods_occ + ([cm] if cm else []))
            cat_labels = [month_label(p) for p in pd.period_range(start, end, freq="M")]
        else:
            cat_labels = []
        computed[cat] = (branch_mat, party_mat, n_missing_b + n_missing_p, cm, cat_labels)

    # Koi party sirf EK category mein hi (ADR ya NET) dikh sakti hai agar
    # doosre mein uska total 0 ho (koi matching block hi nahi mila) --
    # reference report ke jaisa dono table mein HAR party ki row dikhane ke
    # liye (zero-filled) missing parties ko yahan backfill karte hain.
    all_party_names = set()
    for _, party_mat, _, _, _ in computed.values():
        if not party_mat.empty:
            all_party_names |= set(party_mat["B.NAME"])
    for cat in list(computed.keys()):
        branch_mat, party_mat, n_missing, cm, cat_labels = computed[cat]
        missing = all_party_names - (set(party_mat["B.NAME"]) if not party_mat.empty else set())
        if missing:
            zero_rows = pd.DataFrame([{"B.NAME": nm, "TOTAL": 0} for nm in missing])
            party_mat = pd.concat([party_mat, zero_rows], ignore_index=True).fillna(0)
            party_mat = party_mat.sort_values("B.NAME").reset_index(drop=True)
            computed[cat] = (branch_mat, party_mat, n_missing, cm, cat_labels)

    out = {}
    for cat, date_aliases, branch_fn, cm in specs:
        branch_mat, party_mat, n_missing, _, all_labels = computed[cat]

        def _reindex_months(df, label_cols):
            for lbl in all_labels:
                if lbl not in df.columns:
                    df[lbl] = 0
            return df

        # party rows (no S.NO / B.CODE)
        prows = []
        if not party_mat.empty:
            party_mat = _reindex_months(party_mat.copy(), all_labels)
            for _, r in party_mat.iterrows():
                row = {"S.NO": "", "B.CODE": "", "B.NAME": r["B.NAME"]}
                for lbl in all_labels:
                    row[lbl] = int(r.get(lbl, 0))
                row["TOTAL"] = int(r["TOTAL"])
                prows.append(row)

        # branch rows (numbered, skip the auto TOTAL row build_*_month_summary adds)
        brows = []
        if not branch_mat.empty:
            branch_mat = _reindex_months(branch_mat.copy(), all_labels)
            branch_only = branch_mat[branch_mat["B.CODE"].astype(str) != "TOTAL"]
            for i, (_, r) in enumerate(branch_only.iterrows(), start=1):
                row = {"S.NO": i, "B.CODE": r["B.CODE"], "B.NAME": r["B.NAME"]}
                for lbl in all_labels:
                    row[lbl] = int(r.get(lbl, 0))
                row["TOTAL"] = int(r["TOTAL"])
                brows.append(row)

        total_row = {"S.NO": "", "B.CODE": "", "B.NAME": "TOTAL"}
        for lbl in all_labels:
            total_row[lbl] = sum(r[lbl] for r in prows + brows)
        total_row["TOTAL"] = sum(r["TOTAL"] for r in prows + brows)

        cols = ["S.NO", "B.CODE", "B.NAME"] + all_labels + ["TOTAL"]
        combined_df = pd.DataFrame(prows + brows + [total_row], columns=cols)
        audit = dict(branch_mat.attrs.get("audit", {})) if hasattr(branch_mat, "attrs") else {}
        audit["party_rows_counted"] = int(sum(r["TOTAL"] for r in prows))
        audit["grand_total"] = int(total_row["TOTAL"])
        combined_df.attrs["audit"] = audit
        out[cat] = (combined_df, all_labels, n_missing)
    return out


def datetime_strptime_month(label: str):
    return pd.to_datetime("01 " + label.replace(" - ", " 20"), format="%d %B %Y")


def build_net_month_summary(categorized_data: dict, branch_name_map: dict = None,
                             current_month: pd.Period = None, region_branches: list = None):
    """NET (POD_NOT_BILLED) ke liye. CONFIRMED by user: date SIRF
    CN_DELIVERY_DATE se (koi ADR-combo fallback nahi -- ADR ki date NET ke
    liye galat hogi), branch BILL_BRANCH_CODE se; jis row mein
    CN_DELIVERY_DATE nahi, woh 'current month' popup wale month mein."""
    df = (categorized_data or {}).get("POD_NOT_BILLED")
    branch_col = None
    if df is not None and not df.empty:
        branch_col, _ = detect_branch_column(df, "POD_NOT_BILLED")
    if branch_col is None:
        df = None
        branch_col = "BILL_BRANCH_CODE"
    return build_month_wise_summary(df, branch_col, config.NET_DATE_COLUMN_ALIASES,
                                     [], branch_name_map, current_month, region_branches)


def export_full_month_wise_report_to_excel(report: dict, region_name: str, out_path: str,
                                            current_month_adr: pd.Period = None,
                                            current_month_net: pd.Period = None):
    """report = build_full_month_wise_report() ka output. Ek SUMMARY sheet
    banata hai jisme ADR table upar, NET table neeche (jaisa user ke
    reference report mein hai) -- party rows bold+colored, branch rows
    numbered, TOTAL row bold."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SUMMARY"

    title_fill = PatternFill("solid", fgColor="FFFF00")
    header_fill = PatternFill("solid", fgColor="D9EAD3")
    party_font = Font(bold=True, color="C00000")
    total_fill = PatternFill("solid", fgColor="FFF2CC")

    specs = [
        ("PENDING_BILLING", "ADR MADE BUT NOT BILLED", current_month_adr),
        ("POD_NOT_BILLED", "POD ON NET BUT NOT BILLED", current_month_net),
    ]
    r = 1
    for cat, label, cm in specs:
        df, months, _ = report.get(cat, (pd.DataFrame(), [], 0))
        n_cols = len(df.columns) if not df.empty else 5
        till = f" {till_text(cm)}" if cm else ""
        title_text = f"SCM {region_name.upper()} REGION PENDING BILLING REPORT{till}\n({label})"
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=max(n_cols, 1))
        tc = ws.cell(row=r, column=1, value=title_text)
        tc.font = Font(bold=True, size=12)
        tc.fill = title_fill
        tc.alignment = Alignment(horizontal="center", wrap_text=True)
        r += 1
        if df.empty:
            r += 2
            continue
        for j, col in enumerate(df.columns, start=1):
            c = ws.cell(row=r, column=j, value=col)
            c.font = Font(bold=True)
            c.fill = header_fill
            c.alignment = Alignment(horizontal="center")
        r += 1
        for _, row in df.iterrows():
            is_party = row["B.CODE"] == "" and row["B.NAME"] != "TOTAL"
            is_total = row["B.NAME"] == "TOTAL"
            for j, col in enumerate(df.columns, start=1):
                c = ws.cell(row=r, column=j, value=row[col])
                if is_party:
                    c.font = party_font
                elif is_total:
                    c.font = Font(bold=True)
                    c.fill = total_fill
            r += 1
        r += 1   # blank row between the two tables

    ws.column_dimensions["A"].width = 7
    ws.column_dimensions["B"].width = 9
    ws.column_dimensions["C"].width = 26
    wb.save(out_path)
    return out_path


def export_month_summary_to_excel(matrix_df: pd.DataFrame, title: str, out_path: str):
    """B.NAME/B.CODE/month-cols/TOTAL ko ek styled Excel sheet mein likhta
    hai -- title row (bold, yellow), header row (bold, light-green),
    TOTAL row (bold)."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Month-wise Summary"

    n_cols = len(matrix_df.columns)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n_cols)
    tc = ws.cell(row=1, column=1, value=title)
    tc.font = Font(bold=True, size=13)
    tc.fill = PatternFill("solid", fgColor="FFFF00")
    tc.alignment = Alignment(horizontal="center")

    header_fill = PatternFill("solid", fgColor="D9EAD3")
    for j, col in enumerate(matrix_df.columns, start=1):
        c = ws.cell(row=2, column=j, value=col)
        c.font = Font(bold=True)
        c.fill = header_fill
        c.alignment = Alignment(horizontal="center")

    is_total_row = matrix_df["B.CODE"].astype(str) == "TOTAL"
    for i, (_, row) in enumerate(matrix_df.iterrows(), start=3):
        bold = bool(is_total_row.iloc[i - 3])
        for j, col in enumerate(matrix_df.columns, start=1):
            c = ws.cell(row=i, column=j, value=row[col])
            if bold:
                c.font = Font(bold=True)
                c.fill = PatternFill("solid", fgColor="FFF2CC")

    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 26
    for j in range(3, n_cols + 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(j)].width = 13
    ws.freeze_panes = "C3"

    wb.save(out_path)
    return out_path
