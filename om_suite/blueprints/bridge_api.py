"""
API used by the OM Suite Mail Bridge Thunderbird extension (token auth, no cookies/CSRF).

  GET  /api/bridge/status
  GET  /api/bridge/poll?limit=5[&types=send,match]
  POST /api/bridge/job/<id>/result
  GET  /api/bridge/attachment/<id>
Every request carries  X-Thunderbird-User (the user's e-mail)  and  X-Thunderbird-Token.
"""
import hmac

from flask import Blueprint, Response, current_app, jsonify, request

from core import bridge_queue as bq
from core.auth import get_user_cached
from core.modules import can_access

MAIL_MODULES = ("mail_center", "tbb_mail", "bill_mail", "previous_mail")
from core.store import norm_email, user_id_for

bp = Blueprint("bridge_api", __name__, url_prefix="/api/bridge")


def bridge_master() -> str:
    return current_app.config["BRIDGE_MASTER"] or current_app.config["SECRET_KEY"]


def user_token(user) -> str:
    return bq.token_for(bridge_master(), user["email"], user.get("bridge_v", 1))


def _authenticate():
    """-> (user, None) or (None, error response)."""
    email = norm_email(request.headers.get("X-Thunderbird-User"))
    token = (request.headers.get("X-Thunderbird-Token") or "").strip()
    user = get_user_cached(user_id_for(email)) if email else None
    ok = bool(user) and user.get("status") == "approved" and any(can_access(user, m) for m in MAIL_MODULES) \
        and hmac.compare_digest(token, user_token(user))
    if not ok:
        return None, (jsonify(error="unauthorized"), 401)
    return user, None


@bp.after_request
def cors(resp):                       # token (not cookie) auth, so a wildcard origin is safe here
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "X-Thunderbird-Token, X-Thunderbird-User, Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@bp.route("/<path:_any>", methods=["OPTIONS"])
def preflight(_any):
    return "", 204


@bp.route("/status")
def status():
    user, err = _authenticate()
    if err:
        return err
    bq.touch(user["email"])
    return jsonify(ok=True, service="om-suite-bridge", counts=bq.counts(user["email"]))


@bp.route("/poll")
def poll():
    user, err = _authenticate()
    if err:
        return err
    bq.touch(user["email"])
    types = [t for t in (request.args.get("types") or "").split(",") if t] or None
    return jsonify(jobs=bq.poll(user["email"], request.args.get("limit", 5), types))


@bp.route("/job/<int:job_id>/result", methods=["POST"])
def result(job_id):
    user, err = _authenticate()
    if err:
        return err
    bq.set_result(user["email"], job_id, request.get_json(silent=True) or {})
    return jsonify(ok=True)


@bp.route("/attachment/<aid>")
def attachment(aid):
    user, err = _authenticate()
    if err:
        return err
    att = bq.get_attachment(user["email"], aid)
    if not att:
        return jsonify(error="attachment not found"), 404
    name, ctype, data = att
    return Response(data, content_type=ctype or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
