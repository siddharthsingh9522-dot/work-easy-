"""Per-user SMTP settings (optional alternative to sending through Thunderbird). Password is stored encrypted."""
import time

from core import insights

from .mail_sender import SmtpConfig, test_connection

SECURITIES = ("starttls", "ssl", "none")
MAX_SMTP_BATCH = 150            # SMTP sends happen inside one web request; bigger batches should use Thunderbird


def saved(app_cfg, user):
    """SmtpConfig saved by this user, or None."""
    b = (user or {}).get("smtp") or {}
    if not b.get("host") or not b.get("from_addr"):
        return None
    pw = insights.decrypt(app_cfg, b["password_enc"]) if b.get("password_enc") else ""
    if b.get("password_enc") and pw is None:
        return None                       # key changed: user must re-enter the password
    return SmtpConfig(host=b["host"], port=int(b.get("port") or 587), user=b.get("user", ""), password=pw or "",
                      from_addr=b["from_addr"], security=b.get("security", "starttls"),
                      delay_seconds=float(b.get("delay", 1.0)))


def public_view(app_cfg, user):
    """What the Settings form shows (never the password)."""
    b = (user or {}).get("smtp") or {}
    return dict(host=b.get("host") or app_cfg["SMTP_DEFAULT_HOST"], port=b.get("port") or app_cfg["SMTP_DEFAULT_PORT"],
                security=b.get("security") or app_cfg["SMTP_DEFAULT_SECURITY"], user=b.get("user", ""),
                from_addr=b.get("from_addr", ""), delay=b.get("delay", 1.0), has=bool(b.get("host")),
                has_password=bool(b.get("password_enc")))


def build_from_form(app_cfg, form, existing_user):
    """-> (SmtpConfig, error). Empty password keeps the already-saved one."""
    host = (form.get("host") or "").strip()
    try:
        port = int(form.get("port") or 0)
        delay = float(form.get("delay") or 0)
    except ValueError:
        return None, "Port aur gap number hone chahiye."
    security = (form.get("security") or "starttls").lower()
    user = (form.get("smtp_user") or "").strip()
    frm = (form.get("from_addr") or user).strip()
    pw = form.get("smtp_password") or ""
    if not pw:
        old = saved(app_cfg, existing_user)
        pw = old.password if old else ""
    if not host or not (1 <= port <= 65535) or "@" not in frm:
        return None, "Host, sahi port aur From address (email) zaroori hain."
    if security not in SECURITIES or not (0 <= delay <= 10):
        return None, "Security starttls/ssl/none me se ho aur gap 0-10 second."
    return SmtpConfig(host=host, port=port, user=user, password=pw, from_addr=frm, security=security,
                      delay_seconds=delay), None


def to_record(app_cfg, cfg: SmtpConfig):
    return {"host": cfg.host, "port": cfg.port, "user": cfg.user, "from_addr": cfg.from_addr,
            "security": cfg.security, "delay": cfg.delay_seconds,
            "password_enc": insights.encrypt(app_cfg, cfg.password) if cfg.password else "",
            "updated": time.time()}


def verify(cfg: SmtpConfig):
    """-> 'ok' | 'rejected' (credentials wrong: do not save) | 'unverified' (could not connect: save with warning), message."""
    ok, msg = test_connection(cfg)
    if ok:
        return "ok", msg
    if msg.startswith(("SMTPAuthenticationError", "SMTPSenderRefused", "SMTPNotSupportedError")):
        return "rejected", msg
    return "unverified", msg
