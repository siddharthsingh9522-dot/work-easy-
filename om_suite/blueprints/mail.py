"""Mail Center: single-branch drafts, region auto-mail, full-report mail - sent via Thunderbird (bridge) or SMTP."""
import io
import time
import zipfile
from datetime import datetime

import pandas as pd
from flask import (Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template,
                   request, send_file, url_for)

from blueprints.bridge_api import MAIL_MODULES, user_token
from core import bridge_queue as bq
from core.auth import any_module_required, invalidate, module_required, store
from core.config import BASE_DIR
from dbp import mail_generator, mail_recipients, mail_sender, mailcfg, masters, services
from dbp.workspace import Workspace

bp = Blueprint("mail", __name__, url_prefix="/dbp/mail")

CATS = {"All Pending": None, "ADR / Pending Billing": "PENDING_BILLING",
        "NET / POD not billed": "POD_NOT_BILLED", "DDR Pending": "DDR_PENDING"}
TABS = ("one", "region", "full")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def ws() -> Workspace:
    return Workspace(current_app.config["DATA_DIR"], g.user["id"])


def _back(tab, **kw):
    return redirect(url_for("mail.index", tab=tab, **kw))


def _codes(summary):
    return sorted(set(pd.to_numeric(summary["B.CODE"], errors="coerce").dropna().astype(int).tolist()))


def _bridge_info():
    me = g.user["email"]
    seen = bq.last_seen(me)
    return dict(seen=seen, connected=bool(seen and time.time() - seen < 180),
                ago=int(time.time() - seen) if seen else None, counts=bq.counts(me))


