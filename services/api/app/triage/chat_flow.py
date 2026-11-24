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

CHAT_MAX_QUESTIONS = 8
CLOSE_GAP = 0.15        # top two closer than this after the questions: offer the choice
COLLAB_WEIGHT = 0.6     # "this one feels least likely to me": multiply its probability (a soft input, not a veto)
log = logging.getLogger(__name__)

_YES = re.compile(r"(?i)^\s*(?:(?:ji|jee|जी)\s+)?(yes|yeah|yep|yup|ya|haan|haa|han|ha|sahi|thoda(?: bahut| sa)?|halka(?: sa)?|kabhi kabhi|right|correct|true|sure|i do|i have|it is|there is|sometimes|slightly|mildly|"
                  r"हाँ|हां|जी|जी हाँ|हाँ जी|सही|थोड़ा(?:\s+बहुत|\s+सा)?|हल्का(?:\s+सा)?|कभी[\s-]+कभी|হ্যাঁ|হাঁ|জি|অল্প|মাঝে[\s-]+মাঝে)(?=$|[\s,.!?।;:])")  # (\b fails after Indic vowel signs)
_NO = re.compile(r"(?i)^\s*(?:(?:ji|jee|जी)\s+)?(no|nope|nah|nahi|nahin|na|bilkul nahi|aisi koi baat nahi|none|neither|not really|never|i don'?t|i do not|didn'?t|haven'?t|"
                 r"no to both|neither of them|नहीं|ना|बिलकुल नहीं|ऐसी कोई बात नहीं|कोई नहीं|दोनों नहीं|না|একদমই না|কোনোটাই না)(?=$|[\s,.!?।;:])")
_UNSURE = re.compile(r"(?i)(not sure|don'?t know|do not know|no idea|maybe|may be|can'?t say|unsure|pata nahi|pata nahin|yaad nahi|samajh nahi|"
                     r"malum nahi|shayad|पता नहीं|मालूम नहीं|याद नहीं|समझ नहीं|शायद|জানি না|ঠিক জানি না|বলতে পারব না|মনে নেই)")
_LONG_LEAD_NO = re.compile(r"(?i)^\s*(?:(?:ji|jee|जी)\s+)?(?:no|nope|nah|neither|none of these|nahi|nahin|bilkul nahi|नहीं|ना|बिलकुल नहीं|না)(?:\b|(?=$|[\s,.!?।;:]))(?:[,.;:!\s]*(?:babu|beta|sahab|doctor(?:\s+sahab)?|जी|बाबू|डॉक्टर\s*साहब|সাheb)?[,.;:!\s]*)")
_LONG_LEAD_YES = re.compile(r"(?i)^\s*(?:(?:ji|jee|जी)\s+)?(?:yes|yeah|yep|haan|ha|thoda(?: bahut| sa)?|kabhi kabhi|हाँ|हां|थोड़ा(?:\s+बहुत|\s+सा)?|হ্যাঁ)(?:\b|(?=$|[\s,.!?।;:]))(?:[,.;:!\s]*(?:babu|beta|sahab|doctor(?:\s+sahab)?|जी|बाबू|डॉक्टर\s*साहब)?[,.;:!\s]*)")
_LONG_LEAD_UNSURE = re.compile(r"(?i)^\s*(?:not sure|don'?t know|unsure|maybe|pata nahi|pata nahin|জানি না|पता नहीं|शायद)(?:\b|(?=$|[\s,.!?।;:]))(?:[,.;:!\s]+|$)")
_ASK_MORE = re.compile(r"(?i)(one more|another question|ask (me )?(more|another|again)|ask it|keep asking|more question|"
                       r"ek aur|aur pucho|aur poochho|एक और|और पूछो|আরও একটা|আরেকটা প্রশ্ন)")
