"""TBB Party Mail, Bill Generated Mail and Previous Mail & Format: upload -> review -> select -> send."""
import os
import time
from datetime import datetime
from functools import partial, wraps

import pandas as pd
from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for)

from core import bridge_queue as bq
from core.auth import login_required, store
from core.modules import can_access
from dbp import mail_sender, mailcfg
from dbp.workspace import Workspace
from mailflows import ai, bill_parser, builders, directories, directory_parser, emailfix, previous_mail
from mailflows.engine import REVIEW_OK, Flow, fingerprint

bp = Blueprint("flows", __name__, url_prefix="/mail")

FLOWS = {
    "tbb": dict(label="TBB Party Mail", module_id="tbb_mail", ai_label="TBB Party GR Verification", item="GR"),
    "bill": dict(label="Bill Generated Mail", module_id="bill_mail", ai_label="Bill Generated Mail", item="Bill"),
    "previous": dict(label="Previous Mail & Format", module_id="previous_mail", ai_label="Previous Mail & Format", item="Row"),
}
EXCEL_EXT = (".xls", ".xlsx", ".xlsm")
MAX_SMTP = mailcfg.MAX_SMTP_BATCH


# ------------------------------------------------------------------ plumbing
def flow_view(view):
    """Resolve <slug>, check login + the tool's module access, hand over (slug, Flow)."""
    @wraps(view)
    @login_required
    def wrapped(slug, *a, **kw):
        cfg = FLOWS.get(slug)
        if not cfg:
            abort(404)
        if not can_access(g.user, cfg["module_id"]):
            abort(403)
        g.active_module = cfg["module_id"]
        return view(slug, *a, **kw)
    return wrapped


def ws():
    return Workspace(current_app.config["DATA_DIR"], g.user["id"])


def flow_for(slug):
    return Flow(ws(), slug, g.user["email"])


def _from_name():
    return g.user["name"]


def _ext_ok(f, allowed):
    return bool(f and f.filename and os.path.splitext(f.filename)[1].lower() in allowed)


def _save_upload(f, name):
    d = ws().dir / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    path = d / (name + os.path.splitext(f.filename)[1].lower())
    f.save(path)
    return str(path)


def _summary(slug, b):
    if slug == "tbb":
        grs = b.get("grs", [])
        return len(grs), [f"{g_['cn']} · {g_['party_name']}" for g_ in grs[:3]]
    if slug == "bill":
        bills = b.get("bills", [])
        return len(bills), [str(x.get("bill_no", "")) for x in bills[:3]]
    rows = b.get("rows", [])
    return len(rows), [b.get("subject", "")[:80]]


def _title(slug, code, b):
    return b.get("branch_name_sheet", "") if slug != "previous" else b.get("key", code)


def rows_for(slug, flow):
    ops = flow.refresh_from_bridge()
    branches = flow.branches()
    out = []
    for code, b in branches.items():
        op = ops.get(code, {})
        n, sample = _summary(slug, b)
        out.append(dict(
            code=code, name=_title(slug, code, b), region=b.get("region", ""), items=n, sample=sample,
            email=b.get("email") or "", cc=b.get("cc") or "", bcc=b.get("bcc") or "", contact=b.get("contact_name") or "",
            designation=b.get("designation") or "", review=b.get("ai_status") or "pending", reason=b.get("ai_reason") or "",
            status=op.get("status", "DRAFT"), selected=bool(op.get("selected", 1)), attempts=op.get("attempts", 0),
            error=op.get("error", "")))
    return out, flow.counts(branches, ops)


def _json_or_400(slug, code):
    flow = flow_for(slug)
    branch = flow.branches().get(code)
    if not branch:
        abort(404)
    return flow, branch


