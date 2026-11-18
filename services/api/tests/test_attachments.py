"""Chat attachment routing: which reader a photo or PDF goes to (the vision models are replaced by fakes here;
eval/doc_type_eval.py measures the real ones)."""
import pytest

from app.ocr import images
from app.routers import chat


def test_reply_contract_used_by_the_web_app():
    r = chat.attachment_reply("prescription", "d1", "en")
    assert r["kind"] == "attachment" and r["actions"] == [{"key": "readThisRx", "href": "/medicines/rx?doc=d1"}]
    r = chat.attachment_reply("lab_report", "d1", "hi")
    assert r["actions"] == [{"key": "readThisReport", "href": "/records?tab=labs&doc=d1"}] and "रिपोर्ट" in r["reply"]
    assert chat.attachment_reply("scan", "d1", "en")["actions"][0]["key"] == "describeScan"
    assert chat.attachment_reply("photo", "d1", "en") is None  # answered by the vision model in chat


def test_unsure_document_offers_both_readers():
    r = chat.attachment_reply("unsure_document", "d9", "en")
    assert {a["key"] for a in r["actions"]} == {"readThisRx", "readThisReport"}
    assert all("doc=d9" in a["href"] for a in r["actions"]) and "?" in r["reply"]


@pytest.mark.parametrize("scan,lab,rx,expected", [
    (False, True, False, "lab_report"), (False, False, True, "prescription"), (True, False, False, "scan"),
    (False, False, False, "photo"), (True, True, False, "lab_report"), (False, True, True, None), (True, True, True, None),
])
def test_feature_decision(scan, lab, rx, expected):
    assert images._decide(scan, lab, rx) == expected


async def test_conflicting_features_fall_back_to_page_words(monkeypatch):
    async def both(b64):
        return {"scan": False, "lab": True, "rx": True}
    monkeypatch.setattr(images, "_features", both)

    async def unclear(b64):
        return {"kind": "unsure_document"}
    monkeypatch.setattr(images, "_v_direct", unclear)

    async def rx_words(b64):
        return 1, 5
    monkeypatch.setattr(images, "_keywords", rx_words)
    assert (await images.classify_attachment("x"))["kind"] == "prescription"

    async def even(b64):
        return 2, 3
    monkeypatch.setattr(images, "_keywords", even)
    assert (await images.classify_attachment("x"))["kind"] == "unsure_document"


async def test_scan_needs_medgemma_to_agree(monkeypatch):
    async def says_scan(b64):
        return {"kind": "scan"}
    monkeypatch.setattr(chat, "classify_attachment", says_scan)

    async def medgemma_xray(b64):
        return "chest_xray"
    monkeypatch.setattr(chat, "classify_modality", medgemma_xray)
    assert await chat.detect_attachment("x") == "scan"

    async def medgemma_document(b64):
        return "document"
    monkeypatch.setattr(chat, "classify_modality", medgemma_document)
    assert await chat.detect_attachment("x") == "photo"  # never the X-ray describer on one model's word


def test_model_errors_carry_a_plain_next_step():
    from app.llm.providers import ModelBusy, ModelMissing, ProviderUnavailable
    assert "busy" in ModelBusy("x").hint and "try again" in ModelBusy("x").hint
    assert "--pull" in ModelMissing("x").hint
    assert "not running" in ProviderUnavailable("x").hint
    assert issubclass(ModelBusy, ProviderUnavailable)  # every existing `except ProviderUnavailable` still catches it


def test_ollama_server_error_is_reported_as_busy():
    """Ollama answers 500 when a model fails to load (GPU busy swapping models) — the person should see
    'busy, try again', not a raw HTTP error."""
    import httpx
    from app.llm.providers import ModelBusy, _raise_for_server_error

    r = httpx.Response(500, text='{"error":"model runner has unexpectedly stopped"}', request=httpx.Request("POST", "http://x"))
    with pytest.raises(ModelBusy):
        _raise_for_server_error("m", r)
    _raise_for_server_error("m", httpx.Response(200, request=httpx.Request("POST", "http://x")))  # no error


@pytest.mark.parametrize("scan,lab,rx,direct,expected", [
    (False, False, False, "lab_report", "unsure_document"),   # yes/no missed the document: offer both readers
    (False, False, False, "unsure_document", "photo"),
    (False, True, False, "prescription", "prescription"),       # the direct question decides which document
    (True, True, False, "lab_report", "lab_report"),
    (True, False, False, "prescription", "scan"),
    (False, True, False, "unsure_document", "lab_report"),
])
def test_hybrid_decision(scan, lab, rx, direct, expected):
    assert images.decide_hybrid({"scan": scan, "lab": lab, "rx": rx}, direct) == expected


async def test_direct_question_names_the_document(monkeypatch):
    async def lab_only(b64):
        return {"scan": False, "lab": True, "rx": False}

    async def says_rx(b64):
        return {"kind": "prescription"}
    monkeypatch.setattr(images, "_features", lab_only)
    monkeypatch.setattr(images, "_v_direct", says_rx)
    assert (await images.classify_attachment("x"))["kind"] == "prescription"
