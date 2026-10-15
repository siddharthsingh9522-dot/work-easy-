"""Directory + branch-master loading (cached by file mtime) and admin replacement."""
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, field

import pandas as pd

from . import directory_loader, ingest, paths

_lock = threading.Lock()
_cache = {"sig": None, "val": None}


@dataclass
class Masters:
    directory_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    branch_master_map: dict = field(default_factory=dict)
    directory_path: str = ""
    errors: list = field(default_factory=list)


def _paths():
    d = paths.master_dir()
    dpath = next((os.path.join(d, n) for n in ("directory.xlsx", "directory.xls")
                  if os.path.exists(os.path.join(d, n))), None)
    bpath = os.path.join(d, "branch_master.xlsx")
    return d, dpath, bpath if os.path.exists(bpath) else None


def _sig(dpath, bpath):
    return tuple((p, os.stat(p).st_mtime_ns) if p else (None, 0) for p in (dpath, bpath))


def load() -> Masters:
    _, dpath, bpath = _paths()
    sig = _sig(dpath, bpath)
    with _lock:
        if _cache["sig"] == sig and _cache["val"] is not None:
            return _cache["val"]
        m = Masters(directory_path=dpath or "")
        if dpath:
            try:
                m.directory_df = directory_loader.load_directory(dpath)
            except Exception as e:  # noqa: BLE001 - surface the reason on the Directory page
                m.errors.append(f"Directory load nahi hui: {e}")
        if bpath:
            try:
                m.branch_master_map = ingest.load_branch_master(bpath)
            except Exception as e:  # noqa: BLE001
                m.errors.append(f"Branch master load nahi hua: {e}")
        _cache.update(sig=sig, val=m)
        return m


def replace_directory(fileobj, filename):
    """Validates the new file BEFORE replacing the current one. Returns number of rows."""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".xlsx", ".xls"):
        raise ValueError("Directory .xlsx ya .xls honi chahiye.")
    d = paths.master_dir()
    fd, tmp = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    try:
        fileobj.save(tmp)
        rows = len(directory_loader.load_directory(tmp))
        if rows == 0:
            raise ValueError("Is file me koi directory row nahi mili.")
        for old in ("directory.xlsx", "directory.xls"):
            p = os.path.join(d, old)
            if os.path.exists(p):
                os.remove(p)
        shutil.copy2(tmp, os.path.join(d, "directory" + ext))
        return rows
    finally:
        os.remove(tmp)


def replace_branch_master(fileobj, filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".xlsx", ".xls"):
        raise ValueError("Branch master .xlsx ya .xls honi chahiye.")
    fd, tmp = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    try:
        fileobj.save(tmp)
        n = len(ingest.load_branch_master(tmp))
        if n == 0:
            raise ValueError("Is file me koi branch code nahi mila.")
        shutil.copy2(tmp, os.path.join(paths.master_dir(), "branch_master.xlsx"))
        return n
    finally:
        os.remove(tmp)
