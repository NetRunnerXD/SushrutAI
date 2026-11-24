"""Brand → salt (never guessed silently) and the medicine storyline."""
from app.medicine.pack import names_in


def test_names_in_questions():
    assert names_in("Can I take Dolo 650 for fever?") == ["Dolo 650"]
    assert names_in("doctor gave tab Augmentin 625 and Pan-D 40") == ["Augmentin 625", "Pan-D 40"]
    assert names_in("my BP was 140 today") == []


async def test_storyline_from_printed_salt(client, guest, monkeypatch):
    from app.db import SessionLocal
    from app.llm import intent
    from app.medicine import resolve as R
    from app.medicine.storyline import storyline
    from tests.conftest import current_user_for

    async def names(db):
        return {"paracetamol", "ibuprofen", "aceclofenac"}
    monkeypatch.setattr(intent, "_generic_names", names)

    async def no_model(name):
        raise AssertionError("printed salt must not need the model")
    monkeypatch.setattr(R, "_model_salt", no_model)
    cu = await current_user_for(guest)
    async with SessionLocal() as db:
        r = await R.resolve(db, "Dolo 650", printed="Paracetamol IP 650 mg")
        assert r.source == "printed" and r.verified and r.composition[0]["generic"] == "paracetamol"
        st = await storyline(db, cu, r)
    assert st["text"].startswith("The doctor gave Dolo 650.") and "Paracetamol 650 mg" in st["text"]


async def test_unknown_brand_is_unverified_or_unknown(client, guest, monkeypatch):
    from app.db import SessionLocal
    from app.llm import intent
    from app.medicine import resolve as R
    from app.medicine.storyline import storyline
    from tests.conftest import current_user_for

    async def names(db):
        return {"paracetamol"}
    monkeypatch.setattr(intent, "_generic_names", names)

    async def model_says(name):
        return [{"generic": "paracetamol", "strength": 650, "unit": "mg"}]
    monkeypatch.setattr(R, "_model_salt", model_says)
    cu = await current_user_for(guest)
    async with SessionLocal() as db:
        r = await R.resolve(db, "Crocin Advance")
        assert r.source == "model" and not r.verified
        st = await storyline(db, cu, r, given_by_doctor=False)
        assert "probably" in st["text"] and "check the strip" in st["text"]

        async def nothing(name):
            return []
        monkeypatch.setattr(R, "_model_salt", nothing)
        r2 = await R.resolve(db, "Zzyxel 10")
        assert r2.source == "none"
        st2 = await storyline(db, cu, r2)
        assert "could not find the salt" in st2["text"]
