"""Answer-speed setting: stored per account, offered only when enabled, never weakens photo handling."""
from app.config import get_settings
from app.llm.providers import model_options, text_model_for


async def test_answer_speed_setting_round_trip(client, guest):
    s = (await client.get("/me/settings", headers=guest)).json()
    assert s["model_pref"] == "auto" and {"auto", "balanced"} <= set(s["model_options"])
    r = await client.put("/me/settings", json={"model_pref": "fast"}, headers=guest)
    assert r.status_code == 200 and r.json()["model_pref"] == "fast"
    assert (await client.put("/me/settings", json={"model_pref": "turbo"}, headers=guest)).status_code == 422


def test_fast_model_only_when_enabled(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "fast_model_enabled", False)
    assert "fast" not in model_options()
    assert text_model_for("fast") == (s.llm_model, None)  # a saved 'fast' choice falls back to the careful model
    monkeypatch.setattr(s, "fast_model_enabled", True)
    assert "fast" in model_options()
    model, cap = text_model_for("fast")
    assert model == s.fast_llm_model and cap and cap <= 800
    assert text_model_for("balanced") == (s.llm_model, None)


def test_cloud_offered_only_with_a_key(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "groq_api_key", "")
    assert "cloud" not in model_options()
    monkeypatch.setattr(s, "groq_api_key", "x")
    assert "cloud" in model_options()


def test_symptoms_and_photos_always_use_the_careful_model():
    from app.llm.orchestrator import effective_model_pref
    assert effective_model_pref("fast", "symptoms", False) == "balanced"
    assert effective_model_pref("fast", "medicine", True) == "balanced"
    assert effective_model_pref("fast", "nutrition_lifestyle", False) == "fast"
    assert effective_model_pref("cloud", "symptoms", False) == "cloud"


def test_fast_option_on_by_default():
    assert get_settings().fast_model_enabled and "fast" in model_options()


async def test_fast_model_failure_falls_back_to_careful_model(monkeypatch):
    from app.llm import providers as P

    s = get_settings()
    monkeypatch.setattr(s, "fast_model_enabled", True)
    calls = []

    class FakeOllama:
        async def chat(self, model, messages, **kw):
            calls.append(model)
            if model == s.fast_llm_model:
                raise P.ModelBusy("prediction aborted, token repeat limit reached")
            return P.LlmResult(text="{}", provider="ollama", model=model, tier="A", latency_ms=1)

    monkeypatch.setattr(P, "ollama", lambda: FakeOllama())
    r = await P.clinical_chat([{"role": "user", "content": "hi"}], model_pref="fast")
    assert calls == [s.fast_llm_model, s.llm_model] and r.model == s.llm_model


def test_doc_model_only_when_faster_and_configured(monkeypatch):
    from app.llm.providers import doc_model_for
    s = get_settings()
    monkeypatch.setattr(s, "fast_model_enabled", True)
    monkeypatch.setattr(s, "fast_rx_vlm_model", "")
    monkeypatch.setattr(s, "fast_lab_vlm_model", "small-lab-reader")
    assert doc_model_for("fast") == s.rx_vlm_model  # none passed its gate for prescriptions
    assert doc_model_for("fast", "lab_report") == "small-lab-reader"
    monkeypatch.setattr(s, "fast_rx_vlm_model", "small-reader")
    assert doc_model_for("fast") == "small-reader"
    assert doc_model_for("balanced") == s.rx_vlm_model


async def test_lab_report_falls_back_to_default_reader(client, guest, monkeypatch):
    import io

    from PIL import Image

    from app.llm.providers import LlmResult
    from app.routers import reports
    s = get_settings()
    monkeypatch.setattr(s, "fast_model_enabled", True)
    monkeypatch.setattr(s, "fast_lab_vlm_model", "small-reader")
    await client.put("/me/settings", json={"model_pref": "fast"}, headers=guest)
    calls = []

    class FakeOllama:
        async def chat(self, model, messages, **kw):
            calls.append(model)
            text = "" if model == "small-reader" else "Haemoglobin | 10.2 | g/dL | 12-15.5"
            return LlmResult(text=text, provider="ollama", model=model, tier="A", latency_ms=1)

    monkeypatch.setattr(reports, "ollama", lambda: FakeOllama())
    b = io.BytesIO(); Image.new("RGB", (900, 1200), "white").save(b, "PNG")
    doc = (await client.post("/documents", files={"file": ("l.png", b.getvalue(), "image/png")},
                             data={"kind": "lab_report"}, headers=guest)).json()
    r = await client.post("/reports/parse", json={"document_id": doc["id"], "save": False}, headers=guest)
    assert r.status_code == 200 and calls == ["small-reader", s.rx_vlm_model] and r.json()["model"] == s.rx_vlm_model


def test_instant_mode_falls_back_until_a_model_passes(monkeypatch):
    from app.llm.orchestrator import effective_model_pref
    from app.llm.providers import doc_model_for
    s = get_settings()
    monkeypatch.setattr(s, "instant_model_enabled", True)
    monkeypatch.setattr(s, "fast_model_enabled", True)
    assert "instant" in model_options()
    monkeypatch.setattr(s, "instant_llm_model", "")
    assert text_model_for("instant") == (s.fast_llm_model, 500)  # none passed yet: the Faster model, short answers
    monkeypatch.setattr(s, "instant_llm_model", "tiny-chat")
    assert text_model_for("instant")[0] == "tiny-chat"
    monkeypatch.setattr(s, "instant_lab_vlm_model", ""); monkeypatch.setattr(s, "fast_lab_vlm_model", "small-lab")
    monkeypatch.setattr(s, "instant_rx_vlm_model", ""); monkeypatch.setattr(s, "fast_rx_vlm_model", "")
    assert doc_model_for("instant", "lab_report") == "small-lab" and doc_model_for("instant") == s.rx_vlm_model
    assert effective_model_pref("instant", "symptoms", False) == "balanced"
    assert effective_model_pref("instant", "nutrition_lifestyle", True) == "balanced"
    assert effective_model_pref("instant", "nutrition_lifestyle", False) == "instant"


def test_auto_mode_routes_by_question():
    from app.llm.orchestrator import effective_model_pref as e
    assert e("auto", "symptoms", False) == "balanced"
    assert e("auto", "medicine", False) == "balanced"
    assert e("auto", "nutrition_lifestyle", True) == "balanced"   # a photo always gets the careful model
    assert e("auto", "nutrition_lifestyle", False) == "fast"
    assert e("auto", "greeting", False) == "instant"
    assert e("fast", "symptoms", False) == "balanced"            # unchanged safety rule for manual choices


async def test_model_pref_per_message_is_validated(client, guest):
    sid = (await client.post("/chat/sessions", json={"lang": "en"}, headers=guest)).json()["id"]
    r = await client.post(f"/chat/sessions/{sid}/messages", json={"text": "hi", "model_pref": "turbo"}, headers=guest)
    assert r.status_code == 422
