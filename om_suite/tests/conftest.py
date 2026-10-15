import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app          # noqa: E402
from core.store import LocalStore    # noqa: E402

PW = "password123"


@pytest.fixture()
def app(tmp_path):
    return create_app(
        {"TESTING": True, "USER_CACHE_TTL": 0, "ADMIN_EMAILS": {"admin@x.com"}, "SECRET_KEY": "test",
         "DATA_DIR": tmp_path / "data"},
        store=LocalStore(tmp_path / "t.db"),
    )


def csrf(client, path="/login"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def do_signup(client, name, email, pw=PW):
    return client.post("/signup", data=dict(csrf_token=csrf(client, "/signup"), name=name, email=email,
                                            password=pw, confirm=pw))


def do_login(client, email, pw=PW, **extra):
    return client.post("/login", data=dict(csrf_token=csrf(client, "/login"), email=email, password=pw, **extra))


def admin_post(client, path, **data):
    token = csrf(client, "/admin/users")
    return client.post(path, data=dict(csrf_token=token, **data))


@pytest.fixture()
def admin(app):
    c = app.test_client()
    do_signup(c, "Admin", "admin@x.com")
    do_login(c, "admin@x.com")
    return c


@pytest.fixture(autouse=True)
def _reset_global_state():
    """The login limiter and OmInsights token cache are process-wide; isolate every test."""
    from core import auth, insights
    auth.limiter._hits.clear()
    insights._tokens.clear()
    auth.invalidate()
    yield
