"""Paid / ToPay Suite - the original browser-side tool, now inside the shared shell."""
import json
import re
import time
from datetime import datetime

from flask import Blueprint, Response, current_app, g, jsonify, render_template, request, url_for

from core import bridge_queue as bq
from core import insights
from core.auth import module_required, store
from core.modules import can_access

bp = Blueprint("paid_topay", __name__, url_prefix="/paid-topay")


@bp.route("/")
@module_required("paid_topay")
def index():
    return render_template("modules/paid_topay.html")


@bp.route("/insights/api/card/<int:card_id>/query/json", methods=["POST"])
def insights_query(card_id):
    """Server-side replacement for the old local ominsights_proxy.py (JSON errors, never redirects)."""
    cfg = current_app.config
    if not g.user:
        return jsonify(error="Login expire ho gaya - page refresh karke dobara login karein."), 401
    if not can_access(g.user, "paid_topay"):
        return jsonify(error="Is tool ka access nahi hai."), 403
    if card_id not in cfg["OMINSIGHTS_ALLOWED_CARDS"]:
        return jsonify(error="Ye OmInsights card allowed nahi hai."), 403
    body = request.get_data(cache=False)
    if len(body) > 200_000:
        return jsonify(error="Request bahut badi hai."), 413
    creds = insights.saved_credentials(cfg, g.user)
    if not creds:
        return jsonify(error="Pehle Settings me apna OmInsights login save karein."), 400
    if not insights.acquire_slot():
        return jsonify(error="Server abhi busy hai (kuch exports chal rahe hain) - 1 minute baad try karein."), 429

    try:
        upstream = insights.open_query(cfg, g.user["id"], creds, card_id, body)
    except insights.InsightsError as e:
        insights.release_slot()
        return jsonify(error=str(e)), e.status
    except Exception:
        insights.release_slot()
        raise
    store().add_audit(g.user["email"], "insights:fetch", f"card {card_id}")

    def stream():
        try:
            while True:
                chunk = upstream.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            upstream.close()
            insights.release_slot()

    return Response(stream(), status=upstream.status,
                    content_type=upstream.headers.get("Content-Type", "application/json"))


# ====================================================================== Auto Mail -> Thunderbird extension
# The browser builds each region's report (.xlsx) and posts it here with the composed mail; the mail is put
# on the same per-user Thunderbird bridge queue Mail Center uses, and the user's Thunderbird extension sends it.
MAX_MAILS_PER_BATCH = 60
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
_EMAIL_RE = re.compile(r"^[^@\s,;<>\"]+@[^@\s,;<>\"]+\.[^@\s,;<>\"]+$")


def _mail_guard():
    """JSON-friendly access check for the fetch() endpoints below (never redirects to the login page)."""
    if not g.user:
        return jsonify(error="Login expire ho gaya - page refresh karke dobara login karein."), 401
    if not can_access(g.user, "paid_topay"):
        return jsonify(error="Is tool ka access nahi hai."), 403
    return None


def _clean_emails(values):
    """-> (unique valid addresses in order, invalid strings)."""
    if isinstance(values, str):
        values = re.split(r"[;,\s]+", values)
    good, bad, seen = [], [], set()
    for v in values or []:
        v = str(v).strip()
        if not v:
            continue
        if not _EMAIL_RE.match(v):
            bad.append(v)
        elif v.lower() not in seen:
            seen.add(v.lower())
            good.append(v)
    return good, bad


@bp.route("/mail/bridge-status")
def mail_bridge_status():
    err = _mail_guard()
    if err:
        return err
    seen = bq.last_seen(g.user["email"])
    return jsonify(connected=bool(seen and time.time() - seen < 180),
                   ago=int(time.time() - seen) if seen else None,
                   setup_url=url_for("mail.bridge"), me=g.user["email"])


