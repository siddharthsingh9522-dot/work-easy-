"""ADR / NET Billing Suite - the page is client-side (files are processed in the browser, never uploaded)."""
from flask import Blueprint, render_template

from core.auth import module_required

bp = Blueprint("billing", __name__, url_prefix="/billing")


@bp.route("/")
@module_required("adr_net_suite")
def index():
    return render_template("modules/billing.html")