def _mail(slug, code, branch, to, cc, bcc, test_to=None):
    """The mail dict for sending (dbp.mail_sender / bridge format). Test mode swaps every recipient for the test address."""
    mail = builders.build_mail(slug, code, branch, _from_name())
    mail.update(branch_code=code, branch_name=_title(slug, code, branch) or code, ref={"module": slug, "code": code})
    if test_to:
        mail.update(to_list=test_to[:1], cc_list=[], bcc_list=[], subject="[TEST] " + mail["subject"])
    else:
        mail.update(to_list=builders.parse_recipients(to), cc_list=builders.parse_recipients(cc),
                    bcc_list=builders.parse_recipients(bcc))
        if not mail["to_list"]:
            raise ValueError("At least one valid recipient email is required.")
    return mail


# ------------------------------------------------------------------ pages
def _index(slug):
    return flow_view(lambda s: _index_view(s))(slug)


def _index_view(slug):
    flow = flow_for(slug)
    return render_template("flows/index.html", slug=slug, cfg=FLOWS[slug], has=bool(flow.branches()),
                           source=flow.source(), dir_ok=bool(directories.load()), ai_on=ai.enabled(),
                           draft=ws().blob_get("previous_draft") if slug == "previous" else None)


for _slug in FLOWS:
    bp.add_url_rule(f"/{_slug}/", endpoint=f"{_slug}_index", view_func=partial(_index, _slug))


@bp.route("/<slug>/preview")
@flow_view
def preview(slug):
    flow = flow_for(slug)
    rows, counts = rows_for(slug, flow)
    if not rows:
        return redirect(url_for(f"flows.{slug}_index"))
    smtp_ok = mailcfg.saved(current_app.config, g.user) is not None
    return render_template("flows/preview.html", slug=slug, cfg=FLOWS[slug], rows=rows, counts=counts, ai_on=ai.enabled(),
                           smtp_ok=smtp_ok, source=flow.source(), default_test_to=g.user["email"])


# ------------------------------------------------------------------ upload (TBB / Bill)
@bp.route("/<slug>/upload", methods=["POST"])
@flow_view
def upload(slug):
    if slug not in ("tbb", "bill"):
        abort(404)
    f = request.files.get("report_file")
    if not f or not f.filename:
        flash("Report file upload karna zaroori hai.", "error")
        return redirect(url_for(f"flows.{slug}_index"))
    if not _ext_ok(f, EXCEL_EXT):
        flash("Sirf .xls ya .xlsx files allowed hain.", "error")
        return redirect(url_for(f"flows.{slug}_index"))
    directory = directories.load()
    if not directory:
        flash("Directory load nahi hai - admin se Directory & Masters me upload karwayein.", "error")
        return redirect(url_for(f"flows.{slug}_index"))
    path = _save_upload(f, f"{slug}_report")
    try:
        if slug == "tbb":
            branches = directory_parser.build_branches_with_email(directory_parser.parse_report(path), directory)
        else:
            branches = bill_parser.parse_bill_report(path)
            for bc, b in branches.items():
                email, name, desig = directory_parser.pick_branch_email(directory, bc)
                b.update(email=email, contact_name=name, designation=desig)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for(f"flows.{slug}_index"))
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("%s report parse failed", slug)
        flash(f"Report padhne me dikkat: {e}", "error")
        return redirect(url_for(f"flows.{slug}_index"))
    if not branches:
        flash("Is report me koi branch ka data nahi mila.", "error")
        return redirect(url_for(f"flows.{slug}_index"))
    for b in branches.values():
        b.update(ai_status="pending", ai_reason="", cc="", bcc="")
    fps = {c: fingerprint(slug, c, b.get("email") or "", builders.fingerprint_text(slug, b, _from_name()))
           for c, b in branches.items()}
    flow_for(slug).load(branches, os.path.basename(f.filename), fps)
    store().add_audit(g.user["email"], f"flow:{slug}:upload", f"{len(branches)} branches")
    return redirect(url_for("flows.preview", slug=slug))


