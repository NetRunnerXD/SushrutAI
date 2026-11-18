"""Lab reports: values wait for review, are saved on confirm (flags recomputed), can be re-reviewed, and the whole
report can be deleted (file + values). Other people's reports are untouched."""
from pathlib import Path

from app.config import get_settings
from app.models import Document


async def _doc(client, h, kind="lab_report"):
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buf, format="PNG")
    r = await client.post("/documents", files={"file": ("r.png", buf.getvalue(), "image/png")}, data={"kind": kind}, headers=h)
    assert r.status_code == 201
    return r.json()["id"]


async def test_confirm_edit_and_delete(client, guest):
    doc = await _doc(client, guest)
    lib = (await client.get("/reports/library", headers=guest)).json()
    assert lib[0]["id"] == doc and lib[0]["reviewed"] is False
    body = {"report_date": "2026-11-01", "title": "City Lab",
            "results": [{"test_name": "Haemoglobin", "value": 10.2, "unit": "g/dL", "ref_low": 12, "ref_high": 15},
                        {"test_name": "TSH", "value": 2.1, "unit": "mIU/L", "ref_low": 0.4, "ref_high": 4.0}]}
    r = await client.post(f"/reports/{doc}/confirm", json=body, headers=guest)
    assert r.json() == {"saved": 2, "abnormal": 1}
    labs = (await client.get("/reports/labs", headers=guest)).json()
    assert {(x["test_name"], x["flag"]) for x in labs} == {("Haemoglobin", "L"), ("TSH", "N")}
    assert labs[0]["observed_at"].startswith("2026-11-01")
    # re-review: corrected value replaces the earlier ones
    body["results"] = [{"test_name": "Haemoglobin", "value": 12.5, "unit": "g/dL", "ref_low": 12, "ref_high": 15}]
    await client.post(f"/reports/{doc}/confirm", json=body, headers=guest)
    labs = (await client.get("/reports/labs", headers=guest)).json()
    assert [(x["test_name"], x["flag"]) for x in labs] == [("Haemoglobin", "N")]
    rep = (await client.get(f"/reports/{doc}", headers=guest)).json()
    assert rep["reviewed"] and rep["title"] == "City Lab" and rep["values"][0]["value"] == 12.5
    lib = (await client.get("/reports/library", headers=guest)).json()
    assert lib[0]["reviewed"] and lib[0]["values"] == 1
    # delete: values and the stored file go
    assert (await client.delete(f"/reports/{doc}", headers=guest)).status_code == 204
    assert (await client.get("/reports/labs", headers=guest)).json() == []
    assert (await client.get(f"/reports/{doc}", headers=guest)).status_code == 404


async def test_other_users_report_is_not_reachable(client, guest):
    doc = await _doc(client, guest)
    other = {"Authorization": "Bearer " + (await client.post("/auth/guest")).json()["access_token"]}
    await client.post("/consents", json={"purposes": ["core_service", "history_storage"]}, headers=other)
    assert (await client.get(f"/reports/{doc}", headers=other)).status_code == 404
    assert (await client.delete(f"/reports/{doc}", headers=other)).status_code == 404
    assert (await client.post(f"/reports/{doc}/confirm", json={"results": []}, headers=other)).status_code == 404
    assert (await client.get(f"/reports/{doc}", headers=guest)).status_code == 200


async def test_bad_date_rejected(client, guest):
    doc = await _doc(client, guest)
    r = await client.post(f"/reports/{doc}/confirm", json={"results": [], "report_date": "1st Nov"}, headers=guest)
    assert r.status_code == 422
