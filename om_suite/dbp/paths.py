"""Where DBP master data (directory, branch master, saved region lists) lives at runtime."""
import os
import shutil

SEED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "masters_seed")


def master_dir() -> str:
    d = os.environ.get("DBP_MASTER_DIR") or os.path.join(os.path.dirname(SEED_DIR), "..", "data", "masters")
    d = os.path.abspath(d)
    os.makedirs(d, exist_ok=True)
    return d


def ensure_seeded() -> str:
    """Copy the bundled directory/branch master into the runtime folder the first time (never overwrites)."""
    d = master_dir()
    if os.path.isdir(SEED_DIR):
        for name in os.listdir(SEED_DIR):
            target = os.path.join(d, name)
            if not os.path.exists(target) and not (name.startswith("directory.") and
                                                   any(os.path.exists(os.path.join(d, "directory" + e)) for e in (".xlsx", ".xls"))):
                shutil.copy2(os.path.join(SEED_DIR, name), target)
    return d
