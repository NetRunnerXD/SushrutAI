from app.models import Document, Job, Prescription


def test_new_rows_have_ids_before_insert():
    d1, d2 = Document(user_id="u", kind="other", object_key="", sha256="x", mime="image/png"), Document(
        user_id="u", kind="other", object_key="", sha256="y", mime="image/png")
    assert d1.id and d2.id and d1.id != d2.id  # upload file names depend on this
    assert Prescription(user_id="u", parsed_enc=b"").id and Job(user_id="u", kind="k", progress=[]).id
    assert Document(id="fixed", user_id="u", kind="other", object_key="", sha256="z", mime="image/png").id == "fixed"


async def test_uploads_get_separate_files(client, guest):
    import io

    from PIL import Image

    ids = []
    for colour in ("red", "blue"):
        b = io.BytesIO(); Image.new("RGB", (400, 400), colour).save(b, "PNG")
        r = await client.post("/documents", files={"file": ("p.png", b.getvalue(), "image/png")},
                              data={"kind": "other"}, headers=guest)
        ids.append(r.json()["id"])
    a = (await client.get(f"/documents/{ids[0]}/content", headers=guest)).content
    b = (await client.get(f"/documents/{ids[1]}/content", headers=guest)).content
    assert a != b  # each upload kept its own file
