from app.llm.orchestrator import tidy_reply


def test_drops_blocks_that_repeat_the_lists():
    t = ("Fever for **2 days** is often viral.\n\nRest and drink fluids.\n\n"
         "**Self-care:** Rest well, drink fluids.\n\n**Watch for:** Severe dehydration.")
    out, changed = tidy_reply(t)
    assert changed
    assert "Self-care" not in out and "Watch for" not in out
    assert out == "Fever for **2 days** is often viral.\n\nRest and drink fluids."


def test_shortens_runaway_lists_and_caps_sentences():
    t = ("Your symptoms can be seen with dengue, malaria, typhoid, TB, anaemia, diabetes, hypertension, chickenpox. "
         "One. Two.\n\nThree. Four. Five. Six. Seven.")
    out, changed = tidy_reply(t)
    assert changed
    assert "chickenpox" not in out and "and others." in out
    assert out.count(".") <= 8  # at most 6 sentences
    assert "Seven" not in out and "\n\n" in out


def test_leaves_a_good_reply_alone():
    t = "Fever for **2 days** is often viral.\n\nRest and drink fluids. See a doctor if it lasts 3 days."
    assert tidy_reply(t) == (t, False)


def test_hindi_label_and_unfinished_ending():
    out, _ = tidy_reply("यह वायरल बुखार हो सकता है\n\nध्यान दें: तेज़ बुखार")
    assert out == "यह वायरल बुखार हो सकता है।"