# ------------------------------------------------------------------ Previous Mail & Format: analyze -> review -> confirm
@bp.route("/previous/analyze", methods=["POST"])
@login_required
def previous_analyze():
    if not can_access(g.user, FLOWS["previous"]["module_id"]):
        abort(403)
    g.active_module = "previous_mail"
    back = redirect(url_for("flows.previous_index"))
    data_file, eml_file = request.files.get("data_file"), request.files.get("eml_file")
    if not _ext_ok(data_file, EXCEL_EXT + (".csv",)):
        flash("Naya data sheet (.xls / .xlsx / .csv) upload karna zaroori hai.", "error")
        return back
    path = _save_upload(data_file, "previous_data")
    try:
        if eml_file and eml_file.filename:
            old = previous_mail.parse_eml_bytes(eml_file.read())
        else:
            old = previous_mail.parse_manual_fields(*(request.form.get(k, "") for k in
                                                      ("old_to", "old_cc", "old_bcc", "old_subject", "old_body")))
            if not old["subject"] and not old["body"]:
                flash("Purani mail ka .eml upload karein YA subject/body manually likhein.", "error")
                return back
        df = previous_mail.parse_new_data(path)
    except Exception as e:  # noqa: BLE001
        flash(f"File padhne me dikkat: {e}", "error")
        return back
    columns = list(df.columns)
    if not columns or df.empty:
        flash("Data sheet me koi column/row nahi mila.", "error")
        return back
    sample = {c: ("" if pd.isna(df.iloc[0][c]) else df.iloc[0][c]) for c in columns}
    res = ai.extract_template(old["subject"], old["body"], sample, columns)
    ws().blob_put("previous_draft", dict(
        old_mail=old, data_path=path, columns=columns, sample_row=sample, subject_template=res["subject_template"],
        body_template=res["body_template"], ai_confidence=res["confidence"], ai_notes=res["notes"],
        key_col=next((c for c in columns if "branch" in c.lower()), columns[0])))
    return redirect(url_for("flows.previous_review"))


@bp.route("/previous/review")
@login_required
def previous_review():
    if not can_access(g.user, FLOWS["previous"]["module_id"]):
        abort(403)
    g.active_module = "previous_mail"
    draft = ws().blob_get("previous_draft")
    if not draft:
        return redirect(url_for("flows.previous_index"))
    return render_template("flows/review.html", draft=draft, cfg=FLOWS["previous"], slug="previous")


@bp.route("/previous/confirm", methods=["POST"])
@login_required
def previous_confirm():
    if not can_access(g.user, FLOWS["previous"]["module_id"]):
        abort(403)
    g.active_module = "previous_mail"
    draft = ws().blob_get("previous_draft")
    if not draft:
        return redirect(url_for("flows.previous_index"))
    subj_t = request.form.get("subject_template", draft["subject_template"])
    body_t = request.form.get("body_template", draft["body_template"])
    key_col = request.form.get("key_col", draft["key_col"])
    to, cc, bcc = (request.form.get(k, draft["old_mail"].get(k, "")) for k in ("to", "cc", "bcc"))
    try:
        df = previous_mail.parse_new_data(draft["data_path"])
    except Exception as e:  # noqa: BLE001
        flash(f"Data sheet dobara nahi padh paye: {e}", "error")
        return redirect(url_for("flows.previous_index"))
    if key_col not in df.columns:
        flash(f"Column '{key_col}' data sheet me nahi mila.", "error")
        return redirect(url_for("flows.previous_review"))
    branches = {}
    for key, rows in previous_mail.group_by_key(df, key_col).items():
        subject = previous_mail.render_template(subj_t, rows[0])
        body = previous_mail.render_template(body_t, rows[0])
        if len(rows) > 1:
            body += "\n\n---\nIs group ke saare rows:\n" + previous_mail.render_rows_table_text(rows, draft["columns"])
        branches[key] = dict(branch_code=key, key=key, columns=draft["columns"], rows=rows, subject=subject, body=body,
                             email=to, cc=cc, bcc=bcc, contact_name="", ai_status="pending", ai_reason="")
    fps = {k: fingerprint("previous", k, b["email"], b["subject"] + b["body"]) for k, b in branches.items()}
    flow_for("previous").load(branches, os.path.basename(draft["data_path"]), fps)
    return redirect(url_for("flows.preview", slug="previous"))


