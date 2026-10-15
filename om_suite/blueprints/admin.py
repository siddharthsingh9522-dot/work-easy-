"""Admin: approve / block / reset users, per-user tool access, audit log."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from core.auth import admin_required, invalidate, new_pv, store
from core.modules import ASSIGNABLE
from core.security import temp_password

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _active_admins(users):
    return [u for u in users if u["role"] == "admin" and u["status"] == "approved"]


@bp.route("/users")
@admin_required
def users():
    q = (request.args.get("q") or "").strip().lower()
    rows = sorted(store().list_users(), key=lambda u: (u["status"] != "pending", u["name"].lower()))
    if q:
        rows = [u for u in rows if q in (u["name"] + " " + u["email"]).lower()]
    return render_template("admin_users.html", rows=rows, q=q, assignable=ASSIGNABLE)


@bp.route("/audit")
@admin_required
def audit():
    return render_template("admin_audit.html", events=store().list_audit(200))


@bp.route("/users/<uid>/action", methods=["POST"])
@admin_required
def user_action(uid):
    s = store()
    target = s.get_user(uid)
    if not target:
        abort(404)
    action = request.form.get("action", "")
    me = g.user
    is_self = target["id"] == me["id"]
    all_users = s.list_users()
    valid_mods = {m["id"] for m in ASSIGNABLE}
    chosen = [m for m in request.form.getlist("modules") if m in valid_mods]
    label = target["email"]

    if action == "approve":
        s.update_user(uid, status="approved", modules=chosen, pv=new_pv())
        flash(f"{label} approve ho gaya.", "ok")
    elif action == "save_modules":
        s.update_user(uid, modules=chosen)
        flash(f"{label} ka tool access update ho gaya.", "ok")
    elif action in ("block", "delete", "demote") and is_self:
        flash("Apne aap par ye action nahi ho sakta.", "error")
        return redirect(url_for("admin.users"))
    elif action in ("block", "delete", "demote") and target["role"] == "admin" \
            and len(_active_admins(all_users)) <= 1:
        flash("Last admin ko hata/block nahi kar sakte.", "error")
        return redirect(url_for("admin.users"))
    elif action == "block":
        s.update_user(uid, status="blocked", pv=new_pv())
        flash(f"{label} block ho gaya (turant logout).", "ok")
    elif action == "unblock":
        s.update_user(uid, status="approved", pv=new_pv())
        flash(f"{label} unblock ho gaya.", "ok")
    elif action == "promote":
        s.update_user(uid, role="admin", modules=["*"])
        flash(f"{label} ab admin hai.", "ok")
    elif action == "demote":
        s.update_user(uid, role="user", modules=chosen or [m["id"] for m in ASSIGNABLE])
        flash(f"{label} ab normal user hai.", "ok")
    elif action == "reset_password":
        tmp = temp_password()
        s.update_user(uid, pw_hash=generate_password_hash(tmp), pv=new_pv())
        flash(f"{label} ka temporary password: {tmp}  (sirf ab dikhega - user ko bata dein)", "temp")
    elif action == "delete":
        s.delete_user(uid)
        flash(f"{label} delete ho gaya.", "ok")
    else:
        abort(400)
    s.add_audit(me["email"], f"admin:{action}", label)
    invalidate(uid)
    return redirect(url_for("admin.users"))