_BOTH_NOW = re.compile(r"(?i)(both|tell me now|now|give (me )?(both|them)|no more questions|just tell|show (my )?assessment|stop asking|aap batao|ab batao|dono|abhi batao|"
                       r"दोनों|अभी बताओ|अभी संभावित कारण|आप ही बता|দুটোই বলুন|এখনই বলুন|এখনই সম্ভাব্য কারণ)")
_NEW_TOPIC_RE = re.compile(
    r"(?i)^\s*(?:actually|by the way|instead|forget that|new question)\b|"
    r"\b(?:my\s+(?:child|son|daughter|mother|mom|father|dad|brother|sister|friend|wife|husband)\b.*\b(?:also|has|have)\b)"
)

T = {
    "en": {
        "first": "I hear you — let's take a moment to understand this carefully together.",
        "ack_red_no": "That's reassuring to hear.",
        "ack_yes": "Thank you for sharing that, it helps me narrow things down.",
        "ack_no": "Okay, noted.",
        "ack_unsure": "That's completely alright if you're not sure.",
        "continue_lead": "I can ask one more quick question to be clearer, or show my guidance so far.",
        "next": "Question {n}:",
        "negative": "To be safe — are you noticing either of these: {items}?",
        "negotiate": "I have narrowed this to two close possibilities: {a} and {b}. Would you like me to ask one more quick question to tell them apart, or explain both right away?",
        "opt_more": "Ask one more", "opt_both": "Explain both now", "stop_now": "I'm done — show guidance",
        "least": "Which feels least likely to you?",
        "yes": "Yes", "no": "No", "unsure": "Not sure", "none": "No, neither",
    },
    "hi": {
        "first": "मैं आपकी परेशानी समझ रहा हूँ — ठीक से समझने के लिए मैं आपसे एक आसान सवाल पूछूँगा।",
        "ack_red_no": "अच्छी बात है, यह राहत की बात है।",
        "ack_yes": "समझ गया, यह जानकारी कारण को ठीक से पहचानने में मदद करती है।",
        "ack_no": "ठीक है, समझ गया।",
        "ack_unsure": "कोई बात नहीं, अगर पक्का याद नहीं है तो बिल्कुल ठीक है।",
        "continue_lead": "इसे और साफ समझने के लिए मैं एक और आसान सवाल पूछ सकता हूँ, या अभी तक की समझ से सलाह दे सकता हूँ।",
        "next": "सवाल {n}:",
        "negative": "सुरक्षा के लिए एक बात बताएँ: {items}?",
        "negotiate": "दो संभावनाएँ करीब हैं: {a} और {b}। क्या आप एक और छोटा सवाल देना चाहेंगे, या मैं अभी दोनों के बारे में बताऊँ?",
        "opt_more": "एक और सवाल पूछें", "opt_both": "अभी सलाह बताएँ", "stop_now": "बस, अभी सलाह बताएँ",
        "least": "आपको कौन सी सबसे कम लगती है?",
        "yes": "हाँ", "no": "नहीं", "unsure": "पता नहीं", "none": "दोनों नहीं",
    },
    "bn": {
        "first": "আমি আপনার কষ্ট বুঝতে পারছি — ভালোভাবে বুঝতে আমি একটি সহজ প্রশ্ন করছি।",
        "ack_red_no": "ভালো কথা, এটি স্বস্তির বিষয়।",
        "ack_yes": "বুঝেছি, এই তথ্যটি সঠিক কারণ বুঝতে সাহায্য করছে।",
        "ack_no": "ঠিক আছে, নোট করলাম।",
        "ack_unsure": "কোনো অসুবিধা নেই, নিশ্চিত না হলে ঠিক আছে।",
        "continue_lead": "আরও স্পষ্টভাবে বুঝতে আমি আরেকটি প্রশ্ন করতে পারি, অথবা এখনই পরামর্শ দিতে পারি।",
        "next": "প্রশ্ন {n}:",
        "negative": "নিরাপত্তার জন্য একটি কথা বলুন: {items}?",
        "negotiate": "দুটি সম্ভাব্য কারণ কাছাকাছি আছে: {a} এবং {b}। আরেকটি ছোট প্রশ্ন করব, নাকি এখনই দুটি সম্পর্কে বলব?",
        "opt_more": "আরও একটি প্রশ্ন", "opt_both": "এখনই পরামর্শ দিন", "stop_now": "এখনই পরামর্শ দিন",
        "least": "আপনার কাছে কোনটি কম সম্ভব মনে হচ্ছে?",
        "yes": "হ্যাঁ", "no": "না", "unsure": "জানি না", "none": "কোনোটিই নয়",
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
    """yes / no / unsure for a short reply, or a longer reply starting explicitly with yes/no/unsure;
    None when the message is something else (a new description)."""
    t = text.strip()
    if len(t.split()) > 8:
        if _LONG_LEAD_UNSURE.match(t):
            return "unsure"
        if _LONG_LEAD_NO.match(t):
            return "no"
        if _LONG_LEAD_YES.match(t):
            return "yes"
        # Short colloquial Hinglish/Hindi qualifiers embedded early in longer answers from elderly/rural patients
        if re.match(r"^(?:nahi|नहीं|na|bilkul nahi)\b", t, re.I):
            return "no"
        if re.match(r"^(?:haan|ha|yes|हाँ|जी\s*हाँ)\b", t, re.I):
            return "yes"
        return None
    if _UNSURE.search(t):
        return "unsure"
    if _NO.search(t):
        return "no"
    if _YES.search(t):
        return "yes"
    return None


def _options(lang: str, negotiation: bool = False, names: list[str] | None = None, allow_stop: bool = False) -> list[dict]:
    t = _t(lang)
    if negotiation:
        return [{"value": "more", "label": t["opt_more"]}, {"value": "both", "label": t["opt_both"]}] + \
               [{"value": f"least:{n}", "label": n} for n in (names or [])]
    opts = [{"value": "yes", "label": t["yes"]}, {"value": "no", "label": t["no"]}, {"value": "unsure", "label": t["unsure"]}]
    if allow_stop:
        opts.append({"value": "stop_now", "label": t["stop_now"]})
    return opts


def _question_card(st: dict, lang: str, text: str, options: list[dict], mode: str) -> dict:
    plausible = sum(1 for p in (st.get("posterior") or []) if p >= 0.12)
    return {"kind": "clarify", "reply": text, "suggest_check": False,
            "clarify": {"question": text, "options": options, "mode": mode,
                        "step": st.get("chat_asked", 0) + 1, "of": None,
                        "plausible_count": plausible},
            "follow_up_questions": []}


def _ack_for(st: dict, t: dict) -> str:
    last = st.get("last_ans")
    if not last:
        return ""
    if last == "no" and st.get("last_was_red_flag"):
        return t.get("ack_red_no", "")
    return t.get(f"ack_{last}", "")


def _next_turn(st: dict, lang: str) -> tuple[str, dict] | None:
    """Ask one natural, empathetic doctor-like question at a time until narrowed down to <=3 specific conditions."""
    t = _t(lang)
    flags = engine.red_flag_answered(st)
    done, _ = engine.should_stop(st)
    plausible = [p for p in (st.get("posterior") or []) if p >= 0.12]
    asked_n = st.get("chat_asked", 0)
    if flags:
        return None
    if asked_n >= CHAT_MAX_QUESTIONS or (done and len(plausible) <= 3):
        post = sorted(zip(st["posterior"], [c["name"] for c in st["conditions"]]), reverse=True)
        if (not st.get("negotiated") and len(post) > 1 and post[0][0] - post[1][0] < CLOSE_GAP
                and engine.next_question(st) is not None):
            st["negotiated"] = True
            st["pending"] = {"type": "negotiate", "names": [post[0][1], post[1][1]]}
            ack = _ack_for(st, t)
            body = t["negotiate"].format(a=post[0][1], b=post[1][1])
            text = f"{ack} {body}".strip() if ack else body
            return text, _question_card(st, lang, text, _options(lang, True, [post[0][1], post[1][1]]), "negotiate")
        return None
    q = engine.next_question(st)
    if q is None:
        return None
    allow_stop = asked_n >= 1
    st["pending"] = {"type": "finding", "id": q["id"], "red_flag": bool(q.get("red_flag"))}
    parts: list[str] = []
    if asked_n == 0:
        parts.append(t["first"])
    else:
        ack = _ack_for(st, t)
        if ack:
            parts.append(ack)
        if asked_n >= 3 and not st.get("continue_asked"):
            st["continue_asked"] = True
            parts.append(t["continue_lead"])
    parts.append(q["question"])
    text = " ".join(p.strip() for p in parts if p and p.strip())
    return text, _question_card(st, lang, text, _options(lang, allow_stop=allow_stop), "finding")


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
    low_in = (said_en or said).strip().lower()
    if _BOTH_NOW.search(low_in) or low_in in ("stop_now", "enough", "stop", "skip"):
        st["pending"] = None
        st["finish"] = True
        return True
    ans = said if said in ("yes", "no", "unsure") else read_answer(said_en or said) or read_answer(said)
    if ans is None:
        # Subjective / descriptive reply to a clarifying question: keep the consultation going
        # unless the user explicitly pivots to a brand-new complaint or different person.
        if _NEW_TOPIC_RE.search(low_in):
            return False
        if p.get("type") in ("finding", "negative"):
            if re.search(r"\b(?:don'?t|do not|not really|never|neither|none|nothing|no\s+\w+)\b", low_in):
                ans = "no"
            elif re.search(r"\b(?:a bit|slightly|mildly|sometimes|mostly|feels?|hurts?|pain|have|having)\b", low_in):
                ans = "yes"
            else:
                ans = "unsure"
        else:
            return False
    extra = (said_en or said or "").strip()
    if extra and len(extra.split()) > 4 and extra not in ("yes", "no", "unsure"):
        cur = st.get("complaint") or ""
        if extra.lower() not in cur.lower():
            st["complaint"] = f"{cur}; {extra}".strip("; ")[:1200]
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
        st["last_ans"] = ans
        st["last_was_red_flag"] = bool(p.get("red_flag"))
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
        ts.status = "closed"  # something new was said: stop asking, answer normally while preserving Q&A context
        asked_ctx = [f"- {f['question']} -> {st['answers'][f['id']]}" for f in st.get("findings", []) if f["id"] in st.get("answers", {})]
        ctx_note = ("PRIOR CLARIFYING ANSWERS IN THIS CHAT:\n" + "\n".join(asked_ctx)) if asked_ctx else ""
        save_state(ts, cu.cipher, st)
        await db.commit()
        return Step("none", complaint=st.get("complaint", ""), context=ctx_note)
    nxt = None if st.get("finish") else _next_turn(st, lang)
    if nxt is None:
        step = _finish(ts, st, lang)
    else:
        step = Step("ask", structured=nxt[1], reply=nxt[0])
    save_state(ts, cu.cipher, st)
    await db.commit()
    return step


_REFINEMENT_RE = re.compile(
    r"(?i)^\s*(?:no,?\s*actually|actually|it lasts|lasts about|happens when|also happens|could this be|why does|how long|what if)\b"
)


async def should_start(
    db: AsyncSession,
    cu: CurrentUser,
    chat_id: str,
    complaint_en: str,
    history: list[dict] | None = None,
) -> bool:
    """True when chat_flow should start a narrowing round: first symptom in chat, OR a genuinely new
    distinct symptom complaint introduced after an earlier triage round closed."""
    from app.clinical.extract import extract_deterministic

    latest = await active(db, cu, chat_id)
    if latest is not None and latest.status == "open":
        return False
    if latest is None:
        has_ans = any(
            h.get("role") == "assistant" and (h.get("structured") or {}).get("possible_conditions")
            for h in (history or [])
        )
        return not has_ans
    # A prior triage round in this chat already finished: re-trigger only if the user introduces a NEW symptom
    text = (complaint_en or "").strip()
    if not text or len(text.split()) <= 4 or _REFINEMENT_RE.match(text):
        return False
    prev_st = load_state(latest, cu.cipher)
    prev_complaint = str(prev_st.get("complaint") or "")
    prev_syms = {s.name for s in extract_deterministic(prev_complaint, patient_id=cu.id).symptoms}
    now_syms = {s.name for s in extract_deterministic(text, patient_id=cu.id).symptoms}
    return bool(now_syms - prev_syms)


async def start(db: AsyncSession, cu: CurrentUser, chat_id: str, complaint: str, profile: str, age: float | None,
                lang: str, memory: str, *, allow_cloud: bool, model_pref: str = "balanced") -> Step:
    """Build the question plan for a symptom description and ask the first question. Works across both
    local (Ollama) and Gemini/Groq APIs. Step("none") when a plan cannot be made (the normal answer then follows)."""
    from app.llm.orchestrator import LANG_NAMES
    from app.llm.providers import ProviderUnavailable, clinical_chat, gemini_model_for
    from app.routers.triage import _season_hint

    p = await db.get(PatientProfile, cu.id)
    required = engine.must_consider(complaint, p.state_code if p else None, date.today().month)
    seeded = complaint + (f"\nWhat their health record says about this (do not ask about these again):\n{memory[:800]}" if memory else "")
    use_gemini = model_pref in ("gemini", "gemini_fast") and bool(gemini_model_for(model_pref))
    use_cloud = model_pref == "cloud" and allow_cloud
    plan_pref = model_pref if (use_gemini or use_cloud) else "balanced"
    q_lang = LANG_NAMES.get(lang, "English") if use_gemini else ("Hindi" if lang == "hi" else "English")
    msgs = [{"role": "system", "content": "You are a clinical reasoning assistant building a symptom-checker "
                                          "question plan. Be medically accurate and conservative."},
            {"role": "user", "content": engine.build_prompt(seeded, profile, q_lang, _season_hint(date.today()), required)}]
    llm = None
    log.info("question plan uses %s (requested chat model %s)", plan_pref, model_pref)
    for attempt in (1, 2):
        try:
            llm = (await clinical_chat(msgs, schema=engine.TRIAGE_SCHEMA, allow_cloud=allow_cloud,
                                       num_predict=1400, model_pref=plan_pref)).json()
            if not isinstance(llm, dict) or not llm.get("conditions") or not llm.get("findings"):
                raise ValueError("Missing conditions or findings in triage plan")
            # Validate early inside attempt loop so transient malformed JSON retries once
            missing = engine.missing_required(llm, required)
            if missing and isinstance(llm.get("conditions"), list):
                conds = llm["conditions"]
                llm["conditions"] = conds[:2] + [{"name": m, "prior": 0.2, "specialist": "General Physician"}
                                                 for m in missing] + conds[2:]
            st = engine.init_state(llm, sex=p.sex if p else None, age=age,
                                   pregnant=bool(p and p.pregnancy_status in ("pregnant", "possibly_pregnant")))
            break
        except (ProviderUnavailable, ValueError, KeyError, TypeError) as e:
            log.info("chat question plan unavailable (%s: %s), attempt %s", type(e).__name__, str(e)[:120], attempt)
            if attempt == 2:
                return Step("none")
    st.update({"lang": lang, "complaint": complaint, "must_consider": required,
               "missing_must_consider": engine.missing_required(llm, required), "chat_asked": 0})
    ts = TriageSession(user_id=cu.id, chat_session_id=chat_id)
    ui_lang = lang if lang in T else "en"
    nxt = _next_turn(st, ui_lang)
    if nxt is None:
        step = _finish(ts, st, lang)
    else:
        step = Step("ask", structured=nxt[1], reply=nxt[0])
    save_state(ts, cu.cipher, st)
    db.add(ts)
    await db.commit()
    return step
