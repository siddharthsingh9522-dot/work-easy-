"""Glue between the pandas engine and the web pages (ports the helper functions of the old Streamlit app.py)."""
import html
from datetime import datetime

import pandas as pd

from . import config, directory_loader, ingest, month_summary, report_builder, region_branches, validator
from .masters import Masters

KPI_COLS = ["PAID", "TO PAY", "PENDING BILLING", "POD ON NET BUT NOT BILLED", "DDR PENDING", "TOTAL"]


# ---------------------------------------------------------------- shared maps
def current_maps(categorized, masters: Masters):
    paid_map = report_builder.branch_name_map_from_paid_topay(categorized)
    combined = {**paid_map, **masters.branch_master_map}
    dmap = directory_loader.branch_code_to_name_map(masters.directory_df)
    return paid_map, {**combined, **dmap}, dmap


def month_name_map(categorized, masters: Masters):
    """Branch master first (the user's manual reports use these names), then names inside the file, directory last."""
    paid_map, _, dmap = current_maps(categorized, masters)
    return {**dmap, **paid_map, **masters.branch_master_map}


# ---------------------------------------------------------------- report builder
def generate_report(ws, masters: Masters, region, paths):
    categorized = ingest.merge_all_uploaded(paths)
    paid_map, combined, dmap = current_maps(categorized, masters)
    summary = report_builder.build_summary(categorized, paid_map, {**masters.branch_master_map, **dmap})
    ws.save_state(dict(
        categorized_data=categorized, summary_df=summary, branch_name_map=combined, source_paths=paths,
        region_name=region, chat_history=[], issues=None,
        last_report_time=datetime.now().strftime("%d-%b-%Y %H:%M")))
    return summary


def export_report(ws, st):
    region = st["region_name"]
    safe = "".join(c if c.isalnum() else "_" for c in region).strip("_") or "Region"
    name = f"{safe}_DBP_REPORT.xlsx"
    path = ws.out_path(name)
    report_builder.export_full_report_to_excel(
        st["categorized_data"], st["summary_df"], path,
        title=f"{region} SUMMARY PENDING MATTERS REPORT", include_ddr=True)
    return path, name


def summary_kpis(summary: pd.DataFrame):
    out = []
    for c in KPI_COLS:
        if c in summary.columns:
            vals = pd.to_numeric(summary[c], errors="coerce")
            if "B.CODE" in summary.columns:   # ignore a possible TOTAL row so we do not double count
                vals = vals[~summary["B.CODE"].astype(str).str.upper().isin(["TOTAL", "GRAND TOTAL"])]
            out.append((c, int(vals.fillna(0).sum())))
    return out


def top_pending(summary: pd.DataFrame, n=8):
    if "TOTAL" not in summary.columns or "B.CODE" not in summary.columns:
        return []
    d = summary[~summary["B.CODE"].astype(str).str.upper().isin(["TOTAL", "GRAND TOTAL"])].copy()
    d["TOTAL"] = pd.to_numeric(d["TOTAL"], errors="coerce").fillna(0)
    d = d.sort_values("TOTAL", ascending=False).head(n)
    name = "B.NAME" if "B.NAME" in d.columns else "B.CODE"
    return [(str(r[name]), str(r["B.CODE"]), int(r["TOTAL"])) for _, r in d.iterrows() if r["TOTAL"] > 0]


# ---------------------------------------------------------------- validation
def run_validation(st):
    return validator.run_all_checks(
        st["categorized_data"], directory_code_set=set(st["branch_name_map"].keys()),
        branch_name_map=st["branch_name_map"],
        source_path_for_reconcile=st["source_paths"][0] if st["source_paths"] else None,
        computed_summary=st["summary_df"])


# ---------------------------------------------------------------- month-wise
CATS = {
    "adr": dict(label="ADR (Pending Billing)", cat="PENDING_BILLING", aliases=config.ADR_DATE_COLUMN_ALIASES,
                date_hint="ADR DATE", report_label="ADR MADE BUT NOT BILLED", kind="Pending Billing",
                fn=month_summary.build_adr_month_summary, title_kind="PENDING BILLING"),
    "net": dict(label="NET (POD on NET but not billed)", cat="POD_NOT_BILLED", aliases=config.NET_DATE_COLUMN_ALIASES,
                date_hint="CN_DELIVERY_DATE", report_label="NET POD BUT NOT BILLED", kind="NET",
                fn=month_summary.build_net_month_summary, title_kind="POD ON NET"),
    "full": dict(label="Full Region Report (Party + Branch, ADR + NET)"),
}


def period(s):
    return pd.Period(s, freq="M") if s else None


def missing_dates(data, cat_name, aliases):
    df = (data or {}).get(cat_name)
    if df is None or df.empty:
        return 0
    bcol, _ = ingest.detect_branch_column(df, cat_name)
    if bcol is None:
        return 0
    return int(month_summary.resolve_dates(df, aliases, config.ADR_NO_DATE_BRANCH_COLUMN_ALIASES).isna().sum())


