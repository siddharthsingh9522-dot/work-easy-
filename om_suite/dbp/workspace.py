"""
Per-user server-side workspace (replaces Streamlit's session_state).

Everything for one user lives in  <DATA_DIR>/workspaces/<user id>/ :
    state.pkl   - the generated DBP report state (categorized data, summary, chat history ...)
    ms_<x>.pkl  - ingested data of the month-wise tool, one per mode (adr / net / full)
    uploads/    - raw files of the Report Builder
    ms/<x>/     - raw files of the month-wise tool
    out/        - generated Excel files
Only the server ever writes these pickles (never user-supplied bytes), and old workspaces are purged.
"""
import os
import pickle
import shutil
import tempfile
import threading
import time
from pathlib import Path

from werkzeug.utils import secure_filename

ALLOWED_EXT = (".xlsx", ".xlsm")
MAX_FILES = 25
KEEP_DAYS = 7
_cache, _cache_lock = {}, threading.Lock()   # (path) -> (mtime, obj)   tiny per-process unpickle cache


class UploadError(Exception):
    pass


class Workspace:
    def __init__(self, root, uid):
        self.dir = Path(root) / "workspaces" / uid
        self.dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- pickles
    def _load(self, name, default):
        p = self.dir / name
        if not p.exists():
            return default
        m = p.stat().st_mtime_ns
        with _cache_lock:
            hit = _cache.get(str(p))
            if hit and hit[0] == m:
                return hit[1]
        with open(p, "rb") as f:
            obj = pickle.load(f)
        with _cache_lock:
            _cache[str(p)] = (m, obj)
            if len(_cache) > 12:
                _cache.pop(next(iter(_cache)))
        return obj

    def _save(self, name, obj):
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        with os.fdopen(fd, "wb") as f:
            pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, self.dir / name)
        with _cache_lock:
            _cache.pop(str(self.dir / name), None)

    def state(self) -> dict:
        return self._load("state.pkl", {})

    def save_state(self, st: dict):
        self._save("state.pkl", st)

    def update_state(self, **kw) -> dict:
        st = dict(self.state())
        st.update(kw)
        self.save_state(st)
        return st

    def ms_get(self, key):
        return self._load(f"ms_{key}.pkl", None)

    def ms_put(self, key, obj):
        self._save(f"ms_{key}.pkl", obj)

    def blob_get(self, name, default=None):
        return self._load(f"blob_{name}.pkl", default)

    def blob_put(self, name, obj):
        self._save(f"blob_{name}.pkl", obj)

    def blob_del(self, name):
        try:
            os.remove(self.dir / f"blob_{name}.pkl")
        except OSError:
            pass

    # ---------------------------------------------------------------- files
    def save_uploads(self, files, subdir="uploads"):
        """Replace the folder with the freshly uploaded .xlsx files; returns saved paths."""
        files = [f for f in files if f and f.filename]
        if not files:
            raise UploadError("Koi file select nahi hui.")
        if len(files) > MAX_FILES:
            raise UploadError(f"Ek baar me maximum {MAX_FILES} files.")
        target = self.dir / subdir
        tmp = self.dir / (subdir.replace("/", "_") + ".new")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        paths, seen = [], set()
        for f in files:
            name = secure_filename(f.filename) or "upload.xlsx"
            if not name.lower().endswith(ALLOWED_EXT):
                shutil.rmtree(tmp, ignore_errors=True)
                raise UploadError(f"'{f.filename}' Excel (.xlsx) file nahi hai.")
            base, n = name, 1
            while name in seen:
                name = f"{Path(base).stem}_{n}{Path(base).suffix}"
                n += 1
            seen.add(name)
            p = tmp / name
            f.save(p)
            paths.append(name)
        shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, target)
        return [str(target / n) for n in paths]

    def out_path(self, filename) -> str:
        d = self.dir / "out"
        d.mkdir(exist_ok=True)
        return str(d / secure_filename(filename))

    def clear(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True, exist_ok=True)


def purge_old(root, days=KEEP_DAYS):
    base = Path(root) / "workspaces"
    if not base.exists():
        return
    cutoff = time.time() - days * 86400
    for d in base.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass
