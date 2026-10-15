"""
In-process background jobs with progress, log and a result file - used by every long-running tool
(Branch bulk, Customer bulk, OM Automation batches).

Jobs belong to ONE user (owner) and are only visible to them. They live in memory of the web process
(the app is deployed with a single gunicorn worker, see Dockerfile) and outputs are written under
<DATA_DIR>/jobs/<job id>/. Old jobs and their files are purged after 24 hours.
"""
import os
import shutil
import threading
import time
import traceback
import uuid

_LOG_MAX = 2000
_root = None


def configure(data_dir):
    global _root
    _root = os.path.join(str(data_dir), "jobs")
    os.makedirs(_root, exist_ok=True)


def save_upload(file_storage, suffix):
    """Save an upload BEFORE the job starts (the worker thread must find the file ready)."""
    d = os.path.join(_root, "_uploads")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, uuid.uuid4().hex + suffix)
    file_storage.save(path)
    return path


class Job:
    def __init__(self, owner, kind, meta=None):
        self.id = uuid.uuid4().hex[:12]
        self.owner, self.kind, self.meta = owner, kind, dict(meta or {})
        self.status = "running"            # running | done | error | stopped
        self.current = self.total = 0
        self.lines, self.error = [], None
        self.output_name = None
        self.created = self.updated = time.time()
        self.stop_event = threading.Event()
        self.extra = {}                    # tool-specific counters (found / not_found ...)
        self._lock = threading.Lock()
        self.dir = os.path.join(_root, self.id)
        os.makedirs(self.dir, exist_ok=True)

    # ---- helpers for job functions
    def log(self, message):
        with self._lock:
            self.lines.append(str(message))
            if len(self.lines) > _LOG_MAX:
                self.lines = self.lines[-_LOG_MAX:]
            self.updated = time.time()

    def progress(self, current, total=None):
        with self._lock:
            self.current = current
            if total is not None:
                self.total = total
            self.updated = time.time()

    def bump(self, key, n=1):
        with self._lock:
            self.extra[key] = self.extra.get(key, 0) + n

    @property
    def stopped(self):
        return self.stop_event.is_set()

    def path(self, filename):
        return os.path.join(self.dir, os.path.basename(filename))

    # ---- state for the browser
    def snapshot(self, log_from=0):
        with self._lock:
            return dict(job_id=self.id, status=self.status, current=self.current, total=self.total,
                        log=self.lines[log_from:], log_count=len(self.lines), error=self.error,
                        output_filename=self.output_name, extra=dict(self.extra),
                        download_url=f"/jobs/{self.id}/download" if self.output_name else None)


_jobs, _lock = {}, threading.Lock()


def start(owner, kind, target, meta=None) -> Job:
    """target(job) runs in a daemon thread. Return normally -> done (or stopped if stop was requested)."""
    purge()
    job = Job(owner, kind, meta)
    with _lock:
        _jobs[job.id] = job

    def run():
        try:
            target(job)
            job.status = "stopped" if job.stopped else "done"
        except Exception as e:  # noqa: BLE001 - show the reason in the UI
            job.error = f"{type(e).__name__}: {e}"
            job.log(f"Error: {job.error}")
            job.status = "error"
            traceback.print_exc()
        job.updated = time.time()

    threading.Thread(target=run, daemon=True, name=f"job-{job.id}").start()
    return job


def get(job_id, owner):
    with _lock:
        job = _jobs.get(job_id)
    return job if job and job.owner == owner else None


def stop(job_id, owner) -> bool:
    job = get(job_id, owner)
    if job:
        job.stop_event.set()
    return bool(job)


def purge(max_age=24 * 3600):
    if _root is None:
        return
    cutoff = time.time() - max_age
    with _lock:
        old = [j for j in _jobs.values() if j.updated < cutoff and j.status != "running"]
        for j in old:
            _jobs.pop(j.id, None)
    for j in old:
        shutil.rmtree(j.dir, ignore_errors=True)
