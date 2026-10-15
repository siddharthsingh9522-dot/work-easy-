"""App configuration — everything comes from environment variables (see .env.example)."""
import os
import secrets
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _csv(name: str) -> set:
    return {x.strip().lower() for x in os.environ.get(name, "").split(",") if x.strip()}


class Config:
    APP_NAME = os.environ.get("APP_NAME", "OM Suite")
    APP_VERSION = "1.0.0-phase1"

    ENV = os.environ.get("APP_ENV", os.environ.get("FLASK_ENV", "development")).lower()
    IS_PROD = ENV == "production"

    SECRET_KEY = os.environ.get("SECRET_KEY") or ""
    if not SECRET_KEY:
        if IS_PROD:
            raise RuntimeError("SECRET_KEY environment variable is required in production.")
        SECRET_KEY = "dev-" + secrets.token_hex(16)  # dev only: sessions reset on restart

    # --- sessions / cookies ---
    PERMANENT_SESSION_LIFETIME = timedelta(days=int(os.environ.get("SESSION_DAYS", "14")))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = IS_PROD
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "50")) * 1024 * 1024
    SEND_FILE_MAX_AGE_DEFAULT = timedelta(days=7)

    # --- users / admin ---
    # Emails listed here become approved admins the moment they sign up.
    # If this is empty, the very first signup becomes the admin (local/dev convenience).
    ADMIN_EMAILS = _csv("ADMIN_EMAILS")
    USER_CACHE_TTL = int(os.environ.get("USER_CACHE_TTL", "10"))  # seconds

    # --- storage: Firebase Firestore (recommended) or local SQLite fallback ---
    FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "ptp-report")
    FIREBASE_SERVICE_ACCOUNT_JSON = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "")
    FIREBASE_SERVICE_ACCOUNT_FILE = os.environ.get("FIREBASE_SERVICE_ACCOUNT_FILE", "")
    FIRESTORE_PREFIX = os.environ.get("FIRESTORE_PREFIX", "omsuite_")
    DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data")))

    # --- OmInsights (Metabase) - each user saves their own login in Settings ---
    OMINSIGHTS_URL = os.environ.get("OMINSIGHTS_URL", "https://ominsights.omlogistics.co.in").rstrip("/")
    OMINSIGHTS_ALLOWED_CARDS = {int(x) for x in os.environ.get("OMINSIGHTS_ALLOWED_CARDS", "546,967").split(",") if x.strip()}
    OMINSIGHTS_TIMEOUT = int(os.environ.get("OMINSIGHTS_TIMEOUT", "600"))  # big exports can take minutes
    # Key used to encrypt saved OmInsights passwords. Defaults to a key derived from SECRET_KEY
    # (so changing SECRET_KEY makes saved passwords unreadable - users just re-enter them).
    CREDENTIAL_KEY = os.environ.get("CREDENTIAL_KEY", "")

    # --- Thunderbird bridge / mail ---
    BRIDGE_MASTER = os.environ.get("BRIDGE_MASTER", "")          # defaults to SECRET_KEY if empty
    PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")     # shown in the extension connection code
    SMTP_DEFAULT_HOST = os.environ.get("SMTP_DEFAULT_HOST", "mail.omlogistics.co.in")
    SMTP_DEFAULT_PORT = int(os.environ.get("SMTP_DEFAULT_PORT", "587"))
    SMTP_DEFAULT_SECURITY = os.environ.get("SMTP_DEFAULT_SECURITY", "starttls")

    # --- internal OM portal pages scraped by Branch Finder / Customer Search / OM Automation ---
    BRANCH_URL = os.environ.get("BRANCH_URL", "http://scmomsanchar.omlogistics.co.in/oracle/query/branch.php")
    CUSTOMER_PORTAL_USER_ID = os.environ.get("CUSTOMER_PORTAL_USER_ID", "39942")
    CUSTOMER_URL = os.environ.get(
        "CUSTOMER_URL", f"https://scmomsanchar.omlogistics.co.in/oracle/query/customer.php?u={CUSTOMER_PORTAL_USER_ID}")
    CN_API_URL = os.environ.get("CN_API_URL", "https://load.omone.in/utility-service/api/cn-status/{}")

    # --- links to other deployments ---
    GR_AUTO_URL = os.environ.get("GR_AUTO_URL", "").strip()

    TRUST_PROXY = _flag("TRUST_PROXY", IS_PROD)
    DISPLAY_TZ_OFFSET_MIN = int(os.environ.get("DISPLAY_TZ_OFFSET_MIN", "330"))  # IST
