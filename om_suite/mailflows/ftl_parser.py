"""FTL pending-bill report -> branch-wise docket lists (the "FTL Pending Mail" workflow).

The report is the ERP export with headers such as B.CODE, BRANCH, REGION, CN_CN_NO, CN_DATE, PARTY_CODE,
PARTY_NAME ... LOAD_TYPE, CHLN_LORRY_NO.  Underscores / dots / spaces in a header are treated alike, the header row
is found automatically (title rows above it are fine), and every column of the file is kept so the mail can show the
complete row, exactly like the report.

parse_ftl_report(path) -> (branches, stats)

    branches = {
        "814": {
            "branch_code": "814", "branch_name_sheet": "DANKUNI KOLKATA", "region": "KOLKATA",
            "columns": ["B.CODE", "BRANCH", ...],                       # headers as written in the file
            "grs": [{"cn": "7147163", "cn_date": "25.10.2026", "party_code": "840607",
                     "party_name": "STEEL & METALS", "cells": ["814", "DANKUNI KOLKATA", ...]}],   # one value per column
        }
    }
    stats = {"rows": .., "kept": .., "other_load": .., "blank_cn": .., "duplicates": .., "no_branch": ..}

Same shape as the TBB branches ("grs", "cn", "party_name" ...) so the review / AI-check / send machinery is shared.
"""
import re
from datetime import date, datetime
from numbers import Integral, Real

import pandas as pd

from .directory_parser import _safe_branch_code

HEADER_SCAN_ROWS = 30

# field -> accepted headers after normalisation (upper-case, every run of non A-Z0-9 becomes one space)
FIELD_HEADERS = {
    "branch_code": ["B CODE", "BCODE", "BRANCH CODE", "BR CODE"],
    "branch_name": ["BRANCH", "BRANCH NAME"],
    "region": ["REGION", "REGION NAME"],
    "cn": ["CN CN NO", "CN NO", "CN NUMBER", "CN", "GR NO", "GR NUMBER", "GR", "DOCKET NO", "DOCKET NUMBER", "DOCKET"],
    "cn_date": ["CN DATE", "GR DATE", "DOCKET DATE"],
    "party_code": ["PARTY CODE", "PARTY CD"],
    "party_name": ["PARTY NAME", "PARTY"],
    "load_type": ["LOAD TYPE", "LOADTYPE"],
}
REQUIRED = ("branch_code", "cn")


def norm(header) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", str(header or "").upper()).strip()


def cell_text(value) -> str:
    """One cell as the text people see in Excel: no '.0' on whole numbers, dates as dd.mm.yyyy, blanks as ''."""
    if value is None:
        return ""
    if not isinstance(value, str) and pd.isna(value):
        return ""
    if isinstance(value, (datetime, date)):                      # pd.Timestamp is a datetime
        has_time = isinstance(value, datetime) and (value.hour or value.minute or value.second)
        return value.strftime("%d.%m.%Y %H:%M" if has_time else "%d.%m.%Y")
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, Integral):
        return str(int(value))
    if isinstance(value, Real):
        f = float(value)
        return str(int(f)) if f.is_integer() else f"{f:.10g}"
    text = str(value).strip()
    return "" if text.lower() in ("nan", "none", "nat") else text


def _map_columns(row_values):
    """{field: column position} for one candidate header row (first matching column wins)."""
    headers = [norm(v) for v in row_values]
    found = {}
    for field, names in FIELD_HEADERS.items():
        for name in names:                                       # exact header match, in priority order
            if name in headers:
                found[field] = headers.index(name)
                break
    return found


def _find_header(raw):
    for r in range(min(HEADER_SCAN_ROWS, len(raw))):
        found = _map_columns(list(raw.iloc[r]))
        if all(f in found for f in REQUIRED):
            return r, found
    return None, {}


def parse_ftl_report(path, sheet_name=0):
    try:
        # dtype=object: keep every cell as Excel stores it (no int -> float coercion of columns that have blanks)
        raw = pd.read_excel(path, sheet_name=sheet_name, header=None, dtype=object)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Report Excel read nahi ho paayi: {exc}") from exc

    hdr, idx = _find_header(raw)
    if hdr is None:
        raise ValueError("FTL report me 'B.CODE' aur 'CN' (CN_CN_NO) wale columns nahi mile - "
                         "header row check karein (pehli 30 rows me hona chahiye).")

    col_ids = [i for i in range(raw.shape[1]) if cell_text(raw.iat[hdr, i])]
    columns = [cell_text(raw.iat[hdr, i]) for i in col_ids]
    stats = dict(rows=0, kept=0, other_load=0, blank_cn=0, duplicates=0, no_branch=0)
    branches, seen = {}, set()

    def val(row, field):
        return cell_text(row.iloc[idx[field]]) if field in idx else ""

    for r in range(hdr + 1, len(raw)):
        row = raw.iloc[r]
        cells = [cell_text(row.iloc[i]) for i in col_ids]
        if not any(cells):
            continue                                             # empty line
        code = _safe_branch_code(row.iloc[idx["branch_code"]]) if not _is_blank(row.iloc[idx["branch_code"]]) else ""
        if norm(code) in FIELD_HEADERS["branch_code"]:
            continue                                             # header repeated on a new page of the export
        stats["rows"] += 1
        if not code:
            stats["no_branch"] += 1
            continue
        if "load_type" in idx and norm(val(row, "load_type")) != "FTL":
            stats["other_load"] += 1                             # only FTL dockets go into this mail
            continue
        cn = val(row, "cn")
        if not cn:
            stats["blank_cn"] += 1
            continue
        if (code, cn) in seen:
            stats["duplicates"] += 1
            continue
        seen.add((code, cn))
        b = branches.setdefault(code, dict(branch_code=code, branch_name_sheet="", region="", columns=columns, grs=[]))
        b["branch_name_sheet"] = b["branch_name_sheet"] or val(row, "branch_name")
        b["region"] = b["region"] or val(row, "region")
        b["grs"].append(dict(cn=cn, cn_date=val(row, "cn_date"), party_code=val(row, "party_code"),
                             party_name=val(row, "party_name"), cells=cells))
        stats["kept"] += 1
    return branches, stats


def _is_blank(value) -> bool:
    return value is None or (not isinstance(value, str) and pd.isna(value)) or (isinstance(value, str) and not value.strip())