# ------------------------------------------------------------------ JSON API (all answers are JSON)
@bp.route("/<slug>/api/status")
@flow_view
def api_status(slug):
    rows, counts = rows_for(slug, flow_for(slug))
    return jsonify(ok=True, counts=counts, rows={r["code"]: {k: r[k] for k in ("status", "error", "attempts", "review", "reason")}
                                                 for r in rows})


@bp.route("/<slug>/api/confirm", methods=["POST"])
@flow_view
def api_confirm(slug):
    selected = (request.get_json(silent=True) or {}).get("selected", [])
    flow = flow_for(slug)
    flow.confirm([str(c) for c in selected])
    return jsonify(ok=True, counts=flow.counts())


@bp.route("/<slug>/api/mail/<code>")
@flow_view
def api_mail(slug, code):
    _, branch = _json_or_400(slug, code)
    m = builders.build_mail(slug, code, branch, _from_name())
    return jsonify(ok=True, subject=m["subject"], html=m.get("html_body") or "", text=m["body"],
                   attachment=m.get("attachment_name") if m.get("attachment_bytes") else "")


@bp.route("/<slug>/api/ai_check/<code>", methods=["POST"])
@flow_view
def api_ai_check(slug, code):
    flow, branch = _json_or_400(slug, code)
    email = ((request.get_json(silent=True) or {}).get("email") or branch.get("email") or "").strip()
    if not email:
        res = {"status": "missing", "reason": "Is branch ke liye email nahi mila — manually dalein."}
    else:
        n, sample = _summary(slug, branch)
        res = ai.run_check(ai.check_prompt(FLOWS[slug]["ai_label"], code, _title(slug, code, branch), branch.get("region"),
                                           email, branch.get("contact_name"), branch.get("designation"), n,
                                           (branch.get("grs") or branch.get("bills") or branch.get("rows") or [])[:5]))
    flow.set_fields(code, ai_status=res["status"], ai_reason=res["reason"])
    return jsonify(ok=True, **res)


@bp.route("/<slug>/api/ai_fix/<code>", methods=["POST"])
@flow_view
def api_ai_fix(slug, code):
    """Best-effort auto-fix of the recipient: missing -> directory lookup, typo'd domain -> closest known domain."""
    if slug == "previous":
        abort(404)
    flow, branch = _json_or_400(slug, code)
    directory = directories.load()
    current = ((request.get_json(silent=True) or {}).get("email") or branch.get("email") or "").strip()
    new, notes = current, []
    if not current:
        emails, names = directory_parser.pick_branch_role_contacts(directory, code)
        if emails:
            new = ", ".join(emails)
            branch["contact_name"] = "; ".join(names)
            notes.append("Email missing thi — directory se role contacts (Incharge/Billing/Delivery/DBP) dobara fetch kiye.")
        else:
            single, name, desig = directory_parser.pick_branch_email(directory, code)
            if single:
                new = single
                branch.update(contact_name=name, designation=desig)
                notes.append("Email missing thi — directory se fallback contact fetch kiya.")
    else:
        fixed, fnotes = emailfix.autofix(current, directory)
        if fixed:
            new, notes = fixed, fnotes + ["Email formatting/typo clean-up ki gayi."]
    if new == current and not notes:
        return jsonify(ok=True, status="unresolved", email=current,
                       reason="Auto-fix ke liye kuch nahi mila (directory me match ya close domain nahi mila) — email manually check karein.")
    flow.set_fields(code, email=new, contact_name=branch.get("contact_name", ""), designation=branch.get("designation", ""))
    n, _ = _summary(slug, branch)
    res = ai.run_check(ai.check_prompt(FLOWS[slug]["ai_label"], code, _title(slug, code, branch), branch.get("region"), new,
                                       branch.get("contact_name"), branch.get("designation"), n,
                                       (branch.get("grs") or branch.get("bills") or [])[:5]))
    flow.set_fields(code, ai_status=res["status"], ai_reason=res["reason"])
    return jsonify(ok=True, status=res["status"], reason=res["reason"], email=new, notes=notes)


