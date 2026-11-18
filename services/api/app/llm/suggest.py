"""Personalised suggestion chips for the chat: what this person may want to ask next.

Rules first (deterministic, from their own data and the last answer), then an optional tiny model on the CPU that
picks and rephrases the best four in the person's language. Every suggestion is filtered: no medicine names, no
diagnosis, short, de-duplicated. If the model is slow or missing, the rule suggestions are returned as they are.

Context used (minimum necessary, never leaves the machine): age band, active conditions, whether there are
medicines/refills due, abnormal lab names, the latest BP, due care-schedule items, due follow-ups, the last chat
turn (kind, condition names, warning signs), season and time of day.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)


@dataclass
class Ctx:
    lang: str = "en"
    age: float | None = None
    conditions: list[str] = field(default_factory=list)
    has_meds: bool = False
    refill_due: bool = False
    abnormal_labs: list[str] = field(default_factory=list)   # e.g. ["HbA1c high"]
    bp: str | None = None                                     # "150/95" when the latest reading is high
    care_due: list[str] = field(default_factory=list)        # care-schedule titles due/overdue
    follow_ups: list[str] = field(default_factory=list)      # due "check in with me" texts
    last_question: str = ""
    last_kind: str = ""
    last_conditions: list[str] = field(default_factory=list)
    last_watch: bool = False
    last_answer: str = ""   # first sentences of the last answer (what the follow-ups should build on)
    season: str = "winter"
    hour: int = 12
    day: int = 0          # date.toordinal(): rotates the wording so the chips are not the same every day

    def describe(self) -> str:
        """One line for the model (and the eval's relevance judge)."""
        bits = []
        if self.age is not None:
            bits.append(f"age {self.age:.0f}")
        if self.conditions:
            bits.append("conditions: " + ", ".join(self.conditions[:4]))
        if self.abnormal_labs:
            bits.append("abnormal results: " + ", ".join(self.abnormal_labs[:3]))
        if self.bp:
            bits.append(f"latest BP {self.bp}")
        if self.care_due:
            bits.append("due: " + ", ".join(self.care_due[:2]))
        if self.last_question:
            bits.append(f"just asked: {self.last_question[:120]}")
        if self.last_conditions:
            bits.append("answer mentioned: " + ", ".join(self.last_conditions[:3]))
        bits.append(f"it is {self.season} season")
        return "; ".join(bits)


# ------------------------------------------------------------------ rule candidates
T = {
    "en": {
        "cond_cause": "What causes {x}?", "cond_long": "How long does {x} usually last?",
        "cond_food": "What should I eat with {x}?", "cond_prevent": "How can I prevent {x} next time?",
        "cond_test": "How is {x} checked?", "cond_doctor": "When should I see a doctor for {x}?",
        "cond_other": "How do I know if it is {x}?", "chronic_check": "How often should I check my {x}?",
        "worse": "Which signs mean I should go to hospital?", "home_more": "What else can I do at home?",
        "med_general": "Can I take my usual medicines with this?",
        "lab": "What does my {x} result mean?", "bp": "Is my BP {x} too high?",
        "chronic_diet": "Diet tips for {x}", "chronic_exercise": "Safe exercise with {x}",
        "care": "Why is {x} important?", "refill": "Where can I get my medicines cheaper?",
        "monsoon": "How do I protect my family from dengue?", "summer": "How do I avoid heat stroke?",
        "winter": "How can I avoid cold and flu this winter?",
        "morning": "A healthy breakfast idea for today", "night": "Tips to sleep better tonight",
        "day": "How much water should I drink in a day?", "bp_general": "What is a normal BP for my age?",
        "checkup": "Which health check-ups should I get at my age?",
        "nutrition_more": "Give me a simple meal plan for a day", "report_more": "Which values in my report need attention?",
    },
    "hi": {
        "cond_cause": "{x} क्यों होता है?", "cond_long": "{x} आम तौर पर कितने दिन रहता है?",
        "cond_food": "{x} में क्या खाना चाहिए?", "cond_prevent": "{x} दोबारा न हो, इसके लिए क्या करें?",
        "cond_test": "{x} की जाँच कैसे होती है?", "cond_doctor": "{x} में डॉक्टर को कब दिखाएँ?",
        "cond_other": "कैसे पता चले कि यह {x} है?", "chronic_check": "{x} की जाँच कितनी बार करानी चाहिए?",
        "worse": "किन लक्षणों पर अस्पताल जाना चाहिए?", "home_more": "घर पर और क्या कर सकते हैं?",
        "med_general": "क्या मैं अपनी रोज़ की दवाएँ इसके साथ ले सकता हूँ?",
        "lab": "मेरी {x} रिपोर्ट का क्या मतलब है?", "bp": "क्या मेरा BP {x} ज़्यादा है?",
        "chronic_diet": "{x} में खान-पान कैसा रखें?", "chronic_exercise": "{x} में कौन-सा व्यायाम सुरक्षित है?",
        "care": "{x} क्यों ज़रूरी है?", "refill": "मेरी दवाएँ सस्ती कहाँ मिलेंगी?",
        "monsoon": "परिवार को डेंगू से कैसे बचाएँ?", "summer": "लू से कैसे बचें?",
        "winter": "सर्दी-ज़ुकाम से कैसे बचें?",
        "morning": "आज के लिए सेहतमंद नाश्ता", "night": "रात को अच्छी नींद के उपाय",
        "day": "दिन में कितना पानी पीना चाहिए?", "bp_general": "मेरी उम्र में सामान्य BP कितना है?",
        "checkup": "मेरी उम्र में कौन-सी जाँचें करानी चाहिए?",
        "nutrition_more": "एक दिन का आसान भोजन प्लान बताइए", "report_more": "मेरी रिपोर्ट में किन चीज़ों पर ध्यान दें?",
    },
}


def rule_candidates(c: Ctx) -> list[str]:
    """Ordered most-specific first: the last answer, then the person's own data, then season / time of day."""
    L = T.get(c.lang, T["en"])
    out: list[str] = []
    out += c.follow_ups[:1]
    if c.last_conditions:
        x = c.last_conditions[0]
        pool = ["cond_food", "cond_long", "cond_prevent", "cond_test", "cond_doctor"]
        k = (c.day + len(x)) % len(pool)
        out += [L["cond_cause"].format(x=x)] + [L[pool[(k + j) % len(pool)]].format(x=x) for j in range(2)]
        if len(c.last_conditions) > 1:
            out.append(L["cond_other"].format(x=c.last_conditions[1]))
    if c.last_kind == "symptoms":
        out += [L["worse"] if not c.last_watch else L["home_more"]]
        if c.has_meds:
            out.append(L["med_general"])
    elif c.last_kind in ("nutrition_lifestyle",):
        out.append(L["nutrition_more"])
    elif c.last_kind in ("report_question",):
        out.append(L["report_more"])
    out += [L["lab"].format(x=x) for x in c.abnormal_labs[:2]]
    if c.bp:
        out.append(L["bp"].format(x=c.bp))
    for x in c.conditions[:2]:
        trio = [L["chronic_diet"], L["chronic_exercise"], L["chronic_check"]]
        k = (c.day + len(x)) % 3
        out += [trio[k].format(x=x), trio[(k + 1) % 3].format(x=x)]
    out += [L["care"].format(x=x) for x in c.care_due[:1]]
    if c.refill_due:
        out.append(L["refill"])
    out.append(L[c.season] if c.season in L else L["winter"])
    out.append(L["morning"] if 5 <= c.hour < 11 else L["night"] if c.hour >= 20 or c.hour < 5 else L["day"])
    if c.age is not None and c.age >= 30:
        out += [L["checkup"], L["bp_general"]]
    out += [L["day"], L["checkup"], L["bp_general"]]  # general pool so there are always enough to choose from
    return dedupe(out)


# ------------------------------------------------------------------ filters
_DIAG = re.compile(r"(?i)\byou (have|definitely|certainly)\b|आपको .* है$")


def dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for x in items:
        k = re.sub(r"\W+", " ", x.lower()).strip()
        if k and k not in seen:
            seen.add(k)
            out.append(x)
    return out


# A chip is something the person asks the app. Questions addressed TO the person ("Have you had any recent
# illnesses?", "What type of clothing do you usually wear?") are the model getting the direction wrong.
_ASKS_PATIENT = re.compile(
    r"(?i)\b(have|do|are|did|were|will|would|could) you\b|\byou (usually|feel|have|had|been|experienc|notice|take)|"
    r"\byour\b|\bhow (are|is) you|आपको|आपका|आपकी|आपके|क्या आप|आप कैसे|तुम्हें|तुम्हारा")


def clean(items: list, med_rx: re.Pattern | None, lang: str) -> list[str]:
    out = []
    for x in items:
        if not isinstance(x, str):
            continue
        x = re.sub(r"\s+", " ", x).strip().strip('"').strip()
        x = re.sub(r"^\d+[.)]\s*|^[-•*]\s*", "", x)
        if not (3 <= len(x) <= 70) or _DIAG.search(x) or _ASKS_PATIENT.search(x) or (med_rx and med_rx.search(x)):
            continue
        if lang == "hi" and not re.search(r"[ऀ-ॿ]", x):
            continue  # a Hindi user gets Hindi chips
        out.append(x)
    return dedupe(out)


# ------------------------------------------------------------------ tiny model (CPU)
SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "string"}}}, "required": ["items"]}
LANG = {"en": "simple English", "hi": "simple Hindi (Devanagari script)"}


