"""
Workflow rows + 'operations' state machine of the three mail tools, kept in the user's workspace
(replaces om-suite's SQL tables workflow_data / mail_operations - same rules):

  new/changed content   -> fresh DRAFT          same content again -> row untouched (a SENT mail is never re-sent)
  confirm(selection)    -> ticked DRAFT -> QUEUED, the rest of the DRAFTs -> SKIPPED (never sent)
  send                  -> SENDING -> SENT | FAILED   retry_failed: FAILED -> QUEUED (SENT/SKIPPED untouched)
"""
import hashlib
import threading
import time
from collections import defaultdict

from core import bridge_queue as bq

REVIEW_OK = ("ok", "manual_ok")
_locks = defaultdict(threading.RLock)


def fingerprint(module, code, email, text):
    return hashlib.sha256(f"{module}:{code}:{email}:{text}".encode("utf-8")).hexdigest()


class Flow:
    def __init__(self, ws, module, owner):
        self.ws, self.module, self.owner = ws, module, owner
        self.lock = _locks[(str(ws.dir), module)]

    # ---- storage
    def branches(self) -> dict:
        return dict((self.ws.blob_get(f"wf_{self.module}") or {}).get("branches", {}))

    def source(self) -> str:
        return (self.ws.blob_get(f"wf_{self.module}") or {}).get("source", "")

    def ops(self) -> dict:
        return dict(self.ws.blob_get(f"ops_{self.module}") or {})

    def _put_ops(self, ops):
        self.ws.blob_put(f"ops_{self.module}", ops)

    def _put_branches(self, branches, source=None):
        cur = self.ws.blob_get(f"wf_{self.module}") or {}
        self.ws.blob_put(f"wf_{self.module}", {"branches": branches, "source": source if source is not None else cur.get("source", ""),
                                                "saved": time.time()})

    # ---- upload -> rows + idempotent operations
    def load(self, branches: dict, source: str, fingerprints: dict):
        with self.lock:
            self._put_branches(branches, source)
            ops, now = self.ops(), time.time()
            for code, fp in fingerprints.items():
                cur = ops.get(code)
                if cur and cur.get("fingerprint") == fp:
                    continue                      # same content as before (draft / queued / SENT): leave it alone
                ops[code] = dict(fingerprint=fp, selected=1, status="DRAFT", attempts=0, error="", job_id=None, updated=now)
            self._put_ops(ops)
            return ops

    def set_fields(self, code, **fields):
        with self.lock:
            branches = self.branches()
            if code in branches:
                branches[code].update(fields)
                self._put_branches(branches)

    def confirm(self, selected_codes):
        with self.lock:
            ops, chosen, now = self.ops(), set(selected_codes), time.time()
            for code, op in ops.items():
                if op["status"] != "DRAFT":
                    continue                      # only DRAFT rows are affected by (re-)confirming
                op.update(status="QUEUED" if code in chosen else "SKIPPED", selected=int(code in chosen), updated=now)
            self._put_ops(ops)

    def _set(self, code, **kw):
        with self.lock:
            ops = self.ops()
            if code in ops:
                ops[code].update(kw, updated=time.time())
                self._put_ops(ops)

    def mark_sending(self, code, job_id=None):
        self._set(code, status="SENDING", job_id=job_id)

    def mark_sent(self, code):
        op = self.ops().get(code, {})
        self._set(code, status="SENT", error="", attempts=op.get("attempts", 0) + 1)

    def mark_failed(self, code, error):
        op = self.ops().get(code, {})
        self._set(code, status="FAILED", error=str(error)[:500], attempts=op.get("attempts", 0) + 1)

    def retry_failed(self):
        with self.lock:
            ops, retried = self.ops(), []
            for code, op in ops.items():
                if op["status"] == "FAILED":
                    op.update(status="QUEUED", updated=time.time())
                    retried.append(code)
            self._put_ops(ops)
            return retried

    # ---- Thunderbird results arrive asynchronously: fold them into the rows whenever someone looks
    def refresh_from_bridge(self):
        with self.lock:
            ops = self.ops()
            waiting = {c: o["job_id"] for c, o in ops.items() if o["status"] == "SENDING" and o.get("job_id")}
            if not waiting:
                return ops
            states = bq.job_states(self.owner, waiting.values())
            for code, job_id in waiting.items():
                st, err = states.get(int(job_id), (None, ""))
                if st == "DONE":
                    ops[code].update(status="SENT", error="", attempts=ops[code].get("attempts", 0) + 1, updated=time.time())
                elif st == "ERROR":
                    ops[code].update(status="FAILED", error=(err or "Thunderbird send failed")[:500],
                                     attempts=ops[code].get("attempts", 0) + 1, updated=time.time())
                elif st == "CANCELLED":
                    ops[code].update(status="QUEUED", job_id=None, updated=time.time())
            self._put_ops(ops)
            return ops

    def counts(self, branches=None, ops=None):
        branches = self.branches() if branches is None else branches
        ops = self.ops() if ops is None else ops
        status = lambda c: ops.get(c, {}).get("status", "DRAFT")  # noqa: E731
        return {"generated": len(branches), **{k.lower(): sum(1 for c in branches if status(c) == k)
                                              for k in ("DRAFT", "QUEUED", "SKIPPED", "SENDING", "SENT", "FAILED")}}
