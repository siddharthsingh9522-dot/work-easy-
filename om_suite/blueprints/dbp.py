"""DBP tools: Report Builder, Validation, Month-wise Summary, AI Chatbot, Directory & Masters."""
import os
from datetime import datetime

import pandas as pd
from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   send_file, url_for)

from core.auth import module_required, store
from dbp import chatbot, ingest, masters, services
from dbp.workspace import UploadError, Workspace

bp = Blueprint("dbp", __name__, url_prefix="/dbp")

ENDPOINT_MODULE = {"report": "dbp_report", "month": "month_summary", "chatbot": "ai_chatbot",
                   "validation": "validation", "directory": "directory"}


@bp.before_request
def _active_module():
    ep = (request.endpoint or "").split(".")[-1]
    for prefix, mod in ENDPOINT_MODULE.items():
        if ep.startswith(prefix):
            g.active_module = mod


def ws() -> Workspace:
    return Workspace(current_app.config["DATA_DIR"], g.user["id"])


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# =============================================================== Report Builder
@bp.route("/report")
@module_required("dbp_report")
def report():
    st = ws().state()
    summary = st.get("summary_df")
    ctx = dict(st=st, has=summary is not None, region=st.get("region_name") or "")
    if summary is not None:
        ctx.update(table=services.df_html(summary), kpis=services.summary_kpis(summary),
                   top=services.top_pending(summary), rows=len(summary))
    return render_template("dbp/report.html", **ctx)


@bp.route("/report/generate", methods=["POST"])
@module_required("dbp_report")
def report_generate():
    w = ws()
    region = (request.form.get("region") or "").strip()[:80] or "Region"
    try:
        paths = w.save_uploads(request.files.getlist("files"))
        summary = services.generate_report(w, masters.load(), region, paths)
    except UploadError as e:
        flash(str(e), "error")
        return redirect(url_for("dbp.report"))
    except Exception as e:  # noqa: BLE001 - show the real reason to the user instead of a blank 500
        current_app.logger.exception("report generation failed")
        flash(f"Report banane me dikkat: {e}", "error")
        return redirect(url_for("dbp.report"))
    store().add_audit(g.user["email"], "dbp:report", f"{region}: {len(summary)} rows")
    flash(f"Report ready: {len(summary)} summary rows.", "ok")
    return redirect(url_for("dbp.report"))


@bp.route("/report/download")
@module_required("dbp_report")
def report_download():
    w = ws()
    st = w.state()
    if st.get("summary_df") is None:
        abort(404)
    path, name = services.export_report(w, st)
    return send_file(path, as_attachment=True, download_name=name, mimetype=XLSX)


# =============================================================== Validation
@bp.route("/validation", methods=["GET", "POST"])
@module_required("validation")
def validation():
    w = ws()
    st = w.state()
    if request.method == "POST" and st.get("categorized_data") is not None:
        try:
            issues = services.run_validation(st)
        except Exception as e:  # noqa: BLE001
            current_app.logger.exception("validation failed")
            flash(f"Validation chalate waqt dikkat: {e}", "error")
            return redirect(url_for("dbp.validation"))
        w.update_state(issues=issues)
        flash(f"Validation poora hua - {len(issues)} issue(s).", "ok")
        return redirect(url_for("dbp.validation"))
    return render_template("dbp/validation.html", st=st, has=st.get("categorized_data") is not None,
                           issues=st.get("issues"))


# =============================================================== Month-wise Summary
@bp.route("/month")
@module_required("month_summary")
def month():
    key = request.args.get("cat", "adr")
    if key not in services.CATS:
        key = "adr"
    w = ws()
    st = w.state()
    ms = (st.get("ms") or {}).get(key) or {}
    data = w.ms_get(key) if ms else None
    ctx = dict(key=key, cats=services.CATS, ms=ms, result=None, region=ms.get("region") or st.get("region_name", ""),
               saved_branches=None, now=datetime.now())
    if ms and data is not None:
        res = services.month_compute(key, ms, masters.load(), data)
        ctx["result"] = res
        if not res["needs_month"]:
            if key == "full":
                for sec in res["sections"]:
                    sec["html"] = services.df_html(sec["df"])
            else:
                res["html"] = services.df_html(res["matrix"])
    return render_template("dbp/month.html", **ctx)


@bp.route("/month/run", methods=["POST"])
@module_required("month_summary")
def month_run():
    key = request.form.get("cat", "adr")
    if key not in services.CATS:
        abort(400)
    w = ws()
    region = (request.form.get("region") or "").strip()[:80] or "Region"
    try:
        paths = w.save_uploads(request.files.getlist("files"), subdir=f"ms/{key}")
        data = ingest.merge_all_uploaded(paths)
        branches = services.codes_from_inputs(
            region, request.form.get("use_saved") == "1", request.form.get("codes"),
            request.files.get("prev_report"), request.form.get("save_list") == "1")
    except UploadError as e:
        flash(str(e), "error")
        return redirect(url_for("dbp.month", cat=key))
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("month-wise ingest failed")
        flash(f"Files padhne me dikkat: {e}", "error")
        return redirect(url_for("dbp.month", cat=key))
    w.ms_put(key, data)
    st = w.state()
    all_ms = dict(st.get("ms") or {})
    all_ms[key] = dict(region=region, branches=branches)
    w.update_state(ms=all_ms)
    store().add_audit(g.user["email"], "dbp:month", f"{key} {region}")
    return redirect(url_for("dbp.month", cat=key))