EXAMPLES = {
    "en": ('They asked: "My knee hurts when I climb stairs."\nThe app answered: "This can match early knee arthritis or '
           'a strained muscle. Rest it, use a warm compress and avoid squatting."\n'
           '{"items": ["Which exercises make my knee stronger?", "Should I get a knee X-ray?", '
           '"Can I still go for walks?", "Does my weight affect knee pain?"]}'),
    "hi": ('They asked: "सीढ़ियाँ चढ़ने पर घुटने में दर्द होता है।"\nThe app answered: "यह घुटने के जोड़ की शुरुआती '
           'घिसावट या मांसपेशी में खिंचाव हो सकता है। आराम करें और गर्म सेक करें।"\n'
           '{"items": ["घुटना मज़बूत करने के लिए कौन-से व्यायाम करूँ?", "क्या घुटने का एक्स-रे कराना चाहिए?", '
           '"क्या मैं टहलने जा सकता हूँ?", "क्या वज़न से घुटने का दर्द बढ़ता है?"]}'),
}


SYSTEM = {
    lang: ("You suggest what a patient in India might ask next in a health app. Write 4 different short questions "
           f"(at most 9 words each) in {name}, as the patient would say them, about THIS conversation: for example how "
           "long it lasts, home care, food, when to worry, tests, or their own condition. Never name a medicine. Never "
           "state a diagnosis. Do not answer. Write them in the first person (I, my), as questions the patient asks the "
           "app; never questions to the patient (not 'do you…', 'have you…'). Reply only with JSON {\"items\": [4 questions]}.\n\nExample:\n"
           + EXAMPLES[lang])
    for lang, name in LANG.items()
}


