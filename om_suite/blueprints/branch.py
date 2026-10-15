"""Branch Detail Finder - single lookup and bulk Excel (scrapes the internal OM branch page)."""
import io
import os
import random
import re
import time

import pandas as pd
import requests
from bs4 import BeautifulSoup
from flask import Blueprint, current_app, g, jsonify, render_template, request, send_file

from core import jobs
from core.auth import module_required

bp = Blueprint("branch", __name__, url_prefix="/branch")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Content-Type": "application/x-www-form-urlencoded",
}
RETRY_COUNT, TIMEOUT, DELAY_RANGE, BLOCK_COOLDOWN = 4, 20, (1.0, 2.0), 15
COLUMNS = ["SEARCH_CODE", "BRANCH_CODE", "BRANCH_NAME", "ADDRESS", "CONTROLLING_BRANCH", "GST_NO"]
MAX_CODES = 5000


@bp.before_request
def _active():
    g.active_module = "branch_finder"


def fetch_branch(session, url, code):
    """-> (row dict, None) or (None, reason).  Same scraping logic as the standalone branch_app."""
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            r = session.post(url, data={"bname": "", "bcode": code, "ctrlcode": "", "state": "all", "submit": "Search"},
                             headers={**HEADERS, "Referer": url}, timeout=TIMEOUT)
            if r.status_code == 403:
                time.sleep(BLOCK_COOLDOWN)
                continue
            if r.status_code != 200:
                time.sleep(2)
                continue
            table = BeautifulSoup(r.text, "html.parser").find("table", class_="table")
            if table:
                tbody = table.find("tbody")
                for tr in (tbody.find_all("tr") if tbody else []):
                    cols = [td.get_text(strip=True) for td in tr.find_all("td")]
                    if len(cols) >= 6 and cols[1]:
                        return dict(zip(COLUMNS, [code] + cols[1:6])), None
            return None, "No data returned"
        except Exception as e:  # noqa: BLE001
            current_app.logger.warning("branch fetch(%s) attempt %s failed: %s: %s", code, attempt, type(e).__name__, e)
            time.sleep(2)
    return None, "HTTP 403 Blocked / retries exhausted"


@bp.route("/")
@module_required("branch_finder")
def index():
    return render_template("branch/index.html")


@bp.route("/lookup", methods=["POST"])
@module_required("branch_finder")
def lookup():
    code = (request.form.get("branch_code") or "").strip()
    if not code.isdigit():
        return jsonify(ok=False, error="Valid numeric branch code daalo.")
    row, reason = fetch_branch(requests.Session(), current_app.config["BRANCH_URL"], code)
    return jsonify(ok=True, data=row) if row else jsonify(ok=False, error=reason or "Data nahi mila is code ke liye.")


@bp.route("/lookup/download", methods=["POST"])
@module_required("branch_finder")
def lookup_download():
    row = {c: request.form.get(c, "") for c in COLUMNS}
    buf = io.BytesIO()
    pd.DataFrame([row]).to_excel(buf, index=False)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"branch_{re.sub(r'[^0-9A-Za-z]', '', row['SEARCH_CODE']) or 'result'}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _bulk(job, path, url):
    df = pd.read_excel(path)
    codes = df.iloc[:, 0].dropna().astype(str).str.extract(r"(\d+)")[0].dropna().unique()[:MAX_CODES]
    job.progress(0, len(codes))
    session = requests.Session()
    ok_rows, err_rows = [], []
    for code in codes:
        if job.stopped:
            break
        row, reason = fetch_branch(session, url, code)
        if row:
            ok_rows.append(row)
            job.bump("success")
        else:
            err_rows.append({"SEARCH_CODE": code, "REASON": reason})
            job.bump("errors")
        job.progress(job.current + 1)
        time.sleep(random.uniform(*DELAY_RANGE) if DELAY_RANGE[1] else 0)
    out = job.path("all_branch_details.xlsx")
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        pd.DataFrame(ok_rows, columns=COLUMNS).to_excel(w, sheet_name="Success Report", index=False)
        pd.DataFrame(err_rows, columns=["SEARCH_CODE", "REASON"]).to_excel(w, sheet_name="Error Report", index=False)
        pd.DataFrame([{"Total Branch Codes": len(codes), "Success": len(ok_rows),
                       "Errors/Not Found": len(err_rows)}]).to_excel(w, sheet_name="Summary", index=False)
    job.output_name = "all_branch_details.xlsx"
    os.remove(path)


@bp.route("/bulk/start", methods=["POST"])
@module_required("branch_finder")
def bulk_start():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(ok=False, error="Koi file select nahi ki."), 400
    if os.path.splitext(f.filename)[1].lower() not in (".xlsx", ".xls"):
        return jsonify(ok=False, error="Sirf Excel (.xlsx / .xls) file."), 400
    path = jobs.save_upload(f, os.path.splitext(f.filename)[1].lower())
    url = current_app.config["BRANCH_URL"]
    job = jobs.start(g.user["email"], "branch-bulk", lambda j: _bulk(j, path, url))
    return jsonify(ok=True, job_id=job.id)
