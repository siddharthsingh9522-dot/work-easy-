# -*- coding: utf-8 -*-
"""SMTP se real mail bhejna (region-wise auto mail).

SMTP details source code mein NAHI likhte. Do tareeke:
  1. Streamlit secrets (.streamlit/secrets.toml) ya environment variables:
        SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_SECURITY
     SMTP_SECURITY = "starttls" (port 587, default) | "ssl" (port 465) | "none"
  2. App ke Mail Center page par form se (sirf session mein rehta hai,
     disk par save nahi hota).
"""
import email.message
import smtplib
import ssl
import time
from dataclasses import dataclass


@dataclass
class SmtpConfig:
    host: str = ""
    port: int = 587
    user: str = ""
    password: str = ""
    from_addr: str = ""
    security: str = "starttls"   # starttls | ssl | none
    delay_seconds: float = 1.0   # do mails ke beech gap (server throttling se bachne ke liye)

    def is_complete(self) -> bool:
        return bool(self.host and self.port and self.from_addr)


def config_from_env(getter) -> SmtpConfig:
    """getter(key) -> value ya None. os.environ.get ya st.secrets.get chalega."""
    def g(k, d=""):
        try:
            v = getter(k)
        except Exception:
            v = None
        return d if v in (None, "") else v
    try:
        port = int(g("SMTP_PORT", 587))
    except (TypeError, ValueError):
        port = 587
    user = str(g("SMTP_USER"))
    return SmtpConfig(
        host=str(g("SMTP_HOST")), port=port, user=user,
        password=str(g("SMTP_PASSWORD")),
        from_addr=str(g("SMTP_FROM", user)),
        security=str(g("SMTP_SECURITY", "ssl" if port == 465 else "starttls")).lower(),
    )


def _connect(cfg: SmtpConfig):
    if cfg.security == "ssl":
        server = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=30,
                                  context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(cfg.host, cfg.port, timeout=30)
        server.ehlo()
        if cfg.security == "starttls":
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
    if cfg.user:
        server.login(cfg.user, cfg.password)
    return server


def test_connection(cfg: SmtpConfig):
    """(ok, message). Login tak check karta hai, koi mail nahi bhejta."""
    try:
        s = _connect(cfg)
        s.quit()
        return True, "SMTP connection + login OK"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def build_message(mail: dict, from_addr: str) -> email.message.EmailMessage:
    """mail_generator.build_mail() ke output se EmailMessage (plain + HTML + xlsx)."""
    msg = email.message.EmailMessage()
    msg["From"] = from_addr
    msg["To"] = ", ".join(mail["to_list"])
    if mail.get("cc_list"):
        msg["Cc"] = ", ".join(mail["cc_list"])
    msg["Subject"] = mail["subject"]
    msg.set_content(mail["body"])
    if mail.get("html_body"):
        msg.add_alternative(mail["html_body"], subtype="html")
    if mail.get("attachment_bytes"):
        maintype, _, subtype = (mail.get("attachment_type") or
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet").partition("/")
        msg.add_attachment(mail["attachment_bytes"], maintype=maintype, subtype=subtype,
                           filename=mail.get("attachment_name") or "pending_data.xlsx")
    return msg


def send_batch(cfg: SmtpConfig, mails: list, progress=None) -> list:
    """mails = [mail dict, ...] (har ek mein to_list/cc_list/branch_code...).
    Ek connection reuse hota hai; connection toote to ek baar reconnect.
    Har mail ka result: {branch_code, status: SENT|FAILED, to, cc, error}.
    Ek branch fail hone se baaki nahi rukte."""
    results = []
    server = None
    try:
        server = _connect(cfg)
        for i, mail in enumerate(mails):
            res = {"branch_code": mail.get("branch_code"), "branch_name": mail.get("branch_name"),
                   "to": ", ".join(mail["to_list"]), "cc": ", ".join(mail.get("cc_list", [])),
                   "status": "FAILED", "error": ""}
            msg = build_message(mail, cfg.from_addr)
            rcpts = list(dict.fromkeys(mail["to_list"] + mail.get("cc_list", []) + mail.get("bcc_list", [])))
            for attempt in (1, 2):
                try:
                    refused = server.send_message(msg, from_addr=cfg.from_addr, to_addrs=rcpts)
                    if refused:
                        res["status"] = "PARTIAL"
                        res["error"] = f"Refused by server: {refused}"
                    else:
                        res["status"] = "SENT"
                    break
                except (smtplib.SMTPServerDisconnected, smtplib.SMTPSenderRefused) as e:
                    if attempt == 2:
                        res["error"] = f"{type(e).__name__}: {e}"
                        break
                    try:
                        server = _connect(cfg)     # ek baar reconnect
                    except Exception as ce:
                        res["error"] = f"Reconnect failed: {ce}"
                        break
                except Exception as e:
                    res["error"] = f"{type(e).__name__}: {e}"
                    break
            results.append(res)
            if progress:
                progress(i + 1, len(mails), res)
            if i < len(mails) - 1 and cfg.delay_seconds:
                time.sleep(cfg.delay_seconds)
    except Exception as e:
        # Connection/login hi na bane: bache huye sab FAILED
        done = {r["branch_code"] for r in results}
        for mail in mails:
            if mail.get("branch_code") not in done:
                results.append({"branch_code": mail.get("branch_code"),
                                "branch_name": mail.get("branch_name"),
                                "to": ", ".join(mail["to_list"]), "cc": ", ".join(mail.get("cc_list", [])),
                                "status": "FAILED", "error": f"SMTP connect/login: {type(e).__name__}: {e}"})
    finally:
        try:
            if server:
                server.quit()
        except Exception:
            pass
    return results