@bp.route("/month/period", methods=["POST"])
@module_required("month_summary")
def month_period():
    key = request.form.get("cat", "adr")
    w = ws()
    st = w.state()
    all_ms = dict(st.get("ms") or {})
    ms = dict(all_ms.get(key) or {})
    if not ms:
        return redirect(url_for("dbp.month", cat=key))
    for which in ("single", "adr", "net"):
        mo, yr = request.form.get(f"month_{which}"), request.form.get(f"year_{which}")
        if mo and yr:
            try:
                p = str(pd.Period(f"{int(yr)}-{int(mo):02d}", freq="M"))
            except ValueError:
                abort(400)
            ms["cm" if which == "single" else f"cm_{which}"] = p
    all_ms[key] = ms
    w.update_state(ms=all_ms)
    return redirect(url_for("dbp.month", cat=key))


@bp.route("/month/reset-period", methods=["POST"])
@module_required("month_summary")
def month_reset_period():
    key = request.form.get("cat", "adr")
    w = ws()
    st = w.state()
    all_ms = dict(st.get("ms") or {})
    ms = dict(all_ms.get(key) or {})
    for k in ("cm", "cm_adr", "cm_net"):
        ms.pop(k, None)
    all_ms[key] = ms
    w.update_state(ms=all_ms)
    return redirect(url_for("dbp.month", cat=key))


@bp.route("/month/download")
@module_required("month_summary")
def month_download():
    key = request.args.get("cat", "adr")
    if key not in services.CATS:
        abort(400)
    w = ws()
    ms = (w.state().get("ms") or {}).get(key)
    data = w.ms_get(key) if ms else None
    if not ms or data is None:
        abort(404)
    res = services.month_compute(key, ms, masters.load(), data)
    if res["needs_month"]:
        flash("Pehle report ka month select karein.", "warn")
        return redirect(url_for("dbp.month", cat=key))
    path = services.month_export(w, key, ms, res)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path), mimetype=XLSX)


# =============================================================== AI Chatbot
@bp.route("/chatbot")
@module_required("ai_chatbot")
def chatbot_page():
    st = ws().state()
    return render_template("dbp/chatbot.html", st=st, has=st.get("summary_df") is not None,
                           history=st.get("chat_history") or [])


@bp.route("/chatbot/ask", methods=["POST"])
@module_required("ai_chatbot")
def chatbot_ask():
    w = ws()
    st = w.state()
    if st.get("summary_df") is None:
        return jsonify(error="Pehle Report Builder me report generate karein."), 400
    q = ((request.get_json(silent=True) or {}).get("q") or "").strip()
    if not q or len(q) > 500:
        return jsonify(error="Sawal 1 se 500 characters ka hona chahiye."), 400
    m = masters.load()
    try:
        ans = chatbot.answer_question(q, st["summary_df"], st["branch_name_map"], directory_df=m.directory_df,
                                      categorized_data=st["categorized_data"])
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("chatbot failed")
        return jsonify(error=f"Jawab banate waqt dikkat: {e}"), 500
    hist = list(st.get("chat_history") or [])
    hist += [("user", q), ("assistant", ans)]
    w.update_state(chat_history=hist[-60:])
    return jsonify(answer=ans)


@bp.route("/chatbot/clear", methods=["POST"])
@module_required("ai_chatbot")
def chatbot_clear():
    ws().update_state(chat_history=[])
    return redirect(url_for("dbp.chatbot_page"))


# =============================================================== Directory & Masters (admin)
@bp.route("/directory", methods=["GET", "POST"])
@module_required("directory")
def directory():
    if request.method == "POST":
        which, f = request.form.get("which"), request.files.get("file")
        if not f or not f.filename:
            flash("Pehle file select karein.", "error")
            return redirect(url_for("dbp.directory"))
        try:
            if which == "directory":
                n = masters.replace_directory(f, f.filename)
                flash(f"Directory update ho gayi: {n} rows.", "ok")
            elif which == "branch":
                n = masters.replace_branch_master(f, f.filename)
                flash(f"Branch master update ho gaya: {n} codes.", "ok")
            else:
                abort(400)
            store().add_audit(g.user["email"], f"dbp:master:{which}", f.filename)
        except Exception as e:  # noqa: BLE001
            flash(f"Update nahi hua (purani file safe hai): {e}", "error")
        return redirect(url_for("dbp.directory"))
    m = masters.load()
    q = (request.args.get("q") or "").strip()
    bq = (request.args.get("bq") or "").strip().lower()
    ddf = m.directory_df
    if q and not ddf.empty:
        mask = ddf.astype(str).apply(lambda col: col.str.contains(q, case=False, na=False, regex=False))
        ddf = ddf[mask.any(axis=1)]
    bm = pd.DataFrame([{"B.CODE": k, "BRANCH NAME": v} for k, v in m.branch_master_map.items()])
    if not bm.empty:
        bm = bm.sort_values("B.CODE", key=lambda s: pd.to_numeric(s, errors="coerce"))
        if bq:
            bm = bm[bm.astype(str).apply(lambda c: c.str.lower().str.contains(bq, regex=False)).any(axis=1)]
    return render_template("dbp/directory.html", m=m, q=q, bq=bq, total=len(m.directory_df),
                           dir_html=services.df_html(ddf, 300), found=len(ddf),
                           bm_html=services.df_html(bm, 300), bm_total=len(m.branch_master_map))
