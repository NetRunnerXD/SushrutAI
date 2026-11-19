import io

from PIL import Image

from app.vision import screen as S
from app.vision.specialists.base import Finding, Specialist


class FakeChest(Specialist):
    def load(self):
        return object()

    def predict(self, model, img):
        return [Finding("Lung", "hazy lungs", "धुंधले फेफड़े", 0.8, True),
                Finding("Effusion", "possible fluid around the lungs", "पानी", 0.1, False)]


def _png() -> bytes:
    b = io.BytesIO(); Image.new("RGB", (600, 600), (90, 90, 90)).save(b, "PNG"); return b.getvalue()


async def test_screen_and_prompt(monkeypatch):
    fake = FakeChest("chest X-ray screening model", "fake", ("chest_xray",), "test")
    monkeypatch.setattr(S, "for_scan_type", lambda t: fake if t == "chest_xray" else None)
    sc = await S.screen(Image.new("RGB", (300, 300)), "chest_xray")
    assert [f["key"] for f in sc["flagged"]] == ["Lung"] and not sc["none_flagged"]
    assert "It flagged: hazy lungs" in S.prompt_block(sc) and "do not state a diagnosis" in S.prompt_block(sc)
    assert S.for_person(sc, "hi")["flagged"] == [{"key": "Lung", "text": "धुंधले फेफड़े", "band": None, "overlay": None,
                                                  "region": None}]
    assert await S.screen(Image.new("RGB", (300, 300)), "ct") is None  # no specialist: skipped


async def test_missing_model_fails_open():
    class Broken(FakeChest):
        def load(self):
            raise ImportError("not installed")
    assert Broken("x", "x", ("chest_xray",), "x").run(Image.new("RGB", (10, 10))) is None


async def test_analyze_returns_screening_and_tells_medgemma(client, guest, monkeypatch):
    from app.routers import reports

    fake = FakeChest("chest X-ray screening model", "fake", ("chest_xray",), "test")
    monkeypatch.setattr(S, "for_scan_type", lambda t: fake if t == "chest_xray" else None)
    prompts = []

    class FakeOllama:
        async def chat(self, model, messages, **kw):
            from app.llm.providers import LlmResult
            prompts.append(messages[0]["content"])
            text = "chest_xray" if len(prompts) == 1 else "The lungs look a little hazy."
            return LlmResult(text=text, provider="ollama", model=model, tier="A", latency_ms=1)

    monkeypatch.setattr(reports, "ollama", lambda: FakeOllama())
    doc = (await client.post("/documents", files={"file": ("x.png", _png(), "image/png")}, data={"kind": "xray"},
                             headers=guest)).json()
    r = await client.post("/images/analyze", json={"document_id": doc["id"]}, headers=guest)
    assert r.status_code == 200
    body = r.json()
    assert body["screening"] == {"model": "chest X-ray screening model", "scan_type": "chest_xray",
                                 "flagged": [{"key": "Lung", "text": "hazy lungs", "band": None, "overlay": None,
                                              "region": None}], "none_flagged": False}
    assert "It flagged: hazy lungs" in prompts[1]



def test_real_chest_model_gives_band_and_heatmap():
    """The installed TorchXRayVision model on a real pneumonia film: lung finding banded, picture rendered."""
    import pytest
    pq = pytest.importorskip("pyarrow.parquet")
    from pathlib import Path

    from app.vision.specialists.chest_xray import CHEST_XRAY
    data = Path(__file__).resolve().parents[3] / "data" / "eval" / "cxr_kermany" / "test.parquet"
    if not data.exists() or not CHEST_XRAY.available():
        pytest.skip("chest X-ray model or eval data not present")
    import asyncio
    rows = pq.read_table(data).to_pylist()
    r = next(x for x in rows[1::2] if x["label"] == 1)
    sc = asyncio.run(S.screen(Image.open(io.BytesIO(r["image"]["bytes"])), "chest_xray"))
    lung = next(f for f in sc["flagged"] if f["key"] == "Lung")
    assert lung["band"] in ("possible", "likely") and lung["overlay"].startswith("data:image/jpeg;base64,")


def test_users_framed_xray_heat_stays_on_the_lungs():
    """Regression for a published film with a white paper frame, burned-in text and a ruler: the map must not colour
    the frame or text, and its strongest point must lie inside a lung."""
    import pytest
    from pathlib import Path

    from app.vision.specialists.anatomy import ANATOMY, trim_frame
    from app.vision.specialists.chest_xray import CHEST_XRAY
    p = Path(__file__).resolve().parents[3] / "eval" / "samples" / "user_cxr_portable.webp"
    if not p.exists() or not CHEST_XRAY.available() or not ANATOMY.available():
        pytest.skip("sample or models not present")
    import numpy as np
    img = Image.open(p).convert("RGB")
    trimmed, (l, t, r, b) = trim_frame(img)
    assert (l, t) > (5, 5)  # the white frame was found
    heat, outline = CHEST_XRAY.explain(img, "Edema", "occlusion")
    assert heat[:t, :].max() == 0 and heat[:, :l].max() == 0  # frame never coloured
    assert heat[:, int(img.width * 0.86):].max() < 0.5  # the text/ruler strip stays uncoloured (bands start at 0.5)
    y, x = np.unravel_index(int(heat.argmax()), heat.shape)
    assert outline is not None and outline[y, x]



def test_every_flagged_chest_finding_gets_a_region_with_its_reliability():
    """Real models on the user's framed film: each flagged finding has a picture and says how far to trust it."""
    import asyncio
    import pytest
    from pathlib import Path

    from app.vision.specialists.anatomy import ANATOMY
    from app.vision.specialists.chest_xray import CHEST_XRAY
    p = Path(__file__).resolve().parents[3] / "eval" / "samples" / "user_cxr_portable.webp"
    if not p.exists() or not CHEST_XRAY.available() or not ANATOMY.available():
        pytest.skip("sample or models not present")
    sc = asyncio.run(S.screen(Image.open(p).convert("RGB"), "chest_xray"))
    assert sc["flagged"], "this film flags at least one finding"
    for f in sc["flagged"]:
        assert f["overlay"] and f["overlay"].startswith("data:image/jpeg")
        assert f["region"] == S.REGION_QUALITY[f["key"]]
