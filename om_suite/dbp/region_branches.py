# -*- coding: utf-8 -*-
"""
region_branches.py
Har region ki branch-codes ki ORDERED list save/load karta hai
(data/masters/region_branches.json), taaki month-wise report mein woh
branches bhi aayein jinka pending ZERO hai -- jaise user ke reference
report mein 501 BANGLORE AIRPORT, 513, 514 ... zero rows ke saath dikhti
hain. Raw export mein sirf wahi branches hoti hain jinka data hai, isliye
yeh list alag se deni padti hai: pichli report upload karke (B.CODE column
padh li jaati hai), ya codes paste karke -- ek baar save, agli baar auto.
"""
import json
import os
import re

import pandas as pd

from . import paths as _paths


def _store_path():
    return os.path.join(_paths.master_dir(), "region_branches.json")
_CODE_HEADERS = {"BCODE", "BRANCHCODE", "BRCODE", "CODE", "BRANCH"}


def _key(region: str) -> str:
    return re.sub(r"\s+", " ", str(region or "").strip().upper())


def _norm_code(v):
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    if f != f or f != int(f) or f <= 0:
        return None
    return str(int(f))


def _load_all() -> dict:
    try:
        with open(_store_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get(region: str) -> list:
    return list(_load_all().get(_key(region), []))


def save(region: str, codes: list) -> bool:
    codes = _dedupe(codes)
    if not _key(region) or not codes:
        return False
    try:
        data = _load_all()
        data[_key(region)] = codes
        os.makedirs(os.path.dirname(_store_path()), exist_ok=True)
        with open(_store_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        return True
    except Exception:
        return False


def delete(region: str) -> bool:
    try:
        data = _load_all()
        if _key(region) in data:
            del data[_key(region)]
            with open(_store_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
        return True
    except Exception:
        return False


def _dedupe(codes) -> list:
    out = []
    for c in codes or []:
        nc = _norm_code(c)
        if nc is not None and nc not in out:
            out.append(nc)
    return out


def parse_codes_text(text: str) -> list:
    """'501, 502\\n503 504;505' -> ['501','502','503','504','505'] (order
    preserved, duplicates/non-numeric ignored)."""
    return _dedupe(re.split(r"[\s,;|]+", str(text or "")))


def codes_from_excel(path_or_file) -> list:
    """Kisi bhi Excel (jaise pichli month-wise report) se branch-codes padhta
    hai: pehle 15 rows mein 'B.CODE'/'BRANCH CODE' header dhundhta hai aur uske
    neeche ka column order mein leta hai (naam wali party-rows/TOTAL skip).
    Header na mile to sabse zyada numeric values wala column leta hai."""
    try:
        sheets = pd.read_excel(path_or_file, sheet_name=None, header=None, dtype=str)
    except Exception:
        return []
    # Har sheet se header-based list nikaalo; jis sheet mein SABSE zyada
    # branch-codes hon (jaise SUMMARY, kisi raw sheet ke sirf data-wali
    # branches nahi) wahi region ki asli list maani jaati hai.
    header_lists = []
    for _, df in sheets.items():
        if df is None or df.empty:
            continue
        found = False
        for r in range(min(15, len(df))):
            for c in range(df.shape[1]):
                cell = re.sub(r"[^A-Z]", "", str(df.iat[r, c]).upper())
                if cell in _CODE_HEADERS:
                    codes = _dedupe(df.iloc[r + 1:, c].tolist())
                    if codes:
                        header_lists.append(codes)
                        found = True
                        break
            if found:
                break
    if header_lists:
        return max(header_lists, key=len)
    best = []
    for _, df in sheets.items():
        for c in range(df.shape[1]):
            codes = _dedupe(df.iloc[:, c].tolist())
            if len(codes) > len(best):
                best = codes
    return best
