"""The branch -> contacts directory used by TBB / Bill mail: the SAME file the admin manages in Directory & Masters."""
import os
import threading

from dbp import paths

from . import directory_parser

_lock = threading.Lock()
_cache = {"sig": None, "val": {}}


def load() -> dict:
    d = paths.master_dir()
    for name in ("directory.xlsx", "directory.xls"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            sig = (p, os.stat(p).st_mtime_ns)
            with _lock:
                if _cache["sig"] != sig:
                    _cache.update(sig=sig, val=directory_parser.parse_directory(p))
                return _cache["val"]
    return {}
