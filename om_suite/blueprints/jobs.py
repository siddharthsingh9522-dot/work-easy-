"""Generic endpoints for background jobs: status polling, stop and result download (owner-only)."""
from flask import Blueprint, abort, g, jsonify, request, send_file

from core import jobs
from core.auth import login_required

bp = Blueprint("jobs", __name__, url_prefix="/jobs")


def _job(job_id):
    job = jobs.get(job_id, g.user["email"])
    if not job:
        abort(404)
    return job


@bp.route("/<job_id>/status")
@login_required
def status(job_id):
    return jsonify(ok=True, **_job(job_id).snapshot(log_from=int(request.args.get("log_from", 0) or 0)))


@bp.route("/<job_id>/stop", methods=["POST"])
@login_required
def stop(job_id):
    _job(job_id).stop_event.set()
    return jsonify(ok=True)


@bp.route("/<job_id>/download")
@login_required
def download(job_id):
    job = _job(job_id)
    if not job.output_name:
        abort(404)
    return send_file(job.path(job.output_name), as_attachment=True, download_name=job.output_name)