def build_messages(c: Ctx) -> list[dict]:
    """The instructions and example are identical for every request (Ollama reuses that part of the prompt, so only
    the short per-conversation part is processed: this keeps a small model under ~2 s on the CPU)."""
    if c.last_question:
        convo = f'They asked: "{c.last_question[:200]}"\nThe app answered: "{c.last_answer[:240]}"'
    else:
        convo = "They have not asked anything yet today. Suggest questions about their own health data and the season."
    return [{"role": "system", "content": SYSTEM.get(c.lang, SYSTEM["en"])},
            {"role": "user", "content": f"About the patient: {c.describe()}\n{convo}"}]


async def model_pick(c: Ctx, cands: list[str], model: str, timeout: float = 2.0, base: str | None = None) -> list[str] | None:
    """A small model on the CPU writes 4 follow-up questions for this conversation.
    None on timeout/error: the caller falls back to the rule suggestions."""
    from app.config import get_settings

    body = {"model": model, "stream": False, "format": SCHEMA, "keep_alive": "60m", "messages": build_messages(c),
            "options": {"num_gpu": 0, "temperature": 0.4, "num_predict": 90, "num_ctx": 1024, "num_thread": 12}}
    if model.startswith("qwen3"):
        body["think"] = False
    try:
        async with httpx.AsyncClient(timeout=timeout) as cl:
            r = await cl.post(f"{(base or get_settings().ollama_base_url).rstrip('/')}/api/chat", json=body)
        r.raise_for_status()
        items = json.loads(r.json()["message"]["content"]).get("items")
        return items if isinstance(items, list) else None
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.info("suggestion model unavailable/slow (%s): %s", model, type(e).__name__)
        return None


async def suggest(c: Ctx, model: str | None, med_rx: re.Pattern | None, n: int = 4, timeout: float = 2.0) -> dict:
    t0 = time.perf_counter()
    cands = clean(rule_candidates(c), med_rx, c.lang)
    picked = None
    if model and c.last_question:
        raw = await model_pick(c, cands, model, timeout=timeout)
        if raw:
            picked = [x for x in clean(raw, med_rx, c.lang) if x.endswith(("?", "।")) or len(x.split()) >= 3]
    items = dedupe((picked or []) + cands)[:n]  # top up from the rules if the model returned fewer
    return {"items": items, "source": "model" if picked else "rules", "ms": round((time.perf_counter() - t0) * 1000)}