@bp.route("/<slug>/api/review/<code>", methods=["POST"])
@flow_view
def api_review(slug, code):
    """Human 'Reviewed' gate - the manual alternative to the AI check (send-all only takes reviewed rows)."""
    flow, branch = _json_or_400(slug, code)
    if not (request.get_json(silent=True) or {}).get("email", branch.get("email")):
        return jsonify(ok=False, error="Pehle recipient email daalein."), 400
    flow.set_fields(code, ai_status="manual_ok", ai_reason=f"Reviewed by {g.user['email']}")
    return jsonify(ok=True, status="manual_ok")


@bp.route("/<slug>/api/review_all", methods=["POST"])
@flow_view
def api_review_all(slug):
    flow = flow_for(slug)
    items = (request.get_json(silent=True) or {}).get("items", {})
    n = 0
    for code, b in flow.branches().items():
        email = (items.get(code) or {}).get("email", b.get("email"))
        if email and b.get("ai_status") not in REVIEW_OK:
            flow.set_fields(code, ai_status="manual_ok", ai_reason=f"Reviewed by {g.user['email']}")
            n += 1
    return jsonify(ok=True, marked=n)


def _apply_recipients(flow, items):
    """Persist the To/CC/BCC the user typed into the table (they are part of what is sent)."""
    for code, v in (items or {}).items():
        if code in flow.branches() and isinstance(v, dict):
            flow.set_fields(code, email=str(v.get("email", ""))[:500], cc=str(v.get("cc", ""))[:500], bcc=str(v.get("bcc", ""))[:500])


def _dispatch(slug, flow, code, mail, via, test):
    """Send ONE mail. Real sends move the row through SENDING -> SENT/FAILED; test sends never touch row state."""
    if via == "smtp":
        cfg = mailcfg.saved(current_app.config, g.user)
        if cfg is None or not cfg.is_complete():
            raise RuntimeError("SMTP settings save nahi hain - Settings me bharein, ya Thunderbird se bhejein.")
        res = mail_sender.send_batch(cfg, [mail])[0]
        if res["status"] != "SENT":
            raise RuntimeError(res.get("error") or res["status"])
        if not test:
            flow.mark_sent(code)
        return {"status": "sent"}
    batch = f"{slug}-{'test-' if test else ''}{datetime.now().strftime('%Y%m%d%H%M%S')}"
    job = bq.enqueue_send(g.user["email"], mail, batch, "")
    if not test:
        flow.mark_sending(code, job)
    return {"status": "queued_thunderbird", "job_id": job}


@bp.route("/<slug>/api/send/<code>", methods=["POST"])
@flow_view
def api_send(slug, code):
    flow, branch = _json_or_400(slug, code)
    d = request.get_json(silent=True) or {}
    via, test = d.get("via", "tb"), bool(d.get("test"))
    test_to = builders.parse_recipients(d.get("test_to", "")) if test else None
    if test and not test_to:
        return jsonify(ok=False, error="Test mode me test email address zaroori hai."), 400
    op = flow.ops().get(code, {})
    if op.get("status") == "SENT" and not test:
        return jsonify(ok=True, status="already_sent", detail="Ye mail pehle hi bheji ja chuki hai — dobara nahi bheji jayegi.")
    flow.set_fields(code, email=d.get("email", branch.get("email", "")), cc=d.get("cc", ""), bcc=d.get("bcc", ""))
    try:
        mail = _mail(slug, code, branch, d.get("email", branch.get("email", "")), d.get("cc", ""), d.get("bcc", ""), test_to)
        out = _dispatch(slug, flow, code, mail, via, test)
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    except Exception as e:  # noqa: BLE001
        if not test:
            flow.mark_failed(code, e)
        return jsonify(ok=False, error=str(e)), 500
    store().add_audit(g.user["email"], f"flow:{slug}:send", f"{code} via {via}{' TEST' if test else ''}")
    return jsonify(ok=True, **out)