@bp.route("/mail/queue", methods=["POST"])
def mail_queue():
    """multipart/form-data:  meta = JSON {mails:[{region,to,cc,subject,body,attachment}], test, test_to, identity}
    and one file part per attachment (the part name is the mail's `attachment` key)."""
    err = _mail_guard()
    if err:
        return err
    try:
        meta = json.loads(request.form.get("meta") or "{}")
    except ValueError:
        return jsonify(error="Mail data samajh nahi aaya - page refresh karke dobara try karein."), 400
    mails = meta.get("mails") or []
    if not mails:
        return jsonify(error="Bhejne ke liye koi mail nahi hai."), 400
    if len(mails) > MAX_MAILS_PER_BATCH:
        return jsonify(error=f"Ek baar me max {MAX_MAILS_PER_BATCH} mails queue ho sakti hain."), 400

    test_on = bool(meta.get("test"))
    test_to, bad_test = _clean_emails(meta.get("test_to"))
    if test_on and (not test_to or bad_test):
        return jsonify(error="Test mode me ek sahi test email address zaroori hai."), 400
    identity = str(meta.get("identity") or "").strip()[:120]
    if identity and not _EMAIL_RE.match(identity):
        return jsonify(error="Thunderbird identity email sahi nahi hai (ya khali chhodein)."), 400

    outbox = []
    for m in mails:
        region = str(m.get("region") or "").strip()[:120] or "Region"
        to, bad_to = _clean_emails(m.get("to"))
        cc, bad_cc = _clean_emails(m.get("cc"))
        if bad_to or bad_cc:
            return jsonify(error=f"{region}: ye email address sahi nahi hain - {', '.join((bad_to + bad_cc)[:5])}"), 400
        if not to:
            return jsonify(error=f"{region}: To list khaali hai - is region me koi recipient email nahi mila."), 400
        subject = str(m.get("subject") or "").strip()
        body = str(m.get("body") or "")
        if not subject or not body.strip():
            return jsonify(error=f"{region}: subject ya body khaali hai."), 400
        f = request.files.get(str(m.get("attachment") or ""))
        data = f.read() if f else b""
        if not data:
            return jsonify(error=f"{region}: report attachment nahi mili - pehle ToPay report generate karein."), 400
        if len(data) > MAX_ATTACHMENT_BYTES:
            return jsonify(error=f"{region}: report attachment bahut badi hai (max 15 MB)."), 400
        name = re.sub(r"[^A-Za-z0-9._ -]+", "_", str(m.get("attachment_name") or "ToPay_Unbilled.xlsx"))[:120] or "ToPay_Unbilled.xlsx"
        mail = dict(to_list=to, cc_list=cc, bcc_list=[], subject=subject, body=body, html_body="",
                    attachment_bytes=data, attachment_name=name, attachment_type=bq.XLSX,
                    label=region, branch_name=region,
                    ref=dict(module="paid_topay", region=region))
        if test_on:
            mail.update(to_list=test_to[:1], cc_list=[], subject="[TEST] " + subject)
        outbox.append(mail)

    batch = f"ptp-automail-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    for mail in outbox:
        bq.enqueue_send(g.user["email"], mail, batch, identity)
    suffix = " (TEST)" if test_on else ""
    store().add_audit(g.user["email"], "ptp:automail:queue", f"{len(outbox)} region mail(s){suffix}")
    return jsonify(ok=True, batch=batch, queued=len(outbox), test=test_on)


@bp.route("/mail/progress")
def mail_progress():
    err = _mail_guard()
    if err:
        return err
    batch = (request.args.get("batch") or "")[:80]
    rows = bq.batch_status(g.user["email"], batch)
    by = lambda *s: sum(r["status"] in s for r in rows)  # noqa: E731
    seen = bq.last_seen(g.user["email"])
    return jsonify(rows=rows, total=len(rows), done=by("DONE"), error=by("ERROR"), pending=by("QUEUED", "CLAIMED"),
                   cancelled=by("CANCELLED"), connected=bool(seen and time.time() - seen < 180))


@bp.route("/mail/batch", methods=["POST"])
def mail_batch_action():
    err = _mail_guard()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    batch, action = str(data.get("batch") or "")[:80], data.get("action")
    if action == "retry":
        n = bq.retry_failed(g.user["email"], batch)
    elif action == "cancel":
        n = bq.cancel_queued(g.user["email"], batch)
    else:
        return jsonify(error="Unknown action."), 400
    return jsonify(n=n)
