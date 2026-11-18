import io

from PIL import Image, ImageDraw, ImageFilter


def _doc_png(blur: float = 0) -> bytes:
    img = Image.new("RGB", (900, 1200), "white")
    d = ImageDraw.Draw(img)
    for y in range(60, 1150, 28):
        d.text((60, y), "Tab Metformin 500 mg 1-0-1 after food  HbA1c 8.1 %", fill="black")
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    b = io.BytesIO(); img.save(b, "PNG"); return b.getvalue()


def test_quality_flags_blur_dark_small():
    from app.ocr import check as C
    sharp = Image.open(io.BytesIO(_doc_png()))
    assert C.quality_issue(C.quality(sharp)) is None
    assert C.quality_issue(C.quality(Image.open(io.BytesIO(_doc_png(4))))) == "blurry"
    assert C.quality_issue(C.quality(Image.new("RGB", (900, 900), (20, 20, 20)))) == "too_dark"
    assert C.quality_issue(C.quality(sharp.resize((150, 200)))) == "too_small"


async def test_upload_returns_check_and_retake_deletes(client, guest):
    r = await client.post("/documents", files={"file": ("rx.png", _doc_png(4), "image/png")},
                          data={"kind": "prescription"}, headers=guest)
    assert r.status_code == 201
    body = r.json()
    assert body["check"] and body["check"]["issue"] == "blurry"
    assert (await client.delete(f"/documents/{body['id']}", headers=guest)).status_code == 204
    assert (await client.get(f"/documents/{body['id']}", headers=guest)).status_code == 404


def test_clearly_unrelated_photo_is_not_health_before_blurry(monkeypatch):
    from app.ocr import check as C
    monkeypatch.setattr(C, "relevance", lambda img: {"health": 0.07, "top": "a photo of a dog or a cat", "scan": False})
    blurry_pet = Image.open(io.BytesIO(_doc_png(4)))
    assert C.check_image(blurry_pet)["issue"] == "not_health"
    monkeypatch.setattr(C, "relevance", lambda img: {"health": 0.45, "top": "x", "scan": False})
    assert C.check_image(blurry_pet)["issue"] == "blurry"  # borderline: the quality advice comes first


def test_kind_guess_thresholds():
    from app.ocr.check import LAB_SURE, RX_SURE, kind_guess
    rel = lambda lab, scan=False, st=None: {"lab_share": lab, "scan": scan, "scan_type": st}  # noqa: E731
    assert kind_guess(rel(LAB_SURE))["kind"] == "lab_report"
    assert kind_guess(rel(RX_SURE - 0.01))["kind"] == "prescription"
    assert kind_guess(rel(0.7))["kind"] == "unsure"  # between the two: no prompt either way
    assert kind_guess(rel(0.9, True, "chest_xray")) == {"kind": "scan", "scan_type": "chest_xray", "lab_share": 0.9}
    assert kind_guess(rel(0.2, False, "skin"))["kind"] == "scan"
    assert kind_guess(None) is None
