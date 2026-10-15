"""OmInsights server-side access, tested against a tiny fake Metabase over real HTTP."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import admin_post, csrf, do_login, do_signup
from core import insights
from app import create_app
from core.store import LocalStore, user_id_for

ROWS = [{"CN_CN_NO": "A1", "CN_AMOUNT": 100}, {"CN_CN_NO": "A2", "CN_AMOUNT": 250}]


class FakeMetabase:
    def __init__(self):
        self.logins, self.queries, self.valid, self.fail_status = 0, [], set(), None
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.path == "/api/session":
                    c = json.loads(body)
                    if c.get("username") == "me@om.in" and c.get("password") == "pw-123":
                        outer.logins += 1
                        tok = f"tok-{outer.logins}"
                        outer.valid.add(tok)
                        return self._send(200, {"id": tok})
                    return self._send(401, {"message": "Password did not match stored password."})
                m = re.match(r"/api/card/(\d+)/query/json$", self.path)
                if m:
                    if self.headers.get("X-Metabase-Session") not in outer.valid:
                        return self._send(401, {"message": "Unauthenticated"})
                    if outer.fail_status:
                        return self._send(outer.fail_status, {"message": "boom"})
                    outer.queries.append((m.group(1), json.loads(body), self.headers.get("X-Metabase-Session")))
                    return self._send(200, ROWS)
                self._send(404, {})

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def expire_tokens(self):
        self.valid.clear()


@pytest.fixture()
def fake():
    f = FakeMetabase()
    yield f
    f.server.shutdown()


@pytest.fixture()
def iapp(tmp_path, fake):
    return create_app(
        {"TESTING": True, "USER_CACHE_TTL": 0, "ADMIN_EMAILS": {"admin@x.com"}, "SECRET_KEY": "test",
         "OMINSIGHTS_URL": fake.url, "OMINSIGHTS_TIMEOUT": 5, "DATA_DIR": tmp_path / "data"},
        store=LocalStore(tmp_path / "t.db"))


@pytest.fixture()
def me(iapp):
    c = iapp.test_client()
    do_signup(c, "Admin", "admin@x.com")
    do_login(c, "admin@x.com")
    return c


def token_of(client, path="/settings"):
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


def save_login(client, user="me@om.in", pw="pw-123"):
    return client.post("/settings", data=dict(csrf_token=token_of(client), form="insights", action="save",
                                               username=user, password=pw), follow_redirects=True)


def query(client, card=546, payload=None, csrf_header=True):
    headers = {"X-CSRF-Token": token_of(client)} if csrf_header else {}
    return client.post(f"/paid-topay/insights/api/card/{card}/query/json", json=payload or {"parameters": []},
                       headers=headers)


def test_query_needs_saved_login(me):
    r = query(me)
    assert r.status_code == 400 and "Settings" in r.get_json()["error"]


def test_save_verifies_and_encrypts_password(iapp, me, fake):
    page = save_login(me).get_data(as_text=True)
    assert "verify hokar save" in page and fake.logins == 1
    stored = iapp.extensions["store"].get_user(user_id_for("admin@x.com"))["insights"]
    assert stored["username"] == "me@om.in" and "pw-123" not in json.dumps(stored)
    assert insights.decrypt(iapp.config, stored["password_enc"]) == "pw-123"
    assert "pw-123" not in page                                   # never echoed back


def test_wrong_password_is_not_saved(iapp, me):
    page = save_login(me, pw="nope").get_data(as_text=True)
    assert "galat" in page
    assert not iapp.extensions["store"].get_user(user_id_for("admin@x.com")).get("insights")


def test_query_streams_upstream_json(me, fake):
    save_login(me)
    r = query(me, payload={"parameters": [{"id": "x"}]})
    assert r.status_code == 200 and r.get_json() == ROWS
    card, body, sess = fake.queries[-1]
    assert card == "546" and body == {"parameters": [{"id": "x"}]} and sess == "tok-1"


def test_expired_metabase_session_relogs_in_once(me, fake):
    save_login(me)
    query(me)
    fake.expire_tokens()
    r = query(me)
    assert r.status_code == 200 and fake.logins == 2


def test_only_whitelisted_cards(me):
    save_login(me)
    assert query(me, card=1).status_code == 403
    assert query(me, card=967).status_code == 200


def test_csrf_header_required(me):
    save_login(me)
    assert query(me, csrf_header=False).status_code == 400


def test_anonymous_gets_json_401_not_redirect(iapp):
    c = iapp.test_client()
    r = c.post("/paid-topay/insights/api/card/546/query/json", json={}, headers={"X-CSRF-Token": token_of(c, "/login")})
    assert r.status_code == 401 and "error" in r.get_json()


def test_user_without_paid_topay_access_is_refused(iapp, me):
    u = iapp.test_client()
    do_signup(u, "Ravi", "ravi@x.com")
    admin_post(me, f"/admin/users/{user_id_for('ravi@x.com')}/action", action="approve", modules="dbp_report")
    do_login(u, "ravi@x.com")
    assert query(u).status_code == 403


def test_upstream_errors_are_reported(me, fake):
    save_login(me)
    fake.fail_status = 500
    r = query(me)
    assert r.status_code == 500 and "HTTP 500" in r.get_json()["error"]


def test_unreachable_server_message(iapp, me):
    save_login(me)
    iapp.config["OMINSIGHTS_URL"] = "http://127.0.0.1:9"      # nothing listens here
    insights.forget(user_id_for("admin@x.com"))
    r = query(me)
    assert r.status_code == 502 and "pahunch" in r.get_json()["error"]


def test_remove_login(iapp, me):
    save_login(me)
    me.post("/settings", data=dict(csrf_token=token_of(me), form="insights", action="remove"))
    assert not iapp.extensions["store"].get_user(user_id_for("admin@x.com")).get("insights")
    assert query(me).status_code == 400
