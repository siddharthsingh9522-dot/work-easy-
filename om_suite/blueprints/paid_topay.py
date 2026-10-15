"""Paid / ToPay Suite - the original browser-side tool, now inside the shared shell."""
from flask import Blueprint, Response, current_app, g, jsonify, render_template, request

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