# ------------------------------------------------------------------ context from the person's data
async def context_for(db, cu, lang: str, session_id: str | None = None) -> Ctx:
    from sqlalchemy import select

    from app.clock import local_now
    from app.models import ChatSession, Condition, LabResult, Medication, Message, PatientProfile, Vital
    from app.routers.home import season_of
    from app.routers.today import list_follow_ups

    now = local_now()
    c = Ctx(lang=lang if lang in T else "en", season=season_of(now.month), hour=now.hour, day=now.toordinal())
    p = await db.get(PatientProfile, cu.id)
    if p and p.dob_enc:
        from app.llm.orchestrator import _age
        c.age = _age(cu.cipher.dec(p.dob_enc))
    c.conditions = [cu.cipher.dec(x.display_enc) for x in await db.scalars(
        select(Condition).where(Condition.user_id == cu.id, Condition.status == "active"))][:3]
    c.has_meds = (await db.scalars(select(Medication.id).where(Medication.user_id == cu.id).limit(1))).first() is not None
    labs = list(await db.scalars(select(LabResult).where(LabResult.user_id == cu.id).order_by(LabResult.observed_at)))
    latest: dict[str, LabResult] = {}
    for lr in labs:
        latest[lr.test_name.lower()] = lr
    c.abnormal_labs = [lr.test_name for lr in latest.values() if lr.flag in ("H", "L")][-2:]
    vit = {v.type: v for v in await db.scalars(select(Vital).where(Vital.user_id == cu.id, Vital.type.in_(("bp_sys", "bp_dia")))
                                                .order_by(Vital.measured_at))}
    if "bp_sys" in vit and "bp_dia" in vit and (vit["bp_sys"].value >= 140 or vit["bp_dia"].value >= 90):
        c.bp = f"{vit['bp_sys'].value:.0f}/{vit['bp_dia'].value:.0f}"
    try:
        from app.routers.care import schedule_for
        c.care_due = [i["title"] for i in (await schedule_for(db, cu))["items"] if i["status"] in ("due", "overdue")][:2]
    except Exception:  # noqa: BLE001 — optional context
        pass
    c.follow_ups = [f["text"] for f in await list_follow_ups(db, cu, due_only=True)][:1]
    if session_id:
        s = await db.get(ChatSession, session_id)
        if s and s.user_id == cu.id:
            msgs = list(await db.scalars(select(Message).where(Message.session_id == session_id)
                                         .order_by(Message.created_at.desc()).limit(2)))
            st = None
            for m in msgs:
                if m.role == "assistant" and m.structured:
                    st = m.structured
                elif m.role == "user":
                    c.last_question = cu.cipher.dec(m.content_enc)[:200]
            # chips follow the conversation's language (a Hindi chat in an English app gets Hindi chips)
            if re.search(r"[ऀ-ॿ]", c.last_question):
                c.lang = "hi"
            elif c.last_question.isascii() and c.last_question:
                c.lang = "en"
            if st:
                c.last_kind = st.get("kind") or ""
                key = "name" if c.lang == "hi" else "name_en"
                c.last_conditions = [x.get(key) or x.get("name") for x in (st.get("possible_conditions") or [])
                                     if x.get(key) or x.get("name")][:2]
                c.last_watch = bool(st.get("watch_for"))
                c.last_answer = " ".join(re.split(r"(?<=[.!?।])\s+", (st.get("reply") or "").strip())[:2])[:300]
    return c


async def for_user(db, cu, lang: str, session_id: str | None = None, mode: str = "auto") -> dict:
    """mode: "rules" = instant chips from the person's data; "model"/"auto" = the small model writes them (falls
    back to the rules after the timeout)."""
    from app.config import get_settings
    from app.llm.orchestrator import _medicine_names

    c = await context_for(db, cu, lang, session_id)
    s = get_settings()
    indic = c.lang != "en"
    model = None if mode == "rules" else ((s.suggest_model_indic if indic else s.suggest_model) or None)
    return await suggest(c, model, await _medicine_names(db),
                         timeout=s.suggest_timeout_indic_s if indic else s.suggest_timeout_s)
