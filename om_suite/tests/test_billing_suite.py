"""ADR / NET Billing Suite: the page + access control (Python) and its JS engine/UI tests (Node, several time zones)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import admin_post, do_login, do_signup
from core.store import user_id_for

ROOT = Path(__file__).resolve().parents[1]
NODE_OK = bool(shutil.which("node")) and (ROOT / "node_modules" / "xlsx").exists() and (ROOT / "node_modules" / "jsdom").exists()


def test_page_loads_the_engine_and_ui_with_versioned_urls(admin):
    html = admin.get("/billing/").get_data(as_text=True)
    assert 'id="bsuite"' in html and "xlsx.full.min.js" in html
    import re
    assert re.search(r"modules/billing/core\.js\?v=[0-9a-f]{10}", html)
    assert re.search(r"modules/billing/app\.js\?v=[0-9a-f]{10}", html)
    for sub in ("query", "branch", "region"):
        assert f'data-sub-mode="{sub}"' in html                       # sidebar sub-links
    for f in ("core.js", "app.js", "billing.css"):
        assert admin.get(f"/static/modules/billing/{f}").status_code == 200


def test_dashboard_lists_the_new_option(admin):
    assert "ADR / NET Billing Suite" in admin.get("/").get_data(as_text=True)


def test_access_is_per_user(app, admin):
    u = app.test_client()
    do_signup(u, "Ravi", "ravi@x.com")
    admin_post(admin, f"/admin/users/{user_id_for('ravi@x.com')}/action", action="approve", modules=["paid_topay"])
    do_login(u, "ravi@x.com")
    assert u.get("/billing/").status_code == 403
    admin_post(admin, f"/admin/users/{user_id_for('ravi@x.com')}/action", action="save_modules", modules=["adr_net_suite"])
    assert u.get("/billing/").status_code == 200


@pytest.mark.skipif(not NODE_OK, reason="needs node and `npm install` (dev only)")
@pytest.mark.parametrize("tz", ["Asia/Kolkata", "America/New_York"])
def test_js_engine_and_ui_suite(tz):
    """The audited tools mis-bucketed real Excel dates in IST; the engine must give identical results in any zone."""
    res = subprocess.run(["node", "--test", "tests/js/billing_core.test.js", "tests/js/billing_ui.test.js"], cwd=ROOT,
                         env={**os.environ, "TZ": tz}, capture_output=True, text=True, timeout=300)
    tail = "\n".join((res.stdout + res.stderr).splitlines()[-25:])
    assert res.returncode == 0, tail
    assert "# fail 0" in res.stdout, tail
