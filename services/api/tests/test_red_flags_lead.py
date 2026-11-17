from app.safety import red_flags as rf


def test_other_language_gets_native_call_to_action_first():
    r = rf.check("நெஞ்சு வலி", lang="ta")
    assert r.triggered and r.kind == "emergency"
    assert r.messages[0] == rf.LEAD["ta"]["emergency"] and "108" in r.messages[0]
    assert len(r.messages) == 2  # native lead + the detailed English message


def test_en_and_hi_unchanged():
    assert rf.check("chest pain", lang="en").messages[0].startswith("Chest pain")
    assert rf.check("सीने में दर्द", lang="hi").messages[0].startswith("सीने")


def test_every_lead_language_covers_every_kind():
    for lang, m in rf.LEAD.items():
        assert set(m) == set(rf._SEVERITY), lang
