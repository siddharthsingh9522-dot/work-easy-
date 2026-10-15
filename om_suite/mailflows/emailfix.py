"""Deterministic e-mail clean-up used by 'AI Fix' (no AI needed): whitespace/case + known-domain typo correction."""
import difflib
import re


def known_domains(directory) -> set:
    out = set()
    for entries in (directory or {}).values():
        for e in entries:
            for em in (e.get("emails") or []):
                if "@" in em:
                    out.add(em.split("@", 1)[1].strip().lower())
    return out


def autofix(raw_email, directory):
    """-> (fixed string or None, notes). None = nothing worth fixing (e.g. 'omlogistic.co.in' -> 'omlogistics.co.in')."""
    if not raw_email or not raw_email.strip():
        return None, []
    valid = known_domains(directory)
    notes, changed, parts = [], False, []
    for part in re.split(r"[,;\n]+", raw_email):
        p = part.strip()
        if not p:
            continue
        p2 = p.lower().replace(" ", "")
        changed |= p2 != p
        if "@" in p2:
            local, domain = p2.rsplit("@", 1)
            if valid and domain not in valid:
                close = difflib.get_close_matches(domain, valid, n=1, cutoff=0.72)
                if close and close[0] != domain:
                    notes.append(f"'{domain}' ko '{close[0]}' se badla")
                    domain, changed = close[0], True
            p2 = f"{local}@{domain}"
        parts.append(p2)
    if not parts or not changed:
        return None, []
    return ", ".join(dict.fromkeys(parts)), notes
