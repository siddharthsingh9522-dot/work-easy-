# -*- coding: utf-8 -*-
"""Branch/employee directory loader.

Supports .xlsx and legacy .xls.  Legacy files are converted to a temporary
.xlsx with LibreOffice when available; xlrd is used as a fallback.
"""
import os
import shutil
import subprocess
import tempfile
import pandas as pd
import openpyxl
from .config import DIRECTORY_HEADER_MARKERS


def _ensure_xlsx(path: str) -> str:
    if not str(path).lower().endswith(".xls") or str(path).lower().endswith(".xlsx"):
        return path
    tmp = tempfile.mkdtemp(prefix="om_dir_")
    soffice = shutil.which("libreoffice") or shutil.which("soffice")
    if soffice:
        result = subprocess.run(
            [soffice, "--headless", "--convert-to", "xlsx", "--outdir", tmp, path],
            capture_output=True, text=True, timeout=90
        )
        out = os.path.join(tmp, os.path.splitext(os.path.basename(path))[0] + ".xlsx")
        if result.returncode == 0 and os.path.exists(out):
            return out
    # If LibreOffice is not installed, pandas+xlrd can still read old .xls.
    return path


def _find_header_row_from_values(values, max_scan=8):
    for r, row in enumerate(values[:max_scan], start=1):
        row_str = [str(v).strip().upper().replace("\n", " ") for v in row if v is not None]
        joined = " ".join(row_str)
        if any(mk.upper().replace("\n", " ") in joined for mk in DIRECTORY_HEADER_MARKERS):
            return r
    return 2


def _normalise_columns(df):
    df.columns = [str(c).strip().upper().replace("\n", " ") for c in df.columns]
    colmap = {}
    for c in df.columns:
        if "BRANCH CODE" in c or c in ("B.CODE", "B CODE", "BCODE"):
            colmap[c] = "BRANCH_CODE"
        elif c in ("STATE/ BRANCH", "STATE/BRANCH", "STATE BRANCH", "BRANCH NAME"):
            colmap[c] = "STATE_BRANCH"
        elif "EMP" in c and "CODE" in c:
            colmap[c] = "EMP_CODE"
        elif c in ("OFFICIAL", "NAME", "EMPLOYEES NAME", "EMPLOYEE NAME"):
            colmap[c] = "NAME"
        elif "DESIGNATION" in c:
            colmap[c] = "DESIGNATION"
        elif "MOBILE" in c or "CONTACT NO" in c:
            colmap[c] = "MOBILE"
        elif "E-MAIL" in c or "EMAIL" in c:
            colmap[c] = "EMAIL"
        elif "DEPARTMENT" in c:
            colmap[c] = "STATE_BRANCH"
    return df.rename(columns=colmap)


def load_directory(path: str) -> pd.DataFrame:
    path2 = _ensure_xlsx(path)
    engine = None
    if str(path2).lower().endswith(".xls") and not str(path2).lower().endswith(".xlsx"):
        engine = "xlrd"

    try:
        xl = pd.ExcelFile(path2, engine=engine)
    except Exception as e:
        raise RuntimeError(
            "Directory read nahi hui. .xls ke liye LibreOffice ya xlrd install hona chahiye. "
            f"Original error: {e}"
        )

    all_rows = []
    for sheet_name in xl.sheet_names:
        try:
            raw = pd.read_excel(path2, sheet_name=sheet_name, header=None, engine=engine)
            hdr_row = _find_header_row_from_values(raw.values.tolist())
            df = pd.read_excel(path2, sheet_name=sheet_name, header=hdr_row - 1, engine=engine)
            df = _normalise_columns(df)
        except Exception:
            continue

        keep = [c for c in ["STATE_BRANCH", "BRANCH_CODE", "EMP_CODE", "NAME",
                            "DESIGNATION", "MOBILE", "EMAIL"] if c in df.columns]
        if not keep:
            continue
        df = df[keep].copy()
        df["SHEET"] = sheet_name
        if "BRANCH_CODE" in df.columns:
            df["BRANCH_CODE"] = pd.to_numeric(df["BRANCH_CODE"], errors="coerce")
            df["BRANCH_CODE"] = df["BRANCH_CODE"].ffill()
        if "STATE_BRANCH" in df.columns:
            df["STATE_BRANCH"] = df["STATE_BRANCH"].replace({"": pd.NA}).ffill()
        if "NAME" in df.columns:
            df = df.dropna(subset=["NAME"])
        all_rows.append(df)

    cols = ["SHEET", "STATE_BRANCH", "BRANCH_CODE", "EMP_CODE", "NAME",
            "DESIGNATION", "MOBILE", "EMAIL"]
    if not all_rows:
        return pd.DataFrame(columns=cols)
    out = pd.concat(all_rows, ignore_index=True)
    out["BRANCH_CODE"] = pd.to_numeric(out["BRANCH_CODE"], errors="coerce")
    return out


def branch_lookup(directory_df: pd.DataFrame, branch_code) -> pd.DataFrame:
    if directory_df is None or directory_df.empty or "BRANCH_CODE" not in directory_df.columns:
        return pd.DataFrame()
    try:
        code = int(float(str(branch_code).strip()))
    except (TypeError, ValueError):
        return pd.DataFrame()
    return directory_df[pd.to_numeric(directory_df["BRANCH_CODE"], errors="coerce") == code]


def branch_code_to_name_map(directory_df: pd.DataFrame) -> dict:
    if directory_df is None or directory_df.empty or "BRANCH_CODE" not in directory_df.columns:
        return {}
    out = {}
    # Prefer a real branch/in-charge row over random employee rows.
    for code, grp in directory_df.dropna(subset=["BRANCH_CODE"]).groupby(
        pd.to_numeric(directory_df.dropna(subset=["BRANCH_CODE"])["BRANCH_CODE"], errors="coerce").astype(int)
    ):
        names = [str(x).strip() for x in grp.get("STATE_BRANCH", pd.Series(dtype=object)).dropna()
                 if str(x).strip()]
        if names:
            out[int(code)] = names[0]
    return out


def find_contacts(directory_df: pd.DataFrame, branch_code) -> pd.DataFrame:
    """Return unique useful email contacts for a branch."""
    df = branch_lookup(directory_df, branch_code).copy()
    if df.empty:
        return df
    if "EMAIL" in df.columns:
        df["EMAIL"] = df["EMAIL"].fillna("").astype(str).str.strip()
        df = df[df["EMAIL"].str.contains("@", regex=False)]
        df = df.drop_duplicates(subset=["EMAIL"])
    return df
