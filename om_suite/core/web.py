"""Small request helpers shared by the app factory and the auth decorators."""
from flask import request

# URLs that are only ever called by page scripts (fetch) and must never answer with an HTML page.
API_PREFIXES = ("/om/api/", "/jobs/", "/customer/api/", "/paid-topay/insights/", "/dbp/chatbot/ask",
                "/dbp/mail/progress", "/dbp/mail/batch", "/branch/lookup", "/branch/bulk/", "/api/")


def wants_json() -> bool:
    p = request.path
    if p == "/branch/lookup/download":          # a normal form post that returns a file
        return False
    if p.startswith(API_PREFIXES) or "/api/" in p:
        return True
    return (request.is_json or request.headers.get("X-Requested-With") == "fetch"
            or request.accept_mimetypes.best == "application/json")
