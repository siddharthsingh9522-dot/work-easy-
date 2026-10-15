# -*- coding: utf-8 -*-
"""
gemini_client.py
Gemini API ko seedha REST se call karta hai (google-generativeai SDK ki
zaroorat nahi, sirf 'requests' chahiye -- lightweight aur portable).

Aapki API key kabhi bhi source-CODE mein hardcode NAHI hai. Default
session-only rehti hai (sidebar mein daalni padti hai). Agar user
sidebar ka "yaad rakho" checkbox on kare, to key data/gemini_settings.json
mein save ho jaati hai (isi machine par) taaki agli baar app khulte hi
khud-ba-khud bhar jaaye -- yeh SIRF checkbox on karne par hota hai, kabhi
apne aap nahi.
"""
import json
import os
import requests

from .config import DEFAULT_GEMINI_MODEL, GEMINI_API_BASE

_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "data", "gemini_settings.json")


class GeminiError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def load_saved_settings() -> dict:
    """Pehle se saved Gemini API key + model (agar user ne 'yaad rakho'
    checkbox se save kiya tha) load karta hai. Kuch na mile to khaali dict."""
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(api_key: str, model: str = None) -> bool:
    """Gemini API key (+ model) ko is machine par local file mein save karta
    hai, taaki app agli baar khulte hi khud-ba-khud bhar jaaye. NOTE: file
    PLAINTEXT hai -- isko kabhi Git/shared-drive/cloud-sync folder mein
    commit ya upload mat karna, sirf apne local system tak seemit rakhna."""
    try:
        os.makedirs(os.path.dirname(_SETTINGS_PATH), exist_ok=True)
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump({"api_key": api_key or "", "model": model or ""}, f)
        return True
    except Exception:
        return False


def clear_saved_settings() -> bool:
    """Saved key file delete kar deta hai (sidebar ke 'bhool jao' button ke liye)."""
    try:
        if os.path.exists(_SETTINGS_PATH):
            os.remove(_SETTINGS_PATH)
        return True
    except Exception:
        return False


def call_gemini(prompt: str, api_key: str, model: str = None,
                 system_instruction: str = None, temperature: float = 0.2) -> str:
    """Ek single prompt Gemini ko bhejta hai aur text response return karta hai."""
    if not api_key:
        raise GeminiError("Gemini API key nahi di gayi. Sidebar mein daalo.")
    model = model or DEFAULT_GEMINI_MODEL
    url = f"{GEMINI_API_BASE}/{model}:generateContent?key={api_key}"

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    if system_instruction:
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    try:
        resp = requests.post(url, json=payload, timeout=60)
    except requests.RequestException as e:
        raise GeminiError(f"Network error Gemini API call karte waqt: {e}")

    if resp.status_code != 200:
        # Model-not-found jaisa common error yaha clearly dikhado
        raise GeminiError(
            f"Gemini API error {resp.status_code}: {resp.text[:500]}\n"
            f"(Agar 'model not found' likha hai to sidebar mein model naam check karo — "
            f"https://aistudio.google.com/ par apne account ke available models dekho)"
        )

    data = resp.json()
    try:
        candidates = data["candidates"]
        parts = candidates[0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError):
        raise GeminiError(f"Gemini se unexpected response mila: {data}")


def test_connection(api_key: str, model: str = None) -> tuple:
    """Sidebar mein ek 'Test Connection' button ke liye -- (True, msg) ya (False, error)."""
    try:
        out = call_gemini("Reply with exactly one word: OK", api_key, model, temperature=0)
        return True, out.strip()
    except GeminiError as e:
        return False, str(e)


# =====================================================================
#  ADMIN SHARED KEY + MULTI-MODEL AUTO-FALLBACK  (naya, purana kuch nahi
#  badla - call_gemini/test_connection/save_settings sab bilkul waise hi
#  kaam karte hain jaise pehle karte the)
# =====================================================================
# Design:
#  1. Admin apni Gemini key EK BAAR secrets.toml/environment mein
#     GEMINI_ADMIN_API_KEY se de deta hai (Pexels/Pixabay keys jaisa hi
#     pattern). Yeh key kabhi UI mein, kisi error message mein, ya kahin
#     bhi user ko text ke roop mein nahi dikhti - sirf "configured
#     hai/nahi" (bool) dikhta hai.
#  2. Normal user ko kabhi apni Gemini key daalne ki zaroorat nahi -
#     har chatbot/mail call khud-ba-khud admin ki shared key use karti
#     hai, bina kisi UI signal ke ki kaunsi key/model chal raha hai.
#  3. Available models Google ke ListModels API se LIVE fetch hote hain
#     (1 ghante cache) - isliye Google jab naya free model launch kare ya
#     purana retire kare, agle refresh mein apne aap list update ho jaati
#     hai, code kabhi badalna nahi padta.
#  4. Ek model rate-limit (429) khaye, retire (404) ho jaaye, ya kisi bhi
#     wajah se fail ho, to turant agla available model try hota hai -
#     poori list khatam hone par hi user ko pata chalta hai (generic
#     message, model/key ka naam kabhi nahi).
import threading
import time

GEMINI_LIST_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Network/key-issue mein ListModels fail ho jaye to yeh hardcoded list hi
# safety-net hai, taaki AI feature bilkul band na ho jaaye. Yeh sirf
# fallback hai - normal operation mein list_available_models() hamesha
# Google se LIVE list laata hai.
_STATIC_FALLBACK_MODELS = [
    "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash",
    "gemini-2.0-flash-lite", "gemini-1.5-flash", "gemini-1.5-flash-8b",
]
_PREFERRED_FAMILY_ORDER = ["flash-lite", "flash-8b", "flash", "pro"]
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_model_cache = {"models": [], "fetched_at": 0.0, "key_fp": None}
_model_cache_lock = threading.Lock()
_MODEL_CACHE_TTL_SECONDS = 3600
_last_used = {"model": None, "at": 0.0, "error": None}  # admin-debug ke liye