# ====================================================================== page
@bp.route("/", strict_slashes=False)
@module_required("mail_center")
def index():
    w = ws()
    st = w.state()
    m = masters.load()
    tab = request.args.get("tab", "one")
    tab = tab if tab in TABS else "one"
    ctx = dict(tab=tab, st=st, has=st.get("summary_df") is not None, dir_ok=not m.directory_df.empty,
               cats=list(CATS), bridge=_bridge_info(), smtp_ok=mailcfg.saved(current_app.config, g.user) is not None,
               batch=request.args.get("batch") or "", method=request.args.get("method", "tb"),
               smtp_log=w.blob_get("smtp_log"), default_test_to=g.user["email"])
    if not (ctx["has"] and ctx["dir_ok"]):
        return render_template("mail/index.html", **ctx)
    summary, categorized = st["summary_df"], st["categorized_data"]
    codes = _codes(summary)
    ctx["codes"] = codes
    if tab == "one":
        try:
            code = int(request.args.get("code") or codes[0])
        except (ValueError, IndexError):
            abort(400)
        if code not in codes:
            abort(400)
        cat_label = request.args.get("cat", "All Pending")
        if cat_label not in CATS:
            abort(400)
        ctx.update(code=code, cat_label=cat_label, mail=mail_generator.build_mail(
            categorized, summary, m.directory_df, code, CATS[cat_label], st["region_name"], with_attachment=True))
    elif tab == "region":
        cand = mail_recipients.region_dbp_candidates(m.directory_df, codes)
        ctx["candidates"] = [dict(email=r.EMAIL, label=f"{r.NAME} — {r.DESIGNATION} (branch {r.BRANCH_CODE}) — {r.EMAIL}")
                             for r in cand.itertuples()]
        blob = w.blob_get("plan_region")
        if blob:
            plan = blob["plan"]
            ready = [p for p in plan if p["status"] == "READY"]
            prev = request.args.get("prev") or (plan[0]["branch_code"] if plan else None)
            pp = next((p for p in plan if str(p["branch_code"]) == str(prev)), plan[0] if plan else None)
            ctx.update(plan_rows=[{
                "code": p["branch_code"], "name": p["branch_name"], "status": p["status"], "reason": p["reason"],
                "to": ", ".join(p["mail"]["to_list"]), "cc": ", ".join(p["mail"]["cc_list"]),
                "missing": ", ".join(p["recips"]["missing"]),
                "fallback": p["recips"]["fallback_used"], "incharge_mgr": p["recips"]["incharge_from_manager"],
                "rows": p["mail"]["docket_count"]} for p in plan],
                n_ready=len(ready), n_blocked=sum(p["status"] == "BLOCKED" for p in plan),
                n_skipped=sum(p["status"] == "SKIPPED" for p in plan), prev=pp, plan_meta=blob)
    elif tab == "full":
        blob = w.blob_get("plan_full")
        if blob:
            fp = blob["plan"]
            m0 = fp["mails"][0] if fp["mails"] else None
            ctx.update(fp=fp, full_meta=blob, m0=m0, m0_kb=(len(m0["attachment_bytes"]) // 1024) if m0 else 0,
                       fp_to="; ".join(sum([(x["bcc_list"] if blob["bcc"] else x["to_list"]) for x in fp["mails"]], [])),
                       fp_cc="; ".join(m0["cc_list"]) if m0 else "")
    return render_template("mail/index.html", **ctx)


# ====================================================================== downloads (single draft)
def _single_mail():
    w = ws()
    st = w.state()
    m = masters.load()
    if st.get("summary_df") is None or m.directory_df.empty:
        abort(404)
    try:
        code = int(request.args.get("code", ""))
    except ValueError:
        abort(400)
    cat = request.args.get("cat", "All Pending")
    if cat not in CATS or code not in _codes(st["summary_df"]):
        abort(400)
    return code, mail_generator.build_mail(st["categorized_data"], st["summary_df"], m.directory_df, code, CATS[cat],
                                           st["region_name"], with_attachment=True)


@bp.route("/eml")
@module_required("mail_center")
def eml():
    code, mail = _single_mail()
    return Response(mail_generator.as_eml(mail), mimetype="message/rfc822",
                    headers={"Content-Disposition": f'attachment; filename="{code}_pending_mail.eml"'})


@bp.route("/sheet")
@module_required("mail_center")
def sheet():
    code, mail = _single_mail()
    return Response(mail["attachment"], mimetype=XLSX, headers={
        "Content-Disposition": f'attachment; filename="{mail["attachment_name"] or "pending_data.xlsx"}"'})


# ====================================================================== plans
@bp.route("/region/prepare", methods=["POST"])
@module_required("mail_center")
def region_prepare():
    w = ws()
    st = w.state()
    m = masters.load()
    cat_label = request.form.get("cat", "All Pending")
    if st.get("summary_df") is None or m.directory_df.empty or cat_label not in CATS:
        abort(400)
    region_dbp = list(dict.fromkeys(request.form.getlist("dbp_pick") +
                                    mail_recipients.parse_emails(request.form.get("dbp_extra"))))
    try:
        plan = mail_recipients.plan_region_mails(
            st["categorized_data"], st["summary_df"], m.directory_df, st["region_name"], region_dbp,
            CATS[cat_label], request.form.get("skip") == "1", request.form.get("allow") == "1")
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("region plan failed")
        flash(f"Plan banane me dikkat: {e}", "error")
        return _back("region")
    w.blob_put("plan_region", dict(plan=plan, cat=cat_label, region_dbp=region_dbp,
                                   at=datetime.now().strftime("%d-%b %H:%M")))
    return _back("region")


@bp.route("/full/prepare", methods=["POST"])
@module_required("mail_center")
def full_prepare():
    w = ws()
    st = w.state()
    m = masters.load()
    if st.get("summary_df") is None or m.directory_df.empty:
        abort(400)
    try:
        maxr = min(1000, max(50, int(request.form.get("max") or 400)))
        bcc = request.form.get("bcc") == "1"
        path, name = services.export_report(w, st)
        with open(path, "rb") as f:
            rbytes = f.read()
        fp = mail_recipients.plan_full_report_mails(
            st["summary_df"], m.directory_df, rbytes, name, st["region_name"],
            extra_cc=[request.form.get("extra_cc", "")], max_recipients=maxr, bcc_staff=bcc)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("full report plan failed")
        flash(f"Plan banane me dikkat: {e}", "error")
        return _back("full")
    w.blob_put("plan_full", dict(plan=fp, bcc=bcc, at=datetime.now().strftime("%d-%b %H:%M")))
    return _back("full")


# ====================================================================== send
@bp.route("/send", methods=["POST"])
@module_required("mail_center")
def send():
    kind = request.form.get("kind")
    if kind not in ("region", "full"):
        abort(400)
    w = ws()
    blob = w.blob_get("plan_region" if kind == "region" else "plan_full")
    if not blob:
        flash("Pehle plan prepare karein.", "error")
        return _back(kind)
    mails = ([p["mail"] for p in blob["plan"] if p["status"] == "READY"] if kind == "region" else blob["plan"]["mails"])
    method = request.form.get("method", "tb")
    test_on = request.form.get("test") == "1"
    test_to = mail_recipients.parse_emails(request.form.get("test_to"))
    identity = (request.form.get("identity") or "").strip()[:120]
    back = dict(method=method)
    if request.form.get("confirm") != "1":
        flash("Pehle 'preview dekh liya' confirm karein.", "error")
        return _back(kind, **back)
    if not mails:
        flash("Bhejne ke liye koi ready mail nahi hai.", "error")
        return _back(kind, **back)
    if test_on and not test_to:
        flash("Test mode me ek test email address zaroori hai.", "error")
        return _back(kind, **back)

    outbox = []
    for mail in mails:
        mail = dict(mail)
        if test_on:
            mail.update(to_list=test_to[:1], cc_list=[], bcc_list=[], subject="[TEST] " + mail["subject"])
        outbox.append(mail)
    label = "region" if kind == "region" else "fullreport"
    suffix = " (TEST)" if test_on else ""

    if method == "tb":
        batch = f"{label}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        for mail in outbox:
            bq.enqueue_send(g.user["email"], mail, batch, identity)
        store().add_audit(g.user["email"], "mail:queue", f"{label}: {len(outbox)} mail(s){suffix}")
        flash(f"{len(outbox)} mail queue me hain{suffix} - Thunderbird extension bhej dega (Thunderbird khula rehna chahiye).", "ok")
        return _back(kind, method="tb", batch=batch)

    cfg = mailcfg.saved(current_app.config, g.user)
    if cfg is None or not cfg.is_complete():
        flash("SMTP settings save nahi hain - Settings me bharein, ya Thunderbird se bhejein.", "error")
        return _back(kind, **back)
    if len(outbox) > mailcfg.MAX_SMTP_BATCH:
        flash(f"SMTP se ek baar me max {mailcfg.MAX_SMTP_BATCH} mails. Itni badi batch Thunderbird se bhejein.", "error")
        return _back(kind, **back)
    results = mail_sender.send_batch(cfg, outbox)
    sent = sum(r["status"] == "SENT" for r in results)
    w.blob_put("smtp_log", dict(rows=results, label=f"{label}{suffix}", at=datetime.now().strftime("%d-%b %H:%M")))
    store().add_audit(g.user["email"], "mail:smtp", f"{label}: {sent}/{len(results)} sent{suffix}")
    flash(f"SMTP: {sent}/{len(results)} mail(s) bhej di gayi{suffix}.", "ok" if sent == len(results) else "warn")
    return _back(kind, **back)


@bp.route("/smtp-log.csv")
@module_required("mail_center")
def smtp_log_csv():
    log = ws().blob_get("smtp_log")
    if not log:
        abort(404)
    return Response(pd.DataFrame(log["rows"]).to_csv(index=False), mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="mail_send_log.csv"'})


# ====================================================================== Thunderbird progress
@bp.route("/progress")
@module_required("mail_center")
def progress():
    batch = (request.args.get("batch") or "")[:80]
    rows = bq.batch_status(g.user["email"], batch)
    n = len(rows)
    by = lambda *s: sum(r["status"] in s for r in rows)  # noqa: E731
    return jsonify(rows=rows, total=n, done=by("DONE"), error=by("ERROR"), pending=by("QUEUED", "CLAIMED"),
                   cancelled=by("CANCELLED"), seen=bq.last_seen(g.user["email"]))


@bp.route("/batch", methods=["POST"])
@module_required("mail_center")
def batch_action():
    data = request.get_json(silent=True) or request.form
    batch, action = (data.get("batch") or "")[:80], data.get("action")
    if action == "retry":
        n = bq.retry_failed(g.user["email"], batch)
    elif action == "cancel":
        n = bq.cancel_queued(g.user["email"], batch)
    else:
        abort(400)
    return jsonify(n=n)


# ====================================================================== Thunderbird setup page
@bp.route("/bridge")
@any_module_required(*MAIL_MODULES)
def bridge():
    base = current_app.config["PUBLIC_URL"] or request.url_root.rstrip("/")
    token = user_token(g.user)
    code = bq.connection_code(base, g.user["email"], token)
    return render_template("mail/bridge.html", base=base, code=code, info=_bridge_info(), user_email=g.user["email"])


@bp.route("/bridge/regenerate", methods=["POST"])
@any_module_required(*MAIL_MODULES)
def bridge_regenerate():
    store().update_user(g.user["id"], bridge_v=int(g.user.get("bridge_v", 1)) + 1)
    invalidate(g.user["id"])
    store().add_audit(g.user["email"], "bridge:regenerate", "")
    flash("Naya connection code ban gaya. Purana extension ab kaam nahi karega - naya code extension me paste karein.", "ok")
    return redirect(url_for("mail.bridge"))


@bp.route("/extension.zip")
@any_module_required(*MAIL_MODULES)
def extension_zip():
    root = BASE_DIR / "thunderbird_extension"
    if not root.is_dir():
        abort(404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(root).as_posix())       # manifest.json must sit at the zip root
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name="om-suite-mail-bridge.zip")