def codes_from_inputs(region, use_saved, pasted, uploaded_file, save):
    """Region branch list (so zero-pending branches still show). Mirrors the old picker; returns list or None."""
    new = []
    if uploaded_file is not None and uploaded_file.filename:
        new = region_branches.codes_from_excel(uploaded_file)
    if not new and (pasted or "").strip():
        new = region_branches.parse_codes_text(pasted)
    if new:
        if save:
            region_branches.save(region, new)
        return new
    if use_saved:
        return region_branches.get(region) or None
    return None


def month_compute(key, ms, masters: Masters, data):
    """-> dict(needs_month=bool, ...result...). `ms` is the saved settings of this mode."""
    info = CATS[key]
    name_map = month_name_map(data, masters)
    branches = ms.get("branches") or None
    region = ms.get("region", "")
    if key == "full":
        need_adr = missing_dates(data, "PENDING_BILLING", config.ADR_DATE_COLUMN_ALIASES) > 0
        need_net = missing_dates(data, "POD_NOT_BILLED", config.NET_DATE_COLUMN_ALIASES) > 0
        cm_adr, cm_net = period(ms.get("cm_adr")), period(ms.get("cm_net"))
        need = [n for n, flag, cm in (("adr", need_adr, cm_adr), ("net", need_net, cm_net)) if flag and cm is None]
        if need:
            return dict(needs_month=need)
        report = month_summary.build_full_month_wise_report(data, name_map, cm_adr, cm_net, region_branches=branches)
        sections = []
        for cat, label in (("PENDING_BILLING", "ADR"), ("POD_NOT_BILLED", "NET")):
            df, months, _ = report[cat]
            sections.append(dict(label=label, df=df, months=len(months)))
        return dict(needs_month=[], report=report, sections=sections, audit=audit_info(report, data),
                    cm_adr=cm_adr, cm_net=cm_net)
    n_missing = missing_dates(data, info["cat"], info["aliases"])
    cm = period(ms.get("cm"))
    if n_missing > 0 and cm is None:
        return dict(needs_month=["single"], n_missing=n_missing)
    matrix, n_miss, labels = info["fn"](data, name_map, cm, branches)
    return dict(needs_month=[], matrix=matrix, labels=labels, cm=cm, n_missing=n_missing)


def audit_info(report, fr_data):
    """Counting audit: raw rows -> counted rows -> grand total must reconcile (port of _render_count_audit)."""
    excl = (fr_data or {}).get("EXCLUDED_BLOCKS_DATA", {}) or {}
    n_excl = int(sum(len(d) for d in excl.values())) if excl else 0
    rows, all_ok = [], True
    for cat, label in (("PENDING_BILLING", "ADR"), ("POD_NOT_BILLED", "NET")):
        df, months, _ = report[cat]
        a = df.attrs.get("audit", {}) if hasattr(df, "attrs") else {}
        counted, party, grand = a.get("rows_counted", 0), a.get("party_rows_counted", 0), a.get("grand_total", 0)
        ok = counted + party == grand
        all_ok = all_ok and ok
        rows.append(dict(label=label, rows_in=a.get("rows_in", 0), counted=counted, party=party,
                         dateless=a.get("rows_missing_date", 0), grand=grand, ok=ok,
                         dropped=a.get("rows_dropped_bad_branch", 0), samples=a.get("dropped_samples")))
    return dict(rows=rows, excluded=n_excl, all_ok=all_ok)


def month_export(ws, key, ms, result):
    region = ms.get("region", "") or "Region"
    safe = "".join(c if c.isalnum() else "_" for c in region).strip("_") or "Region"
    if key == "full":
        path = ws.out_path(f"{safe}_PENDING_BILLING_REPORT.xlsx")
        month_summary.export_full_month_wise_report_to_excel(
            result["report"], region, path, current_month_adr=result["cm_adr"], current_month_net=result["cm_net"])
        return path
    info, cm = CATS[key], result["cm"]
    title = f"{region.upper()} {info['title_kind']} REPORT" + (f" {month_summary.till_text(cm)}" if cm else "") \
            + f" ({info['report_label']})"
    path = ws.out_path(f"{safe}_{key.upper()}_MONTHWISE.xlsx")
    month_summary.export_month_summary_to_excel(result["matrix"], title, path)
    return path


# ---------------------------------------------------------------- html helpers
def df_html(df: pd.DataFrame, max_rows=1500):
    if df is None or df.empty:
        return ""
    cut = df.iloc[:max_rows]
    out = cut.to_html(index=False, border=0, classes="tbl dbp", na_rep="", escape=True)
    if len(df) > max_rows:
        out += f'<p class="muted small">Pehli {max_rows} rows dikh rahi hain ({len(df)} me se). Poora data Excel me hai.</p>'
    return out


def esc(s):
    return html.escape(str(s))