@bp.route("/<slug>/api/send_all", methods=["POST"])
@flow_view
def api_send_all(slug):
    """Sends every QUEUED + reviewed row that has a recipient. Test mode sends to the test address and changes nothing."""
    flow = flow_for(slug)
    d = request.get_json(silent=True) or {}
    via, test = d.get("via", "tb"), bool(d.get("test"))
    test_to = builders.parse_recipients(d.get("test_to", "")) if test else None
    if test and not test_to:
        return jsonify(ok=False, error="Test mode me test email address zaroori hai."), 400
    _apply_recipients(flow, d.get("items"))
    branches, ops = flow.branches(), flow.refresh_from_bridge()
    todo = [c for c, b in branches.items() if ops.get(c, {}).get("status") == "QUEUED" and b.get("ai_status") in REVIEW_OK
            and (b.get("email") or "").strip()]
    skipped = [c for c, b in branches.items() if ops.get(c, {}).get("status") == "QUEUED" and c not in todo]
    if not todo:
        return jsonify(ok=False, error="Bhejne ke liye koi QUEUED + reviewed row nahi (recipient email aur 'Reviewed' zaroori hai).",
                       skipped=skipped), 400
    if via == "smtp" and len(todo) > MAX_SMTP:
        return jsonify(ok=False, error=f"SMTP se ek baar me max {MAX_SMTP} mails. Thunderbird se bhejein."), 400
    results = {"sent": 0, "queued": 0, "failed": 0, "errors": {}}
    for code in todo:
        b = branches[code]
        try:
            mail = _mail(slug, code, b, b.get("email", ""), b.get("cc", ""), b.get("bcc", ""), test_to)
            out = _dispatch(slug, flow, code, mail, via, test)
            results["queued" if out["status"] == "queued_thunderbird" else "sent"] += 1
        except Exception as e:  # noqa: BLE001
            results["failed"] += 1
            results["errors"][code] = str(e)
            if not test:
                flow.mark_failed(code, e)
    store().add_audit(g.user["email"], f"flow:{slug}:send_all",
                      f"{len(todo)} rows via {via}{' TEST' if test else ''}: {results['sent'] + results['queued']} ok, {results['failed']} failed")
    return jsonify(ok=True, skipped=skipped, **results)


@bp.route("/<slug>/api/retry_failed", methods=["POST"])
@flow_view
def api_retry_failed(slug):
    return jsonify(ok=True, retried=flow_for(slug).retry_failed())


# ---- Previous Mail: look up the old recipients in the user's own Thunderbird Sent folder
@bp.route("/previous/api/find/<code>", methods=["POST"])
@login_required
def api_find(code):
    if not can_access(g.user, FLOWS["previous"]["module_id"]):
        abort(403)
    branch = flow_for("previous").branches().get(code)
    if not branch:
        abort(404)
    names = list(dict.fromkeys(str(v) for r in branch["rows"] for k, v in r.items() if "party" in k.lower() and v))
    bills = list(dict.fromkeys(str(v) for r in branch["rows"] for k, v in r.items() if "bill" in k.lower() and v))
    job = bq.enqueue_job(g.user["email"], "match", dict(branch_code=branch["key"], branch_name=branch["key"], party_names=names,
                                                       bill_nos=bills, subject=branch.get("subject", "")),
                         batch="previous-match", label=code)
    return jsonify(ok=True, job_id=job)


@bp.route("/previous/api/find_result/<int:job_id>")
@login_required
def api_find_result(job_id):
    job = bq.get_job(g.user["email"], job_id)
    if not job or job["job_type"] != "match":
        abort(404)
    prof = ((job["result"] or {}).get("profile") or {}) if job["status"] == "DONE" else {}
    return jsonify(ok=True, status=job["status"], error=job["error"], profile=prof)


# ---- sent / queued history (this user's Thunderbird bridge jobs)
@bp.route("/history")
@login_required
def history():
    g.active_module = None
    return render_template("flows/history.html", jobs=bq.recent(g.user["email"], 60))
