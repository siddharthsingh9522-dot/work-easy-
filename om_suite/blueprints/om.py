"""OM Automation: CN / GST / Party Code / Master lookups - manual, Excel batch and the captcha-driven GST flow."""
import os
import shutil
import time
import uuid

from flask import Blueprint, abort, current_app, g, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from core import jobs
from core.auth import module_required
from omauto import config as om_config
from omauto.api import gst_captcha
from omauto.core.batch_runner import BATCH_RUNNERS, MANUAL_FETCHERS, run_master_manual
from omauto.excel.report import ReportManager

bp = Blueprint("om", __name__, url_prefix="/om")

ALLOWED_EXTENSIONS = (".xlsx", ".xlsm")
PAGE_META = {
    "cn": {"title": "CN Query", "subtitle": "Check CN / Docket / LR / Billty status", "input_label": "CN No."},
    "gst": {"title": "GST Query", "subtitle": "Verify GSTIN details (captcha)", "input_label": "GSTIN"},
    "party": {"title": "Party Code Query", "subtitle": "Lookup customer by party code", "input_label": "Party Code"},
    "master": {"title": "Master", "subtitle": "CN No. \u2192 Party Code \u2192 GSTIN \u2192 GST details, all in one",
               "input_label": "CN No."},
}


@bp.errorhandler(Exception)
def _json_errors(e):
    """The page's script always expects JSON from /om/api/...; never hand it an HTML error page."""
    from werkzeug.exceptions import HTTPException
    if request.path.startswith("/om/api/"):
        status = e.code if isinstance(e, HTTPException) else 500
        if status >= 500:
            current_app.logger.exception("om api error")
        return jsonify(success=False, message=f"{type(e).__name__}: {e}"), status
    raise e


def _excel_ok(name):
    return os.path.splitext(name or "")[1].lower() in ALLOWED_EXTENSIONS


@bp.before_request
def _active():
    g.active_module = "om_automation"


@bp.route("/")
@module_required("om_automation")
def index():
    return render_template("om/index.html", pages=PAGE_META)


@bp.route("/api/manual/<page_key>", methods=["POST"])
@module_required("om_automation")
def manual_fetch(page_key):
    if page_key not in PAGE_META:
        return jsonify(success=False, message="Unknown page"), 404
    value = str((request.get_json(silent=True) or {}).get("value", "")).strip()
    if not value:
        return jsonify(success=False, message="Value is required"), 400
    if page_key == "master":
        result = run_master_manual(value)
        return jsonify(success=result["success"], data=result["data"] if result["success"] else result["error"])
    result = MANUAL_FETCHERS[page_key](value)
    if not result["success"]:
        return jsonify(success=False, data={PAGE_META[page_key]["input_label"]: value, "Error": result["message"]})
    return jsonify(success=True, data=result["data"])


# ------------------------------------------------------------------ GST captcha flow
def _uid():
    return g.user["id"]


@bp.route("/api/gst/captcha/start_manual", methods=["POST"])
@module_required("om_automation")
def gst_start_manual():
    gstin = str((request.get_json(silent=True) or {}).get("gstin", "")).strip().upper()
    if not gst_captcha.is_valid_gstin(gstin):
        return jsonify(success=False, message="That doesn't look like a valid 15-character GSTIN."), 400
    try:
        return jsonify(success=True, **gst_captcha.start_session(_uid(), [gstin]))
    except RuntimeError as e:
        return jsonify(success=False, message=str(e)), 503


@bp.route("/api/gst/captcha/start_excel", methods=["POST"])
@module_required("om_automation")
def gst_start_excel():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(success=False, message="No file selected"), 400
    if not _excel_ok(f.filename):
        return jsonify(success=False, message="Only .xlsx files are allowed"), 400
    try:
        result = gst_captcha.start_excel(_uid(), f)
    except RuntimeError as e:
        return jsonify(success=False, message=str(e)), 503
    if "error" in result:
        return jsonify(success=False, message=result["error"]), 400
    return jsonify(success=True, **result)


@bp.route("/api/gst/captcha/image")
@module_required("om_automation")
def gst_image():
    path = gst_captcha.captcha_image_path(_uid())
    if not path:
        abort(404)
    resp = send_file(path, mimetype="image/png", max_age=0)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/api/gst/captcha/submit", methods=["POST"])
@module_required("om_automation")
def gst_submit():
    result, status = gst_captcha.submit_captcha(_uid(), (request.get_json(silent=True) or {}).get("captcha", ""))
    return jsonify(success="error" not in result, **result), status


@bp.route("/api/gst/captcha/stop", methods=["POST"])
@module_required("om_automation")
def gst_stop():
    result, status = gst_captcha.stop_session(_uid())
    return jsonify(success="error" not in result, **result), status


@bp.route("/api/gst/report")
@module_required("om_automation")
def gst_report():
    path = gst_captcha.report_path(_uid())
    if not path:
        abort(404)
    return send_file(path, as_attachment=True, download_name="GST_Report.xlsx")


# ------------------------------------------------------------------ Excel batches (background jobs)
def _batch(job, runner, path):
    started = time.time()
    try:
        success, errors = runner(path, job.progress, job.log, job.stop_event)
        out = ReportManager().generate(success, errors, time.time() - started)
        name = os.path.basename(out)
        shutil.move(out, job.path(name))
        job.output_name = name
        job.log(f"Done. Success: {len(success)} | Failed: {len(errors)}")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


@bp.route("/api/excel/<page_key>/start", methods=["POST"])
@module_required("om_automation")
def excel_start(page_key):
    if page_key not in BATCH_RUNNERS:
        return jsonify(success=False, message="Unknown page"), 404
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(success=False, message="No file selected"), 400
    if not _excel_ok(f.filename):
        return jsonify(success=False, message="Only .xlsx files are allowed"), 400
    path = os.path.join(om_config.INPUT_DIR, f"{uuid.uuid4().hex}_{secure_filename(f.filename)}")
    f.save(path)
    job = jobs.start(g.user["email"], f"om-{page_key}", lambda j: _batch(j, BATCH_RUNNERS[page_key], path))
    return jsonify(success=True, job_id=job.id)


@bp.route("/api/excel/status/<job_id>")
@module_required("om_automation")
def excel_status(job_id):
    job = jobs.get(job_id, g.user["email"])
    if not job:
        return jsonify(success=False, message="Unknown job"), 404
    return jsonify(success=True, **job.snapshot(log_from=int(request.args.get("log_from", 0) or 0)))


@bp.route("/api/excel/stop/<job_id>", methods=["POST"])
@module_required("om_automation")
def excel_stop(job_id):
    if not jobs.stop(job_id, g.user["email"]):
        return jsonify(success=False, message="Unknown job"), 404
    return jsonify(success=True)
