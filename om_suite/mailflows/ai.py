"""Optional AI helpers (Gemini via the shared server key). Everything works without AI: the human 'Reviewed' gate remains."""
import json
import re

from dbp import gemini_client


def enabled() -> bool:
    return gemini_client.admin_key_configured()


def _parse_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"AI ne valid JSON return nahi kiya: {text[:300]}")
    return json.loads(text[start:end + 1])


def check_prompt(module_label, code, name, region, email, contact, designation, item_count, sample_items):
    return f"""Tum ek logistics company (Om Logistics) ke internal admin ho jo "{module_label}" mail ko
bhejne se pehle check karta hai.

Branch Code: {code}
Branch Naam: {name}
Region: {region}
Bheja jaane wala email: {email}
Contact: {contact or 'N/A'} — Designation: {designation or 'N/A'}
Item Count: {item_count}
Sample: {json.dumps(sample_items, ensure_ascii=False, default=str)}

Check karo: (1) email format/domain sahi hai, (2) designation is kaam ke liye plausible hai
(Security Guard/Cook/Driver jaisi role galat lagegi), (3) data garbage to nahi.

Sirf JSON return karo, kuch aur text nahi:
{{"status": "ok" ya "review", "reason": "chhoti Hindi/Hinglish line"}}"""


def run_check(prompt):
    if not enabled():
        return {"status": "unavailable", "reason": "AI configure nahi hai - manually verify karke 'Reviewed' mark karein."}
    try:
        parsed = _parse_json(gemini_client.call_gemini_auto(prompt))
        status = str(parsed.get("status", "review")).lower()
        return {"status": status if status in ("ok", "review") else "review", "reason": str(parsed.get("reason", "")).strip()}
    except Exception as exc:  # noqa: BLE001 - the UI shows the reason, the row stays un-reviewed
        return {"status": "ai_error", "reason": f"AI check failed: {exc}"}


def template_prompt(old_subject, old_body, sample_row, columns):
    return f"""Tum ek email-template extraction assistant ho. Neeche ek PURANI email ka
subject aur body diya hai, aur NAYE DATA ka ek sample row (jisse naya mail banana hai).

PURANI EMAIL — Subject:
{old_subject}

PURANI EMAIL — Body:
{old_body[:3000]}

NAYE DATA ke columns: {json.dumps(columns, ensure_ascii=False)}
NAYE DATA ka ek sample row: {json.dumps(sample_row, ensure_ascii=False, default=str)}

Kaam: Purani email ke subject aur body ko ek TEMPLATE mein convert karo, jahan jo bhi
specific values (jaise party name, bill number, branch name, date, amount) the unko
{{{{Column Name}}}} placeholders se replace karo — sirf un columns ka naam use karo jo
"NAYE DATA ke columns" list mein di hai. Baaki fixed text (greeting, instructions,
signature) bilkul waisa hi rakho jaisa tha.

Sirf JSON return karo, kuch aur text nahi:
{{"subject_template": "...", "body_template": "...", "confidence": 0-100, "notes": "Hindi/Hinglish mein chhoti si explanation ki kya-kya replace kiya"}}"""


def extract_template(old_subject, old_body, sample_row, columns):
    if not enabled():
        return {"subject_template": old_subject, "body_template": old_body, "confidence": 0,
                "notes": "AI configure nahi hai - purani mail hoobahu copy ki gayi hai, aapko manually {{Column}} placeholders lagane honge."}
    try:
        out = _parse_json(gemini_client.call_gemini_auto(template_prompt(old_subject, old_body, sample_row, columns)))
        out.setdefault("subject_template", old_subject)
        out.setdefault("body_template", old_body)
        out.setdefault("confidence", 50)
        out.setdefault("notes", "")
        return out
    except Exception as e:  # noqa: BLE001
        return {"subject_template": old_subject, "body_template": old_body, "confidence": 0,
                "notes": f"AI template extraction fail hui: {e}. Manually placeholders edit kar lein."}
