import os, sys, time
pass
from dbp import gemini_client as gc


class FakeResp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data or {}
        self.text = str(data)

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


LIST_MODELS_RESP = {"models": [
    {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-2.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},  # generateContent nahi -> exclude
    {"name": "models/gemini-2.0-flash-exp", "supportedGenerationMethods": ["generateContent"]},
]}


def _reset_cache():
    with gc._model_cache_lock:
        gc._model_cache.update(models=[], fetched_at=0.0, key_fp=None)
    gc._last_used.update(model=None, at=0.0, error=None)


def _setup(monkeypatch, key="test-admin-key"):
    _reset_cache()
    monkeypatch.setenv("GEMINI_ADMIN_API_KEY", key) if key else monkeypatch.delenv("GEMINI_ADMIN_API_KEY", raising=False)


def test_admin_key_configured_reflects_env(monkeypatch):
    _setup(monkeypatch, key="abc123")
    assert gc.admin_key_configured() is True
    _setup(monkeypatch, key=None)
    assert gc.admin_key_configured() is False


def test_list_available_models_filters_and_ranks(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setattr(gc.requests, "get", lambda url, **kw: FakeResp(200, LIST_MODELS_RESP))
    models = gc.list_available_models("k")
    assert "text-embedding-004" not in models          # generateContent na karne wale exclude
    assert models[0] == "gemini-2.5-flash-lite"         # flash-lite family sabse pehle
    assert models.index("gemini-2.5-flash") < models.index("gemini-2.5-pro")  # flash pro se pehle
    assert models[-1] == "gemini-2.5-pro"               # sabse bhaari/kam-free-quota 'pro' sabse peeche
    assert "gemini-2.0-flash-exp" in models[:-1]         # experimental bhi flash family mein upar hi rehta hai


def test_list_available_models_caches_and_force_refreshes(monkeypatch):
    _setup(monkeypatch)
    calls = {"n": 0}
    def fake_get(url, **kw):
        calls["n"] += 1
        return FakeResp(200, LIST_MODELS_RESP)
    monkeypatch.setattr(gc.requests, "get", fake_get)
    gc.list_available_models("k")
    gc.list_available_models("k")           # cached - koi naya network call nahi
    assert calls["n"] == 1
    gc.list_available_models("k", force=True)
    assert calls["n"] == 2


def test_list_available_models_falls_back_when_network_down(monkeypatch):
    _setup(monkeypatch)
    def boom(*a, **k): raise ConnectionError("offline")
    monkeypatch.setattr(gc.requests, "get", boom)
    models = gc.list_available_models("k")
    assert models == gc._STATIC_FALLBACK_MODELS


def test_call_gemini_auto_falls_back_to_next_model_on_429(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setattr(gc, "list_available_models", lambda key, force=False: ["gemini-a", "gemini-b"])
    attempts = []
    def fake_raw(prompt, api_key, model, system_instruction=None, temperature=0.2):
        attempts.append(model)
        if model == "gemini-a":
            raise gc.GeminiError("rate limited", status_code=429)
        return "answer from " + model
    monkeypatch.setattr(gc, "_call_gemini_raw", fake_raw)
    out = gc.call_gemini_auto("hello")
    assert out == "answer from gemini-b"
    assert attempts == ["gemini-a", "gemini-b"]
    assert gc._last_used["model"] == "gemini-b"


def test_call_gemini_auto_raises_generic_error_with_no_key_or_model_leak(monkeypatch):
    _setup(monkeypatch, key="super-secret-key-value")
    monkeypatch.setattr(gc, "list_available_models", lambda key, force=False: ["gemini-a", "gemini-b"])
    def fake_raw(prompt, api_key, model, system_instruction=None, temperature=0.2):
        raise gc.GeminiError(f"boom for {model}", status_code=500)
    monkeypatch.setattr(gc, "_call_gemini_raw", fake_raw)
    try:
        gc.call_gemini_auto("hello")
        assert False, "should have raised"
    except gc.GeminiError as e:
        msg = str(e)
        assert "super-secret-key-value" not in msg
        assert "gemini-a" not in msg and "gemini-b" not in msg


def test_call_gemini_auto_without_any_key_raises_immediately(monkeypatch):
    _setup(monkeypatch, key=None)
    try:
        gc.call_gemini_auto("hello")
        assert False, "should have raised"
    except gc.GeminiError as e:
        assert "super-secret" not in str(e)


def test_shared_ai_debug_status_never_exposes_key_value(monkeypatch):
    _setup(monkeypatch, key="super-secret-key-value")
    monkeypatch.setattr(gc, "list_available_models", lambda key, force=False: ["gemini-a"])
    status = gc.shared_ai_debug_status()
    assert status["admin_key_configured"] is True
    assert "super-secret-key-value" not in str(status)


def test_refresh_shared_models_returns_zero_without_key(monkeypatch):
    _setup(monkeypatch, key=None)
    assert gc.refresh_shared_models() == 0


def test_old_call_gemini_and_settings_functions_untouched(monkeypatch, tmp_path):
    """Purane functions (call_gemini, save/load/clear_settings) bilkul waise hi
    kaam karte hain jaise pehle karte the - naya admin-key system inhe chheda nahi."""
    monkeypatch.setattr(gc, "_SETTINGS_PATH", str(tmp_path / "gemini_settings.json"))
    assert gc.load_saved_settings() == {}
    assert gc.save_settings("mykey", "mymodel") is True
    assert gc.load_saved_settings() == {"api_key": "mykey", "model": "mymodel"}
    assert gc.clear_saved_settings() is True
    assert gc.load_saved_settings() == {}


def test_chatbot_falls_back_to_deterministic_facts_without_any_key(monkeypatch):
    _setup(monkeypatch, key=None)
    from dbp import chatbot
    import pandas as pd
    summary = pd.DataFrame({"B.CODE": [801], "PENDING BILLING": [2]})
    out = chatbot.answer_question("801 ki pending batao", summary, {801: "TEST BRANCH"})
    assert isinstance(out, str) and len(out) > 0
    assert "super-secret" not in out
