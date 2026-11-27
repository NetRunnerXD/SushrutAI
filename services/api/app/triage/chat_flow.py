"""The conversational differential inside chat (the symptom checker's engine, asked one question at a time).

A first symptom message no longer gets a disease list at once. Instead:
1. a question plan is built (app/triage/engine.py: candidate conditions with priors, yes/no findings), seeded with
   what the person said, their health memory, season and region;
2. the single most useful question is asked (danger signs first, then the highest information gain), at most
   CHAT_MAX_QUESTIONS times; once, two things they have not mentioned are checked together ("negative space");
3. if two possibilities stay close, the person chooses by voice: one more question, both now, or which one feels
   least likely to them (their answer softly updates the ranking; it never removes a danger sign);
4. the final answer is written by the normal answer path, limited to the 2–3 ranked possibilities, with urgency
   from the deterministic rules (app/triage/urgency.py).
A long new message instead of an answer ends the questions and is answered normally.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PatientProfile, TriageSession
from app.security.auth import CurrentUser
from app.triage import engine
from app.triage.store import load_state, save_state
from app.triage.urgency import recommend, recommended_specialist

CHAT_MAX_QUESTIONS = 3
CLOSE_GAP = 0.15        # top two closer than this after the questions: offer the choice
COLLAB_WEIGHT = 0.6     # "this one feels least likely to me": multiply its probability (a soft input, not a veto)
log = logging.getLogger(__name__)

_YES = re.compile(r"(?i)^\s*(yes|yeah|yep|yup|ya|haan|haa|han|ha|ji|ji haan|right|correct|true|sure|i do|i have|it is|there is|"
                  r"हाँ|हां|जी|जी हाँ|हाँ जी|सही)(?=$|[\s,.!?।;:])")  # (\b fails after Devanagari vowel signs)
_NO = re.compile(r"(?i)^\s*(no|nope|nah|nahi|nahin|na|none|neither|not really|never|i don'?t|i do not|didn'?t|haven'?t|"
                 r"no to both|neither of them|नहीं|ना|कोई नहीं|दोनों नहीं)(?=$|[\s,.!?।;:])")
_UNSURE = re.compile(r"(?i)(not sure|don'?t know|do not know|no idea|maybe|may be|can'?t say|unsure|pata nahi|pata nahin|"
                     r"malum nahi|shayad|पता नहीं|मालूम नहीं|शायद)")
_ASK_MORE = re.compile(r"(?i)(one more|another question|ask (me )?(more|another|again)|ask it|keep asking|more question|"
                       r"ek aur|aur pucho|aur poochho|एक और|और पूछो)")
_BOTH_NOW = re.compile(r"(?i)(both|tell me now|now|give (me )?(both|them)|no more questions|just tell|dono|abhi batao|"
                       r"दोनों|अभी बताओ)")

T = {
    "en": {
        "first": "To narrow this down, a few quick questions (yes, no or not sure).",
        "next": "Question {n}:",
        "negative": "You haven't mentioned these: {items} Is the answer no to all of them?",
        "negotiate": "I have two close possibilities: {a} and {b}. I can ask one more question to tell them apart, "
                     "tell you both now with what to watch for, or you can tell me which one feels less likely to you.",
        "opt_more": "Ask one more", "opt_both": "Tell me both now", "least": "Which feels least likely to you?",
        "yes": "Yes", "no": "No", "unsure": "Not sure", "none": "None of these",
    },
    "hi": {
        "first": "ठीक से समझने के लिए कुछ छोटे सवाल (हाँ, नहीं या पता नहीं)।",
        "next": "सवाल {n}:",
        "negative": "आपने इनके बारे में नहीं बताया: {items} क्या इन सबका जवाब 'नहीं' है?",
        "negotiate": "दो संभावनाएँ करीब हैं: {a} और {b}। मैं इन्हें अलग करने के लिए एक और सवाल पूछ सकता हूँ, "
                     "अभी दोनों बता सकता हूँ कि किन बातों का ध्यान रखें, या आप बताएँ कि आपको कौन सी कम लगती है।",
        "opt_more": "एक और सवाल", "opt_both": "अभी दोनों बताएँ", "least": "आपको कौन सी सबसे कम लगती है?",
        "yes": "हाँ", "no": "नहीं", "unsure": "पता नहीं", "none": "इनमें से कोई नहीं",
    },
}


@dataclass
class Step:
    """What the chat should do with this turn."""
    kind: str                         # ask | finish | none
    structured: dict | None = None    # for "ask": the question card
    reply: str = ""
    complaint: str = ""               # for "finish": the original description, to answer about
    context: str = ""                 # for "finish": the question round + ranking for the answer prompt
    ranked: list[dict] = field(default_factory=list)
    urgency: str | None = None
    specialist: str | None = None
    low_confidence: bool = False


def _t(lang: str) -> dict:
    return T.get(lang, T["en"])


async def active(db: AsyncSession, cu: CurrentUser, chat_id: str) -> TriageSession | None:
    return await db.scalar(select(TriageSession).where(TriageSession.user_id == cu.id,
                                                       TriageSession.chat_session_id == chat_id)
                           .order_by(TriageSession.created_at.desc()).limit(1))


def read_answer(text: str) -> str | None:
    """yes / no / unsure for a short reply, None when the message is something else (a new description)."""
    t = text.strip()
    if len(t.split()) > 8:
        return None
    if _UNSURE.search(t):
        return "unsure"
    if _NO.search(t):
        return "no"
    if _YES.search(t):
        return "yes"
    return None


def _options(lang: str, negotiation: bool = False, names: list[str] | None = None) -> list[dict]:
    t = _t(lang)
    if negotiation:
        return [{"value": "more", "label": t["opt_more"]}, {"value": "both", "label": t["opt_both"]}] + \
               [{"value": f"least:{n}", "label": n} for n in (names or [])]
    return [{"value": "yes", "label": t["yes"]}, {"value": "no", "label": t["no"]}, {"value": "unsure", "label": t["unsure"]}]


def _question_card(st: dict, lang: str, text: str, options: list[dict], mode: str) -> dict:
    return {"kind": "clarify", "reply": text, "suggest_check": False,
            "clarify": {"question": text, "options": options, "mode": mode,
                        "step": st.get("chat_asked", 0) + 1, "of": CHAT_MAX_QUESTIONS},
            "follow_up_questions": []}


def _next_turn(st: dict, lang: str) -> tuple[str, dict] | None:
    """The next thing to ask (text, card), or None when it is time to answer."""
    t = _t(lang)
    flags = engine.red_flag_answered(st)
    done, _ = engine.should_stop(st)
    if flags:
        return None
    if st.get("chat_asked", 0) >= CHAT_MAX_QUESTIONS or done:
        post = sorted(zip(st["posterior"], [c["name"] for c in st["conditions"]]), reverse=True)
        if (not st.get("negotiated") and len(post) > 1 and post[0][0] - post[1][0] < CLOSE_GAP
                and engine.next_question(st) is not None):
            st["negotiated"] = True
            st["pending"] = {"type": "negotiate", "names": [post[0][1], post[1][1]]}
            text = t["negotiate"].format(a=post[0][1], b=post[1][1])
            return text, _question_card(st, lang, text, _options(lang, True, [post[0][1], post[1][1]]), "negotiate")
        return None
    pending = [f for f in st["findings"] if f["id"] not in st["answers"]]
    red = [f for f in pending if f["red_flag"]]
    if not red and not st.get("negative_asked") and len(pending) >= 3:
        # negative space: two useful things they haven't mentioned, checked together
        top2 = sorted(pending, key=lambda f: -engine.expected_information_gain(st["posterior"], f["likelihoods"]))[:2]
        st["negative_asked"] = True
        st["pending"] = {"type": "negative", "ids": [f["id"] for f in top2]}
        items = " ".join(f"• {f['question']}" for f in top2)
        text = t["negative"].format(items=items)
        opts = [{"value": "no", "label": t["none"]}, {"value": "yes", "label": t["yes"]}, {"value": "unsure", "label": t["unsure"]}]
        return text, _question_card(st, lang, text, opts, "negative")
    q = engine.next_question(st)
    if q is None:
        return None
    st["pending"] = {"type": "finding", "id": q["id"]}
    lead = t["first"] + " " if st.get("chat_asked", 0) == 0 else ""
    text = f"{lead}{q['question']}"
    return text, _question_card(st, lang, text, _options(lang), "finding")


def _apply(st: dict, said: str, said_en: str) -> bool:
    """Apply the reply to the pending question; False when it was not an answer to it."""
    p = st.get("pending") or {}
    if p.get("type") == "negotiate":
        names = p.get("names", [])
        low = (said_en or said).lower()
        if (said_en or said).startswith("least:"):
            pick = (said_en or said).split(":", 1)[1]
        else:
            best = max(names, key=lambda n: fuzz.partial_ratio(n.lower(), low), default=None)
            exact = best is not None and fuzz.ratio(best.lower(), low.strip()) >= 90  # tapped / said just the name
            pick = best if best and (exact or (fuzz.partial_ratio(best.lower(), low) >= 80
                                               and re.search(r"(?i)less|least|not|kam|कम", low))) else None
        if pick:  # collaborative: their sense of "least likely" is a soft input
            i = next((k for k, c in enumerate(st["conditions"]) if c["name"] == pick), None)
            if i is not None:
                st["posterior"][i] *= COLLAB_WEIGHT
                st["posterior"] = engine.normalise(st["posterior"])
                st["user_least_likely"] = pick
            st["pending"] = None
            st["finish"] = True
            return True
        if _ASK_MORE.search(low) or low.strip() in ("more", "ask", "yes"):
            st["pending"] = None
            st["chat_asked"] = CHAT_MAX_QUESTIONS - 1  # exactly one more
            st["extra_question"] = True
            return True
        if _BOTH_NOW.search(low) or low.strip() in ("both", "no"):
            st["pending"] = None
            st["finish"] = True
            return True
        return False
    ans = said if said in ("yes", "no", "unsure") else read_answer(said_en or said) or read_answer(said)
    if ans is None:
        return False
    if p.get("type") == "negative":
        for fid in p.get("ids", []):
            if ans in ("no", "unsure"):  # "not sure" closes them too: never ask the same thing again
                engine.update(st, fid, ans)
            # "yes" (at least one of them): left open, the most useful one is asked on its own next
        st["chat_asked"] = st.get("chat_asked", 0) + 1
        st["pending"] = None
        return True
    if p.get("type") == "finding":
        engine.update(st, p["id"], ans)
        st["chat_asked"] = st.get("chat_asked", 0) + 1
        st["pending"] = None
        return True
    return False


def _floor(rec: str, ranked: list[dict], complaint: str) -> str:
    """The same urgency floors the chat answer applies: a must-not-miss condition among the possibilities shown
    (dengue, typhoid, TB …) means today; fever for 3+ days means today."""
    from app.llm.orchestrator import _FEVER_DAYS, URGENCY_FLOOR, _raise

    for r in ranked:
        for pat, floor in URGENCY_FLOOR:
            if pat.search(r["name"]):
                rec = _raise(rec, floor)
                r["urgency"] = _raise(r["urgency"], floor)
    if re.search(r"(?i)fever|बुखार|bukhar|ज्वर", complaint) and any(
            (m.group(1).isdigit() and int(m.group(1)) >= 3) or (not m.group(1).isdigit() and m.group(1) not in ("two", "दो"))
            for m in _FEVER_DAYS.finditer(complaint)):
        rec = _raise(rec, "see_doctor_24h")
    return rec


def _finish(ts: TriageSession, st: dict, lang: str) -> Step:
    ts.status = "closed"
    flags = engine.red_flag_answered(st)
    full = engine.summary(st)
    ranked = full[:3]
    # must-not-miss conditions from the public-health rules (TB for a long cough, malaria / dengue / typhoid
    # for fever in season) stay visible: the highest-ranked one takes the third place if it fell out
    req = [r.split(" (")[0].lower().split()[-1] for r in st.get("must_consider") or []]
    if req and not any(any(q in r["name"].lower() for q in req) for r in ranked):
        keep = next((r for r in full[3:] if any(q in r["name"].lower() for q in req)), None)
        if keep:
            ranked = ranked[:2] + [keep]
    rec = recommend(full, red_flag_yes=bool(flags))
    rec = _floor(rec, ranked, st.get("complaint", ""))
    asked = [f"- {f['question']} → {st['answers'][f['id']]}" for f in st["findings"] if f["id"] in st["answers"]]
    rank = ", ".join(f"{r['name']} ({r['likelihood']})" for r in ranked)
    ctx = ("QUESTION ROUND IN THIS CHAT (their answers):\n" + ("\n".join(asked) or "- (no answers)") +
           f"\nRANKING FROM THE SYMPTOM CHECKER (most likely first): {rank}."
           + (f"\nThey felt {st['user_least_likely']} is least likely." if st.get("user_least_likely") else "")
           + ("\nThey answered YES to a danger sign: " + "; ".join(flags) if flags else "")
           + "\nIn `possible_conditions`, list these conditions in this order. Only if what they describe clearly "
             "points to a condition missing from this ranking, put that one first instead.")
    return Step("finish", complaint=st.get("complaint", ""), context=ctx, ranked=ranked, urgency=rec,
                specialist=recommended_specialist(engine.summary(st), rec),
                low_confidence=(ranked[0]["probability"] < 0.4 if ranked else True))


async def handle_reply(db: AsyncSession, cu: CurrentUser, chat_id: str, text: str, english: str, lang: str) -> Step:
    """A turn in a chat that is in the middle of questions."""
    ts = await active(db, cu, chat_id)
    if ts is None or ts.status != "open":
        return Step("none")
    st = load_state(ts, cu.cipher)
    if not _apply(st, text.strip(), (english or "").strip()):
        ts.status = "closed"  # something new was said: stop asking, answer normally
        save_state(ts, cu.cipher, st)
        await db.commit()
        return Step("none")
    nxt = None if st.get("finish") else _next_turn(st, lang)
    if nxt is None:
        step = _finish(ts, st, lang)
    else:
        step = Step("ask", structured=nxt[1], reply=nxt[0])
    save_state(ts, cu.cipher, st)
    await db.commit()
    return step


async def start(db: AsyncSession, cu: CurrentUser, chat_id: str, complaint: str, profile: str, age: float | None,
                lang: str, memory: str, *, allow_cloud: bool, model_pref: str = "balanced") -> Step:
    """Build the question plan for a first symptom description and ask the first question. Step("none") when a
    plan cannot be made (the normal answer then follows)."""
    from app.llm.orchestrator import LANG_NAMES
    from app.llm.providers import ProviderUnavailable, clinical_chat
    from app.routers.triage import _season_hint

    p = await db.get(PatientProfile, cu.id)
    required = engine.must_consider(complaint, p.state_code if p else None, date.today().month)
    seeded = complaint + (f"\nWhat their health record says about this (do not ask about these again):\n{memory[:800]}" if memory else "")
    q_lang = "Hindi" if lang == "hi" else "English"  # other languages: asked in English, translated afterwards
    msgs = [{"role": "system", "content": "You are a clinical reasoning assistant building a symptom-checker "
                                          "question plan. Be medically accurate and conservative."},
            {"role": "user", "content": engine.build_prompt(seeded, profile, q_lang, _season_hint(date.today()), required)}]
    llm = None
    # Prefer the local careful model first; if local Ollama is unavailable and the user consented to cloud,
    # fall back seamlessly to the selected cloud provider instead of skipping differential questions.
    log.info("question plan (chat model_pref=%s, allow_cloud=%s)", model_pref, allow_cloud)
    for attempt in (1, 2):
        try:
            use_pref = "balanced" if attempt == 1 and model_pref in ("auto", "fast", "instant", "balanced") else model_pref
            llm = (await clinical_chat(msgs, schema=engine.TRIAGE_SCHEMA, allow_cloud=allow_cloud,
                                       num_predict=1400, model_pref=use_pref)).json()
            break
        except (ProviderUnavailable, ValueError, KeyError, TypeError) as e:
            log.info("chat question plan unavailable (%s: %s), attempt %s", type(e).__name__, str(e)[:120], attempt)
            if attempt == 2:
                return Step("none")
    # Indian public-health rules (fever in monsoon / endemic state → dengue, malaria; long cough → TB): a
    # condition the plan left out is added with a moderate prior, so it is always weighed and can be shown.
    missing = engine.missing_required(llm, required)
    if missing and isinstance(llm.get("conditions"), list):
        conds = llm["conditions"]
        llm["conditions"] = conds[:2] + [{"name": m, "prior": 0.2, "specialist": "General Physician"}
                                         for m in missing] + conds[2:]
    st = engine.init_state(llm, sex=p.sex if p else None, age=age,
                           pregnant=bool(p and p.pregnancy_status in ("pregnant", "possibly_pregnant")))
    st.update({"lang": lang, "complaint": complaint, "must_consider": required,
               "missing_must_consider": engine.missing_required(llm, required), "chat_asked": 0})
    ts = TriageSession(user_id=cu.id, chat_session_id=chat_id)
    nxt = _next_turn(st, "hi" if lang == "hi" else "en")
    if nxt is None:
        step = _finish(ts, st, lang)
    else:
        step = Step("ask", structured=nxt[1], reply=nxt[0])
    save_state(ts, cu.cipher, st)
    db.add(ts)
    await db.commit()
    return step