def _get_secret(name: str):
    val = os.environ.get(name)
    if val:
        return val
    return None


def admin_key_configured() -> bool:
    """Sirf True/False - key ki value kabhi bahar nahi jaati."""
    return bool(_get_secret("GEMINI_ADMIN_API_KEY"))


def _admin_key():
    return _get_secret("GEMINI_ADMIN_API_KEY")


def _rank_model(name: str) -> tuple:
    """Chhote/tez 'flash' family (zyada free-tier quota) pehle try hote
    hain, bhaari 'pro' models sabse peeche (bas fallback ke liye).
    Embedding/vision/tts jaisे non-chat models sabse aakhir."""
    n = name.lower()
    if any(x in n for x in ("embedding", "aqa", "vision", "tts", "image-generation")):
        return (99, 0, n)
    fam = next((i for i, hint in enumerate(_PREFERRED_FAMILY_ORDER) if hint in n),
               len(_PREFERRED_FAMILY_ORDER))
    is_exp = 1 if any(x in n for x in ("exp", "preview", "thinking")) else 0
    return (fam, is_exp, n)


def list_available_models(api_key: str, force: bool = False) -> list:
    """Is key ke liye ABHI generateContent support karne wale models,
    Google se live (1-hour cache). Google naya model launch/retire kare
    to agle refresh mein khud-ba-khud reflect hota hai."""
    key_fp = (api_key or "")[-6:]
    with _model_cache_lock:
        fresh = (_model_cache["models"] and _model_cache["key_fp"] == key_fp
                  and (time.time() - _model_cache["fetched_at"]) < _MODEL_CACHE_TTL_SECONDS)
        if fresh and not force:
            return list(_model_cache["models"])
    try:
        resp = requests.get(GEMINI_LIST_MODELS_URL, params={"key": api_key}, timeout=15)
        resp.raise_for_status()
        names = []
        for m in resp.json().get("models", []):
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            nm = str(m.get("name", "")).split("/")[-1]
            if nm:
                names.append(nm)
        names = sorted(set(names), key=_rank_model)
        if names:
            with _model_cache_lock:
                _model_cache.update(models=names, fetched_at=time.time(), key_fp=key_fp)
            return names
    except Exception:
        pass
    with _model_cache_lock:
        if _model_cache["models"]:
            return list(_model_cache["models"])
    return list(_STATIC_FALLBACK_MODELS)


def _call_gemini_raw(prompt, api_key, model, system_instruction=None, temperature=0.2):
    """call_gemini() jaisa hi request, bas GeminiError.status_code bhi
    attach karta hai taaki auto-fallback ko pata chale retry karna hai ya
    nahi. Purane call_gemini() ko iske liye badla nahi gaya."""
    url = f"{GEMINI_API_BASE}/{model}:generateContent"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    if system_instruction:
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
    try:
        resp = requests.post(url, params={"key": api_key}, json=payload, timeout=60)
    except requests.RequestException as e:
        raise GeminiError(f"Network error: {e}")
    if resp.status_code != 200:
        raise GeminiError(f"HTTP {resp.status_code}", status_code=resp.status_code)
    data = resp.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError):
        raise GeminiError("Unexpected response shape")


def call_gemini_auto(prompt: str, system_instruction: str = None, temperature: float = 0.2,
                      user_api_key: str = None, user_model: str = None) -> str:
    """Chatbot/mail jaisे features ke liye single entrypoint.
    - user_api_key/user_model diya ho to wahi use hota hai (kisi admin ke
      manual test jaisi situation ke liye) - warna admin ki hidden shared
      key + har available free model try hota hai jab tak jawab na mile.
    - Is function ke bahar (return value ya raised error mein) kabhi bhi
      key ya model ka naam nahi jaata - saari messaging generic hai."""
    api_key = user_api_key or _admin_key()
    if not api_key:
        raise GeminiError("AI service abhi configure nahi hai.")

    models = [user_model] if user_model else list_available_models(api_key)
    if not models:
        models = list(_STATIC_FALLBACK_MODELS)

    last_err = None
    for m in models:
        try:
            out = _call_gemini_raw(prompt, api_key, m, system_instruction, temperature)
            _last_used.update(model=m, at=time.time(), error=None)
            return out
        except GeminiError as e:
            last_err = e
            _last_used.update(model=m, at=time.time(), error=str(e))
            continue  # koi bhi wajah se fail ho, agla model try karo

    raise GeminiError("AI service abhi jawab nahi de paa raha, thodi der baad try karein.")


def refresh_shared_models() -> int:
    """Admin ke 'Model list refresh karo' button ke liye - key ki value
    app.py mein kabhi nahi aati, sab kuch yahin andar handle hota hai.
    Returns: kitne models mile (0 = key missing ya fetch fail)."""
    key = _admin_key()
    if not key:
        return 0
    return len(list_available_models(key, force=True))


def shared_ai_debug_status() -> dict:
    """Admin Panel ke liye chhota diagnostic - kabhi key ki value nahi,
    sirf configured/nahi + kitne models mile + aakhri call ka status."""
    key = _admin_key()
    models = list_available_models(key) if key else []
    return {
        "admin_key_configured": bool(key),
        "available_models_count": len(models),
        "available_models": models[:12],
        "last_used_model": _last_used["model"],
        "last_used_at": (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_last_used["at"]))
                          if _last_used["at"] else None),
        "last_error": _last_used["error"],
    }
