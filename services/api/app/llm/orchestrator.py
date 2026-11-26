"""Health-assistant turn orchestration.

red-flag rules → profile context → RAG → MedGemma (constrained JSON) → citation/grounding check
→ prohibition check → response. Emergencies short-circuit before the model is called.
"""
from __future__ import annotations

import asyncio
import os
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clinical.bmi import bmi_category
from app.config import get_settings
from app.llm import intent as intent_mod
from app.llm.providers import LlmResult, clinical_chat, full_cloud_allowed
from app.models import Allergy, Condition, Medication, PatientProfile, Vital
from app.rag.store import Hit, search
from app.safety import guard, red_flags
from app.safety.disclaimer import PROHIBITIONS, disclaimer
from app.security.auth import CurrentUser

log = logging.getLogger(__name__)

LANG_NAMES = {"en": "English", "hi": "Hindi", "bn": "Bengali", "ta": "Tamil", "te": "Telugu",
              "mr": "Marathi", "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam", "or": "Odia",
              "pa": "Punjabi (Gurmukhi script)", "as": "Assamese", "ur": "Urdu (Nastaliq / Arabic script)"}

# Languages the local chat models write poorly: the answer is written in English (the question is translated with
# IndicTrans2, which is also what search uses) and translated back with IndicTrans2. English and Hindi go direct.
PIVOT_LANGS = set(filter(None, os.environ.get("PIVOT_LANGS", "bn,ta,te,mr,gu,kn,ml,or,pa,as,ur").split(",")))
# Fields of an answer that people read (translated back for a pivot language); enums and English names are kept.
_READ_LISTS = ("self_care", "tips", "watch_for", "nutrition", "follow_up_questions", "suggested_tests")


async def translate_answer(structured: dict, lang: str, *, pref: str = "auto") -> bool:
    """English answer -> the person's language, in one batched pass (IndicTrans2 by default, or Gemini / local LLM
    when configured in AI Backend settings). False if translation failed."""
    from app.speech import models as speech

    texts: list[str] = []
    where: list[tuple] = []

    def add(t, loc):
        if isinstance(t, str) and t.strip():
            texts.append(re.sub(r"\*\*([^*]+)\*\*", r"\1", t))  # bold markers do not survive translation
            where.append(loc)
    add(structured.get("reply"), ("reply",))
    for i, st in enumerate(structured.get("storylines") or []):
        add(st.get("text"), ("story", i))
    for k in _READ_LISTS:
        for i, x in enumerate(structured.get(k) or []):
            add(x, (k, i))
    for i, pc in enumerate(structured.get("possible_conditions") or []):
        add(pc.get("name_en") or pc.get("name"), ("pc", i, "name"))
        for f in ("supporting", "against"):
            for j, x in enumerate(pc.get(f) or []):
                add(x, ("pc", i, f, j))
    if not texts:
        return False
    out: list[str] | None = None
    if pref == "gemini":
        from app.llm.providers import ProviderUnavailable, full_cloud_allowed, gemini
        if full_cloud_allowed.get() and gemini().available:
            try:
                from app.config import get_settings
                payload = json.dumps(texts, ensure_ascii=False)
                res = await gemini().chat(
                    get_settings().gemini_fast_model,
                    [{"role": "system", "content": f"Translate every string in the JSON array to {LANG_NAMES.get(lang, lang)} preserving medical accuracy and numbers. Return ONLY a JSON array of the same length."},
                     {"role": "user", "content": payload}],
                    temperature=0.1,
                )
                parsed = res.json()
                if isinstance(parsed, list) and len(parsed) == len(texts):
                    out = [str(x) for x in parsed]
            except (ProviderUnavailable, ValueError, TypeError) as e:
                log.warning("Gemini translation failed (%s); falling back", e)
    if out is None:
        if not speech.available("transformers"):
            return False
        try:
            out = await asyncio.to_thread(speech.translate_many, texts, "en", lang)
        except Exception as e:  # noqa: BLE001
            log.warning("answer translation failed: %s", e)
            return False
    for loc, t in zip(where, out):
        if loc[0] == "reply":
            structured["reply"] = t
        elif loc[0] == "story":
            structured["storylines"][loc[1]]["text"] = t
        elif loc[0] == "pc":
            pc = structured["possible_conditions"][loc[1]]
            if loc[2] == "name":
                pc["name"] = t
            else:
                pc[loc[2]][loc[3]] = t
        else:
            structured[loc[0]][loc[1]] = t
    return True


SPECIALISTS = ["General Physician", "Internal Medicine", "General Surgery", "Paediatrician", "Gynaecologist",
               "Obstetrics", "Cardiologist", "Endocrinologist", "Pulmonologist", "Gastroenterologist",
               "Hepatologist", "Nephrologist", "Neurologist", "Neurosurgeon", "Dermatologist",
               "ENT Specialist", "Ophthalmologist", "Orthopaedician", "Psychiatrist", "Urologist",
               "Oncologist", "Rheumatologist", "Infectious Disease", "Haematologist",
               "Physical Medicine and Rehabilitation", "Dentist", "Emergency Department"]

# Deterministic urgency floor for must-not-miss conditions (the model may under-triage).
URGENCY_RANK = {"self_care": 0, "see_doctor_week": 1, "see_doctor_24h": 2, "emergency": 3}
URGENCY_FLOOR = [
    (re.compile(r"(?i)myocardial|heart attack|acute coronary|stroke|meningitis|sepsis|ectopic|appendicitis|"
                r"pulmonary embolism|anaphyla|diabetic ketoacidosis|encephalitis|intestinal obstruction"), "emergency"),
    (re.compile(r"(?i)dengue|malaria|typhoid|enteric fever|leptospir|scrub typhus|pneumonia|tuberculosis|\btb\b|"
                r"chikungunya|hepatitis|jaundice|urinary tract infection|pyelonephritis|cellulitis|preeclampsia|"
                r"pre-eclampsia|kidney stone|renal colic"), "see_doctor_24h"),
]
_FEVER_DAYS = re.compile(r"(?i)(\d+|two|three|four|five|six|seven|teen|तीन|चार|पांच|पाँच|दो)\s*(din|days?|दिन)")


# Self-care lists must not recommend medicines (no prescribing). Matches common Indian brand/generic names,
# drug classes, and dose expressions; such items are replaced by a pharmacist/doctor referral.
_MED_ADVICE = re.compile(
    r"(?i)\b\d+\s?(mg|mcg|ml)\b|paracetamol|acetaminophen|dolo|crocin|calpol|ibuprofen|brufen|combiflam|diclofenac|"
    r"aceclofenac|nimesulide|aspirin|disprin|cetirizine|levocetirizine|montelukast|antibiotic|azithromycin|"
    r"amoxicillin|augmentin|ciprofloxacin|ofloxacin|metronidazole|pantoprazole|omeprazole|ranitidine|"
    r"domperidone|ondansetron|loperamide|antacid|steroid|prednisolone|dexamethasone|cough syrup|painkiller|"
    r"pain killer|दवा|गोली|टैबलेट|सिरप|dawai|goli")
MED_REFERRAL = {"en": "Ask a doctor or pharmacist before taking any medicine for these symptoms.",
                "hi": "इन लक्षणों के लिए कोई भी दवा लेने से पहले डॉक्टर या फार्मासिस्ट से पूछें।"}


# A child mentioned in a medicine question: children's doses depend on age and weight, and adult formulations
# (cough syrups, combination cold medicines) can be dangerous for them.
_CHILD = re.compile(
    r"(?i)\b(1[0-2]|[1-9])\s*-?\s*(years?|yrs?|months?|weeks?)\s*-?\s*old\b|\b(1[0-2]|[1-9])\s*(saal|mahine|maheene)\s*(ka|ki|ke)\b|"
    r"\b(child|children|kid|kids|baby|"
    r"babies|infant|toddler|newborn|my son|my daughter|bachch\w*|bacha|beta|beti)\b|बच्च|शिशु|बेटा|बेटी")
_DOSE_TIP = re.compile(r"(?i)\bdos(e|age|ing)\b|label|\b\d+\s?(mg|ml|tsp|teaspoons?|spoons?)\b|खुराक")
_ANY_REFERRAL = re.compile(r"(?i)doctor|physician|pharmacist|paediatrician|pediatrician|health ?care professional|"
                           r"health worker|डॉक्टर|चिकित्सक|फार्मासिस्ट")
CHILD_MED = {"en": "Never give an adult medicine to a child. A child's medicine and dose depend on age and weight — "
                   "ask a doctor or pharmacist first.",
             "hi": "बच्चे को कभी भी बड़ों की दवा न दें। बच्चे की दवा और खुराक उम्र और वज़न पर निर्भर करती है — "
                   "पहले डॉक्टर या फार्मासिस्ट से पूछें।"}
_DOSE_Q = re.compile(r"(?i)\b(dose|dosage|units?|mg|tablets?|capsules?|insulin|medicine|medication|syrup|injection)\b|"
                     r"दवा|दवाई|खुराक|गोली|इंसुलिन|dawai|dawa|goli")
MED_ASK = {"en": "Check with your doctor or pharmacist before starting, stopping or changing any medicine.",
           "hi": "कोई भी दवा शुरू करने, बंद करने या बदलने से पहले अपने डॉक्टर या फार्मासिस्ट से पूछें।"}


_DOSE_AMOUNT = re.compile(r"(?i)\b\d+(?:[.,]\d+)?\s?(mg|mcg|µg|iu|units?|puffs?|drops?|tablets?|tabs?|capsules?|गोली|गोलियाँ|मिलीग्राम)\b"
                          r"(?!\s*/\s*(d[lL]|l\b))")
_DOSE_AMOUNT_MED = re.compile(r"(?i)\b\d+(?:[.,]\d+)?\s?(ml|g|grams?|ग्राम)\b(?!\s*/\s*(d[lL]|l\b))")
_SENTENCE = re.compile(r"(?<=[.!?।])\s+")


def _strip_doses(structured: dict, kind: str, lang: str) -> list[str]:
    """Never state how much of a medicine to take, for any kind of answer (the prompt forbids it, but the small
    models still write "500 mg to 1 g every 4–6 hours" or "do not exceed 4000 mg"). Dose amounts are removed from
    the reply, tips and self-care; a doctor/pharmacist referral is added instead. ml/grams count only in medicine
    answers, so food and ORS tips keep their amounts."""
    pats = [_DOSE_AMOUNT] + ([_DOSE_AMOUNT_MED] if kind == "medicine" else [])
    has = lambda s: any(p.search(s or "") for p in pats)  # noqa: E731
    removed = False
    for key in ("tips", "self_care"):
        items = structured.get(key)
        if isinstance(items, list):
            kept = [x for x in items if not (isinstance(x, str) and has(x))]
            if len(kept) != len(items):
                structured[key], removed = kept, True
    reply = structured.get("reply") or ""
    if has(reply):
        structured["reply"] = " ".join(s for s in _SENTENCE.split(reply) if not has(s)).strip()
        removed = True
    if not removed:
        return []
    ref = MED_REFERRAL.get(lang, MED_REFERRAL["en"])
    key = "tips" if "tips" in structured or "self_care" not in structured else "self_care"
    if ref not in (structured.get(key) or []):
        structured[key] = list(structured.get(key) or []) + [ref]
    return ["removed_dose_amount"]


_REFUSAL = re.compile(
    r"(?i)(can(not|'t)|unable to|not able to|won't|am not allowed to) (provide|give|offer|recommend)[^.]{0,40}"
    r"(medical advice|advice|diagnos|treatment|medic)|i('m| am) not a (doctor|medical)|not a substitute for "
    r"(professional )?medical|cannot diagnose|i can(not|'t) (help|tell) you|मैं डॉक्टर नहीं|चिकित्सा सलाह नहीं दे|सलाह नहीं दे सकत|निदान नहीं कर सकत")
_REFERRAL_ONLY = re.compile(r"(?i)^(please )?(consult|see|visit|talk to|speak (to|with)|ask) (a |your )?"
                            r"(doctor|physician|healthcare|health care|medical|pharmacist)")
NOT_MEDICINES = {"iron", "zinc", "calcium", "water", "oxygen", "sodium", "potassium", "magnesium", "vitamin",
                 "glucose", "salt", "sugar", "honey", "caffeine", "fiber", "fibre", "protein", "omega", "folate",
                 "lactose", "alcohol", "nicotine", "menthol", "camphor", "aloe", "turmeric", "ginger", "chlorine", "minerals", "vitamins", "mineral", "electrolytes", "probiotics", "fluoride"}
_med_re: list = []


def _is_refusal(structured: dict) -> bool:
    """Boilerplate refusal, or nothing to do except 'see a doctor'."""
    items = [x for k in ("self_care", "tips", "watch_for") for x in (structured.get(k) or []) if isinstance(x, str)]
    actionable = [x for x in items if not _REFERRAL_ONLY.search(x.strip())]
    return bool(_REFUSAL.search(structured.get("reply") or "")) or not actionable


def _strip_refusals(structured: dict) -> bool:
    reply = structured.get("reply") or ""
    parts = _SENTENCE.split(reply)
    kept = []
    for s in parts:
        if not _REFUSAL.search(s):
            kept.append(s)
            continue
        # "I'm not a doctor, but this can be linked to acidity." -> keep what follows the refusal clause
        m = re.search(r"(?i)[,;]\s*(but|however|though|still|that said)?\s*(.+)$", s)
        if m and not _REFUSAL.search(m.group(2)) and len(m.group(2).split()) >= 3:
            rest = m.group(2)
            kept.append(rest[0].upper() + rest[1:])
    if kept != parts:
        structured["reply"] = " ".join(kept).strip()
        return True
    return False


async def _medicine_names(db: AsyncSession):
    if not _med_re:
        from app.medicine.checks import BRANDS
        names = {n for n in await intent_mod._generic_names(db) if len(n) >= 5} | set(BRANDS)
        names -= NOT_MEDICINES
        _med_re.append(re.compile(r"(?i)\b(" + "|".join(sorted(map(re.escape, names), key=len, reverse=True)) + r")\b"))
    return _med_re[0]


async def _strip_medicine_names(db: AsyncSession, structured: dict, question: str, lang: str) -> list[str]:
    """Never name a medicine the person did not ask about (user's rule: advice, never medicine). Sentences and items
    naming one are removed and a doctor/pharmacist referral is added. Medicines the person named themselves can be
    discussed (e.g. 'is Dolo 650 safe with my BP tablets?')."""
    rx = await _medicine_names(db)
    from app.medicine.checks import BRANDS
    asked = {m.group(0).lower() for m in rx.finditer(question)}
    asked |= {g for n in list(asked) for g in BRANDS.get(n, [])}  # 'Dolo' asked -> paracetamol can be discussed
    bad = lambda t: any(m.group(0).lower() not in asked for m in rx.finditer(t or ""))  # noqa: E731
    removed = False
    for key in ("self_care", "tips", "watch_for", "nutrition"):
        items = structured.get(key)
        if isinstance(items, list):
            kept = [x for x in items if not (isinstance(x, str) and bad(x))]
            if len(kept) != len(items):
                structured[key], removed = kept, True
    reply = structured.get("reply") or ""
    if bad(reply):
        structured["reply"] = " ".join(s for s in _SENTENCE.split(reply) if not bad(s)).strip()
        removed = True
    if not removed:
        return []
    key = "self_care" if "self_care" in structured else "tips"
    ref = MED_REFERRAL.get(lang, MED_REFERRAL["en"])
    if ref not in (structured.get(key) or []):
        structured[key] = list(structured.get(key) or []) + [ref]
    return ["removed_medicine_name"]


NO_REPLY_LEAD = {"en": "Here is what you can do now, and the signs that mean you should see a doctor.",
                 "hi": "अभी आप ये कर सकते हैं, और इन संकेतों पर डॉक्टर को दिखाएँ।"}
CONSULT_MIN = 0.1  # reranker score; on the 150 chat cases: 92% of shown sources relevant, 75% of questions get one (eval/reports/consult_threshold.json)
CONSULT_TOP_MIN = 0.02  # the top-ranked hit alone: 91% relevant, 94% of questions get a source (same report, "top_hit")


# ---------------------------------------------------------------- long conversations
KEEP_EXCHANGES = 3  # last 3 question/answer pairs go to the model in full; older ones as a short note
_FOLLOWUP = re.compile(r"(?i)^\s*(and|aur|but|also|so|what about|how about|is it|will it|does it|can it|should i|how long|"
                       r"kitne din|kab tak|kya ye|kya yeh|ye|yeh|it|this|that|wo|woh|isme|isse|उसमें|और|क्या यह|क्या ये|"
                       r"यह|ये|कितने दिन|कब तक|इसमें|इससे)\b")


def is_followup(text: str) -> bool:
    """'How long will it last?', 'aur bacche mein?': a message that leans on the previous question's topic."""
    return len(text.split()) <= 6 or bool(_FOLLOWUP.match(text))


def retrieval_query(english: str, history: list[dict] | None) -> str:
    """Follow-ups search with the topic they refer to (the last question), so sources stay on topic."""
    prev = [h["content"] for h in (history or []) if h.get("role") == "user" and h.get("content")]
    if prev and is_followup(english) and os.environ.get("FOLLOWUP_CONTEXT", "on") != "off":
        return f"{prev[-1][:200]} {english}"
    return english


def followup_kind(english: str, history: list[dict] | None) -> str | None:
    """The kind of the chat a follow-up belongs to (from the last stored answer), or None if not a follow-up."""
    if not history or not is_followup(english):
        return None
    last = next((h for h in reversed(history) if h.get("role") == "assistant"), None)
    return ((last or {}).get("structured") or {}).get("kind")


def _brief_answer(h: dict) -> str:
    """An earlier answer as one line: its first sentence plus what it discussed (from the stored structured answer)."""
    st = h.get("structured") or {}
    first = _SENT_SPLIT.split((st.get("reply") or h.get("content") or "").strip())[0][:200]
    conds = ", ".join(c.get("name_en") or c.get("name", "") for c in (st.get("possible_conditions") or [])[:3])
    return first + (f" [possible: {conds}]" if conds else "")


def conversation_messages(history: list[dict] | None) -> list[dict]:
    """Model context for a long chat: older turns folded into one 'conversation so far' note (built from stored
    answers, no extra model call), then the last exchanges in full (answers trimmed)."""
    h = [x for x in (history or []) if x.get("content")]
    keep = h[-KEEP_EXCHANGES * 2:]
    older = h[: len(h) - len(keep)]
    out: list[dict] = []
    if older:
        lines = []
        for x in older[-16:]:
            lines.append(f"- Person asked: {x['content'][:160]}" if x["role"] == "user" else f"  You answered: {_brief_answer(x)}")
        out.append({"role": "user", "content": "CONVERSATION SO FAR (earlier messages, summarised):\n" + "\n".join(lines)})
        out.append({"role": "assistant", "content": "Noted. I will keep this context in mind."})
    for x in keep:
        content = x["content"] if x["role"] == "user" else _brief_answer(x) + "\n" + x["content"][:700]
        out.append({"role": x["role"], "content": content})
    return out


CAREFUL_KINDS = {"symptoms", "medicine", "report_question", "attachment"}
INSTANT_KINDS = {"greeting", "thanks_bye", "about_app", "off_topic"}


def english_pivot(force_english: bool, model_pref: str, lang: str, english: str, text: str) -> bool:
    """True when the answer should be written in English and translated back.

    Spoken questions always do this, so the chosen answer model reads English. Typed Gemini and Online
    questions stay in the person's language. Local models already do it for Bengali and the other pivot languages.
    """
    translated = bool((english or "").strip()) and (english or "").strip() != (text or "").strip()
    if not translated or lang == "en":
        return False
    if force_english:
        return True
    outside = model_pref in ("gemini", "gemini_fast", "cloud")
    return (not outside) and lang in PIVOT_LANGS


def effective_model_pref(pref: str, kind: str, has_images: bool) -> str:
    """The answer-speed choice (auto | balanced = Careful | fast = Faster | instant | cloud), except that symptoms and
    photos always get the careful medical model in every speed mode:
    the smaller model judged urgency correctly in only 16/30 chat-eval cases (heat stroke as "none").
    Auto: Careful for symptoms, medicines, reports and photos; Instant for small talk; Faster for the rest
    (food, lifestyle, general health questions)."""
    if pref == "auto":
        from app.llm.providers import model_options

        opts = model_options()
        if kind in CAREFUL_KINDS or has_images:
            return "balanced"
        if kind in INSTANT_KINDS and "instant" in opts:
            return "instant"
        return "fast" if "fast" in opts else "balanced"
    if pref in ("fast", "instant") and (kind == "symptoms" or has_images):
        return "balanced"
    return pref


def answered_speed(provider: str, model: str, requested: str) -> str:
    """The speed label for the model that actually wrote the answer, not the one that was asked for."""
    from app.config import get_settings
    s = get_settings()
    if provider == "gemini":
        return "gemini_fast" if model == s.gemini_fast_model else "gemini"
    if provider == "groq":
        return "cloud"
    if model == s.llm_model:
        return "balanced"
    return requested


def _medicine_guard(structured: dict, question: str, lang: str) -> list[str]:
    """Deterministic rules for medicine answers (the model's text is not trusted to get these right):
    a child + medicine question never gets dosing tips and always gets the child warning; every medicine answer
    ends with a doctor/pharmacist referral."""
    warnings, tips = [], list(structured.get("tips") or [])
    if _CHILD.search(question):
        kept = [x for x in tips if not _DOSE_TIP.search(x or "")]
        if len(kept) != len(tips):
            warnings.append("removed_child_dosing_tip")
        tips = [CHILD_MED.get(lang, CHILD_MED["en"])] + kept
        warnings.append("child_medicine_warning")
    if not _ANY_REFERRAL.search(" ".join([structured.get("reply", "")] + tips)):
        tips.append(MED_ASK.get(lang, MED_ASK["en"]))
    structured["tips"] = tips
    return warnings


def _raise(current: str, floor: str) -> str:
    return floor if URGENCY_RANK.get(floor, 0) > URGENCY_RANK.get(current, 0) else current


ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "possible_conditions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "name_en": {"type": "string"},
                    "likelihood": {"type": "string", "enum": ["high", "moderate", "low"]},
                    "urgency": {"type": "string",
                                "enum": ["emergency", "see_doctor_24h", "see_doctor_week", "self_care"]},
                    "supporting": {"type": "array", "items": {"type": "string"}},
                    "against": {"type": "array", "items": {"type": "string"}},
                    "citations": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["name", "name_en", "likelihood", "urgency"],
            },
        },
        "suggested_tests": {"type": "array", "items": {"type": "string"}},
        "specialist": {"type": "string", "enum": SPECIALISTS},
        "self_care": {"type": "array", "items": {"type": "string"}},
        "watch_for": {"type": "array", "items": {"type": "string"}},
        "nutrition": {"type": "array", "items": {"type": "string"}},
        "follow_up_questions": {"type": "array", "items": {"type": "string"}},
        "clinical_recap": {"type": "string"},
        "doctor_note": {"type": "string"},
        "citations_used": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["reply", "possible_conditions", "suggested_tests", "specialist", "self_care", "watch_for",
                 "follow_up_questions"],
}

_BANNED_OUTPUT = [
    re.compile(r"(?i)\byou (definitely|certainly) have\b"),
    re.compile(r"(?i)\b(increase|decrease|double|stop|discontinue) (your|the) (dose|medicine|medication|tablets?)\b"),
    re.compile(r"(?i)\btake \d+\s?(mg|ml|tablets?)\b"),
]


@dataclass
class TurnResult:
    reply: str
    structured: dict | None
    red_flag: dict
    citations: list[dict] = field(default_factory=list)
    disclaimer: str = ""
    provider: str | None = None
    model: str | None = None
    tier: str | None = None
    latency_ms: int | None = None
    warnings: list[str] = field(default_factory=list)
    timings: dict = field(default_factory=dict)  # seconds per stage (profile, guard, translate, search, model, ...)
    speed: str | None = None  # the answer-speed setting that actually answered (symptoms/photos: always balanced)
    english: str = ""  # the question in English (for memory extraction); "" when not translated
    kind: str | None = None  # what kind of question it was (symptoms, medicine, general_health …)


def _age(dob: str | None) -> float | None:
    if not dob:
        return None
    try:
        d = date.fromisoformat(dob)
    except ValueError:
        return None
    return (date.today() - d).days / 365.25


async def profile_summary(db: AsyncSession, cu: CurrentUser) -> tuple[str, float | None]:
    """Compact, minimum-necessary clinical context for the model."""
    p = await db.get(PatientProfile, cu.id)
    parts: list[str] = []
    age = None
    if p:
        age = _age(cu.cipher.dec(p.dob_enc))
        if age is not None:
            parts.append(f"age {age:.0f}y" if age >= 2 else f"age {age * 12:.0f} months")
        if p.sex:
            parts.append(f"sex {p.sex}")
        if p.pregnancy_status in ("pregnant", "possibly_pregnant"):
            parts.append(p.pregnancy_status.replace("_", " "))
        if p.lactating:
            parts.append("breastfeeding")
        if p.smoking_status and p.smoking_status != "never":
            parts.append(f"smoking {p.smoking_status}")
        if p.tobacco_chewing:
            parts.append("chews tobacco")
        if p.alcohol_units_wk:
            parts.append(f"alcohol {p.alcohol_units_wk:g} units/week")
        if p.diet:
            parts.append(f"diet {p.diet}")
        if p.state_code:
            parts.append(f"lives in {p.state_code}, India")
        if getattr(p, "occupation_enc", None):
            if occ := cu.cipher.dec(p.occupation_enc):
                parts.append(f"occupation {occ}")
        if getattr(p, "sleep_enc", None):
            if slp := cu.cipher.dec(p.sleep_enc):
                parts.append(f"sleep {slp}")
        if getattr(p, "hydration_enc", None):
            if hyd := cu.cipher.dec(p.hydration_enc):
                parts.append(f"hydration {hyd}")
        if getattr(p, "healthcare_access", None):
            parts.append(f"healthcare access: {p.healthcare_access}")
    conds = [cu.cipher.dec(c.display_enc) for c in await db.scalars(
        select(Condition).where(Condition.user_id == cu.id, Condition.status == "active"))]
    if conds:
        parts.append("conditions: " + ", ".join(conds))
    alls = [cu.cipher.dec(a.substance_enc) for a in await db.scalars(select(Allergy).where(Allergy.user_id == cu.id))]
    if alls:
        parts.append("ALLERGIES: " + ", ".join(alls))
    meds = [cu.cipher.dec(m.name_enc) for m in await db.scalars(
        select(Medication).where(Medication.user_id == cu.id, Medication.status == "active"))]
    if meds:
        parts.append("current medicines: " + ", ".join(meds))
    latest: dict[str, Vital] = {}
    for v in await db.scalars(select(Vital).where(Vital.user_id == cu.id).order_by(Vital.measured_at)):
        latest[v.type] = v
    if "bp_sys" in latest and "bp_dia" in latest:
        parts.append(f"BP {latest['bp_sys'].value:g}/{latest['bp_dia'].value:g}")
    for t, label in (("hr", "HR"), ("spo2", "SpO2"), ("glucose_f", "fasting glucose")):
        if t in latest:
            parts.append(f"{label} {latest[t].value:g} {latest[t].unit}")
    if "weight" in latest and p and p.height_cm:
        b = latest["weight"].value / (p.height_cm / 100) ** 2
        parts.append(f"BMI {b:.1f} ({bmi_category(b)}, Asian cut-offs)")
    summary = "; ".join(parts) or "no profile provided"
    if cu.is_member:  # a guardian asking for a family member: say "your mother", not "you"
        from app.models import HouseholdMember
        link = (await db.scalars(select(HouseholdMember).where(HouseholdMember.user_id == cu.account_id,
                                                               HouseholdMember.member_id == cu.id))).first()
        rel = (link.relation if link and getattr(link, "relation", None) else "family member")
        summary += (f". THE PERSON WRITING is the patient's guardian asking on behalf of their {rel}: speak to the "
                    f"guardian and refer to the patient as \"your {rel}\"")
    summary += await _explain_style(db, cu)
    return summary, age


# ---------------------------------------------------------------- how this person likes things explained
EXPLAIN_LEVELS = {
    1: ". EXPLAIN VERY SIMPLY for this person: very short sentences, everyday words, one familiar comparison; avoid "
       "medical terms (if one is needed, explain it plainly first, then 'doctors call this …')",
    2: "",
    3: ". This person likes more detail: you may explain the reasons briefly and use the medical term with a plain meaning",
}
_SIMPLER_ASK = re.compile(r"(?i)explain (that |it )?(more )?simply|in simple words|i (don'?t|do not|didn'?t) understand|"
                          r"samajh nahi aa?ya|aasan bhasha|समझ नहीं आया|आसान (भाषा|शब्दों)")
_MORE_ASK = re.compile(r"(?i)tell me more|more detail|in detail|explain more|go deeper|aur batao|विस्तार से")


async def _explain_style(db: AsyncSession, cu: CurrentUser) -> str:
    from app.models import HealthFact
    p = await db.get(PatientProfile, cu.id)
    f = await db.scalar(select(HealthFact).where(HealthFact.user_id == cu.id, HealthFact.kind == "preference",
                                                 HealthFact.name == "explain_depth").order_by(HealthFact.observed_at.desc()).limit(1))
    if f is not None:
        import json as _json
        level = int((_json.loads(cu.cipher.dec(f.value_enc)) or {}).get("level", 2))
        return EXPLAIN_LEVELS.get(level, "")
    if p is not None and getattr(p, "literacy_level", None) in ("limited", "non_reader"):
        return EXPLAIN_LEVELS[1]
    return ""


async def adapt_explain_depth(db: AsyncSession, cu: CurrentUser, text_en: str) -> None:
    """Adaptive explanation depth: "explain simply" / "I don't understand" lowers it, "tell me more" raises it;
    remembered per profile and used in every later answer (no setting to find)."""
    import json as _json

    from app.models import HealthFact
    step = -1 if _SIMPLER_ASK.search(text_en) else 1 if _MORE_ASK.search(text_en) else 0
    if not step:
        return
    f = await db.scalar(select(HealthFact).where(HealthFact.user_id == cu.id, HealthFact.kind == "preference",
                                                 HealthFact.name == "explain_depth").limit(1))
    level = int((_json.loads(cu.cipher.dec(f.value_enc)) or {}).get("level", 2)) if f else 2
    new = max(1, min(3, level + step))
    if f is None:
        db.add(HealthFact(user_id=cu.id, kind="preference", name="explain_depth", value_enc=cu.cipher.enc(_json.dumps({"level": new})),
                          source="chat", confidence=1.0, confirmed=True))
    else:
        f.value_enc = cu.cipher.enc(_json.dumps({"level": new}))
    await db.commit()


_HINGLISH = re.compile(r"(?i)\b(hai|hain|ho|raha|rahi|rahe|kya|karu|karun|karein|kare|karo|mein|mujhe|mera|meri|mere|"
                       r"nahi|nahin|bahut|dard|bukhar|khansi|khujli|jalan|pet|sar|kamar|bacche|bachche|din|hafte|"
                       r"baad|se|ke|ki|ka|aur|tha|thi|lagta|lagti|hota|hoti|khana|khane|dawai|dawa)\b")


def is_hinglish(text: str) -> bool:
    """Romanised Hindi ("subah se sar dard ho raha hai"): English retrieval needs it translated first."""
    words = re.findall(r"[A-Za-z]+", text)
    return len(words) >= 3 and len(_HINGLISH.findall(text)) >= max(2, len(words) // 4)


def is_banglish(text: str) -> bool:
    """Romanised Bengali / Bengali-influenced English ("amar matha ghurche"): needs translation for retrieval."""
    from app.speech.normalize import _BANGLISH_CUES

    words = re.findall(r"[A-Za-z]+", text)
    return len(words) >= 2 and len(_BANGLISH_CUES.findall(text)) >= 1


async def to_english(text: str, lang: str, *, pref: str = "auto") -> str:
    """Translate an Indic query to English for retrieval over English sources.
    Respects user's translation_model preference ('auto' | 'indictrans2' | 'llm' | 'gemini')."""
    if lang == "en" and text.isascii() and not is_hinglish(text) and not is_banglish(text):
        return text
    from app.config import get_settings
    from app.llm.providers import ProviderUnavailable, full_cloud_allowed, gemini, ollama
    from app.speech import models as speech

    if pref == "gemini" and full_cloud_allowed.get() and gemini().available:
        try:
            res = await gemini().chat(get_settings().gemini_fast_model, [
                {"role": "system", "content": "Translate the user's text to plain English. Keep medicine names, "
                                              "numbers and durations exactly. Output only the translation."},
                {"role": "user", "content": text}], temperature=0.0)
            return res.text.strip().strip('"')[:600] or text
        except ProviderUnavailable:
            pass
    if pref != "llm" and lang in speech.LANGS and lang != "en" and speech.is_native_script(text) and speech.available("transformers"):
        try:
            return await asyncio.to_thread(speech.translate, text, lang, "en")
        except Exception:  # noqa: BLE001 — fall back to the LLM
            pass
    try:
        res = await ollama().chat(get_settings().llm_model, [
            {"role": "system", "content": "Translate the user's text to plain English. Keep medicine names, "
                                          "numbers and durations exactly. Output only the translation."},
            {"role": "user", "content": text}], temperature=0.0, num_ctx=1024)
        return res.text.strip().strip('"')[:600] or text
    except ProviderUnavailable:
        return text


_SENT_SPLIT = re.compile(r"(?<=[.!?।])\s+")


def dedupe_repeats(text: str) -> tuple[str, bool]:
    """Drop sentences the model already said (runaway repetition loops). Returns (text, changed)."""
    seen: set[str] = set()
    out: list[str] = []
    changed = False
    for part in re.split(r"(\n+)", text or ""):
        if part.startswith("\n"):
            out.append(part)
            continue
        kept = []
        for sent in _SENT_SPLIT.split(part):
            key = re.sub(r"\W+", " ", sent).strip().lower()
            if len(key) > 20 and key in seen:
                changed = True
                continue
            seen.add(key)
            kept.append(sent)
        out.append(" ".join(kept))
    cleaned = re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()
    return cleaned, changed


# A paragraph or line that restates a list the app already shows on its own ("Self-care: …", "**Watch for:** …").
_LIST_LABEL = re.compile(
    r"^\s*(?:\*\*|#+\s*)?(self[- ]?care|watch(?:\s+out)?\s+for|warning signs?|red flags?|tips|tests?(?: to (?:do|consider))?"
    r"|suggested tests|possible (?:causes|conditions)|घरेलू देखभाल|ध्यान दें|चेतावनी के संकेत|ख़तरे के संकेत|खतरे के संकेत|जाँच)"
    r"\s*:?\s*(?:\*\*)?\s*:", re.I)
REPLY_MAX_SENTENCES = 6
LIST_MAX_ITEMS = 3


def _shorten_list(sentence: str) -> str:
    """"…seen with dengue, malaria, typhoid, TB, … (20 more)" -> the first few items, so a lead stays readable."""
    parts = [p for p in re.split(r",\s*", sentence)]
    if len(parts) <= LIST_MAX_ITEMS + 1:
        return sentence
    end = re.search(r"[.!?।]\s*$", sentence)
    return ", ".join(parts[:LIST_MAX_ITEMS]).rstrip(" ,") + (" and others" if re.search(r"[a-z]", parts[0], re.I) else " आदि") \
        + (end.group(0).strip() if end else ".")


def tidy_reply(text: str, max_sentences: int = REPLY_MAX_SENTENCES) -> tuple[str, bool]:
    """Keep `reply` short and readable: no blocks that repeat the separate lists, no runaway enumerations, at most
    `max_sentences` sentences (a model sometimes pastes a whole source passage). Paragraph breaks are kept."""
    if not text:
        return text, False
    changed = False
    paras_out: list[str] = []
    budget = max_sentences
    for para in re.split(r"\n\s*\n", text.strip()):
        lines = [ln for ln in para.split("\n") if ln.strip()]
        kept_lines = [ln for ln in lines if not _LIST_LABEL.match(ln)]
        if len(kept_lines) != len(lines):
            changed = True
        if not kept_lines or budget <= 0:
            changed = changed or bool(kept_lines)
            continue
        sents = [s for s in _SENT_SPLIT.split(" ".join(ln.strip() for ln in kept_lines)) if s.strip()]
        short = [_shorten_list(s) for s in sents[:budget]]
        if len(sents) > budget or short != sents[:budget]:
            changed = True
        budget -= len(short)
        paras_out.append(" ".join(short))
    out = "\n\n".join(paras_out).strip()
    if not out:  # everything was a list label: keep the original rather than show nothing
        return text, False
    if out[-1] not in ".!?।…":
        out += "।" if re.search(r"[ऀ-ॿ]", out[-12:]) else "."
    return out, changed


_REPLY_FIELD = re.compile(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)', re.S)


_ESC = {"n": "\n", "t": " ", '"': '"', "\\": "\\", "/": "/", "r": "", "b": "", "f": ""}


def partial_reply(acc: str) -> str:
    """The "reply" string of a JSON object that is still being written (escapes decoded, incomplete tail dropped)."""
    m = _REPLY_FIELD.search(acc or "")
    if not m:
        return ""
    raw, out, i = m.group(1), [], 0
    while i < len(raw):
        ch = raw[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= len(raw):
            break  # escape cut in half
        nxt = raw[i + 1]
        if nxt == "u":
            hexs = raw[i + 2:i + 6]
            if len(hexs) < 4:
                break
            try:
                out.append(chr(int(hexs, 16)))
            except ValueError:
                pass
            i += 6
            continue
        out.append(_ESC.get(nxt, nxt))
        i += 2
    return "".join(out)


def _reply_from_broken_json(text: str) -> str:
    """If the model's JSON was cut off and could not be repaired, show only the human-readable reply text —
    never raw JSON — and say the answer was incomplete."""
    m = _REPLY_FIELD.search(text or "")
    if not m:
        return text if not text.lstrip().startswith("{") else \
            "Sorry — I could not finish that answer. Please ask again, or consult a doctor."
    import json as _json

    try:
        reply = _json.loads(f'"{m.group(1)}"')
    except ValueError:
        reply = m.group(1).replace("\\n", "\n").replace('\\"', '"')
    reply, looped = dedupe_repeats(reply)
    if looped:  # it ran out of tokens because it was looping; the de-duplicated text is the complete answer
        return reply.strip()
    return reply.strip() + " …\n\n(The answer was cut short — please ask a follow-up or consult a doctor.)"


def _system_prompt(lang: str) -> str:
    rules = "\n".join(f"- {r}" for r in PROHIBITIONS)
    return f"""You are SushrutAI, a calm, attentive, and empathetic medical guidance assistant for people in India.
Speak like an experienced, kind family doctor in a consultation: acknowledge what the patient is feeling, reflect back the key details they shared, explain what the symptoms could match in plain language, and guide them clearly on what to do now and when to seek care. (The app already shows that you do not replace a doctor, so do not say it.)

Rules:
{rules}
- Use the numbered SOURCES when relevant and cite them by number in `citations` / `citations_used`. Do not invent sources.
- Consider conditions common in India (e.g. dengue, malaria, typhoid, TB, anaemia, diabetes, hypertension) where the season/region make sense, but do not over-diagnose.
- Rank possible conditions from most to least likely; give at most 5. Mark urgency honestly; fever for 3 or more
  days, or any condition that needs tests (dengue, malaria, typhoid, TB…), is at least `see_doctor_24h`.
- `name_en` is the condition's standard English name; `name` is in the user's language.
- `specialist` must be one of the allowed values; use "General Physician" when unsure.
- When advising what doctor to see, respect Indian medical qualification tiers: recommend MBBS primary care for initial undifferentiated assessment, MD/MS/DNB for specialist evaluation, and DM/MCh only when advanced subspecialty care is genuinely indicated. Never invent a doctor's name, registration number, hospital, or test reference range, and never advise delaying emergency care for routine tests.
- Personalise to the patient context (age, sex, pregnancy, allergies, conditions, current medicines). Flag allergy or interaction concerns.
- Plain words first: when a medical idea is needed, explain it with everyday words or a comparison familiar in
  India FIRST, and only then, optionally, "doctors call this …".
- "What if …?" questions ("what if I had taken the other medicine?", "what usually happens when I ignore this
  headache?"): answer only from THEIR HEALTH MEMORY plus clearly labelled general knowledge ("In general, …"); never
  invent their history. If they ask what may happen next, describe it as "if it goes like last time …" from the
  memory, always end with the warning signs, and never do this when urgency is see_doctor_24h or emergency.
- THEIR HEALTH MEMORY is the person's own history. Link what they say now to the ONGOING thread ("the temperature"
  means the ongoing fever). If they ask whether this happened before, what usually works for them, or to compare
  with an earlier time, answer from the memory only, with dates; if the memory has nothing, say so.
- Nutrition advice should fit Indian diets (dal, roti, rice, millets, curd, seasonal fruit) and the user's diet preference.
- Help the person now with a warm doctor–patient bedside manner. `reply`: 3-5 short, natural sentences in two short paragraphs separated by a blank line, in your own plain words (never copy passages from SOURCES, and name at most the two most likely conditions there — the full ranked list goes in `possible_conditions`):
  Paragraph 1: start with a brief, genuine acknowledgement of what they are going through (only if not already acknowledged in this turn) and reflect back key facts they shared, then explain what the symptoms can match and how likely.
  Paragraph 2: give the single most useful step right now and when to see which doctor. Put the single most important phrase in **double asterisks**. Keep sentences easy to listen to when read aloud. No headings and no bullet lists in `reply`, and do not repeat the `self_care` or `watch_for` items there (they are shown as their own lists).
- `clinical_recap` (optional, 1 short sentence): brief recap of what you have understood from the patient so far (e.g., "Fever with body ache for 3 days, no rash reported yet.").
- `doctor_note` (optional, 1 short sentence): what the patient should mention if they visit a clinic today.
- `self_care`: 3-6 concrete things to do at home (rest, fluids or ORS, food, hygiene, position, a warm compress for cramps or muscle pain, a cold one for a fresh injury or swelling, what to avoid) — no medicines. `watch_for`: 2-5 warning signs that mean seeing a doctor urgently. Say when and which doctor to see.
- If key information is missing, ask ONLY 1 natural, high-value follow-up question in `follow_up_questions` (never a 3-question list; keep it conversational like a doctor asking one question at a time).
- Do not guess unidentified medicines from vague descriptions ("white pill"); if the person cannot read the strip, ask them to share a photo or ask a pharmacist/health worker.
- Clearly distinguish patient-reported information vs confirmed diagnoses; never fabricate past consultations or missing dates.
- Do not repeat generic self-introductions or boilerplate disclaimers during an ongoing conversation; address the patient's exact question, correction, or causal hypothesis using CLINICAL REASONING STATE and THEIR HEALTH MEMORY directly.
- Write `reply` and every user-facing string in {LANG_NAMES.get(lang, 'English')}, in simple everyday words a rural or non-medical person understands. Keep medical terms in English in brackets when helpful.
- Respond ONLY with JSON matching the schema."""


GENERAL_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "tips": {"type": "array", "maxItems": 6, "items": {"type": "string"}},
        "watch_for": {"type": "array", "maxItems": 5, "items": {"type": "string"}},
        "follow_up_questions": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
        "citations_used": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["reply", "tips", "follow_up_questions"],
}

_KIND_FOCUS = {
    "nutrition_lifestyle": "This is a food, diet, exercise, sleep or lifestyle question. Give practical advice that fits "
                           "Indian meals (dal, roti, rice, millets, curd, sabzi, seasonal fruit) and the person's "
                           "diet preference and health conditions.",
    "medicine": "This is a question about a medicine: what it is for, how it is generally taken, common side effects, "
                "precautions and interactions with the person's other medicines or allergies. Never tell them to start, "
                "stop or change a dose — that is for their doctor or pharmacist.",
    "report_question": "This is a question about a medical test or report value. Explain in simple words what the "
                       "test measures and what the value usually means, and when to discuss it with a doctor.",
    "general_health": "This is a general health question. Answer it clearly and practically.",
    "off_topic": "This message may not be about health. If it has anything to do with health, the body, food, medicines "
                 "or wellbeing, answer it. Only if it is clearly unrelated to health, reply in one short, friendly "
                 "sentence that you can only help with health questions, and leave `tips` empty.",
}


def _general_prompt(lang: str, kind: str) -> str:
    rules = "\n".join(f"- {r}" for r in PROHIBITIONS)
    return f"""You are SushrutAI, a calm, attentive, and empathetic medical guidance assistant for people in India. Speak like a kind family doctor: acknowledge the person's concern warmly and give practical, specific guidance (the app already shows that you do not replace a doctor, so do not say it).
{_KIND_FOCUS.get(kind, _KIND_FOCUS["general_health"])}

Rules:
{rules}
- Help the person now with specific, practical advice. Do not give a list of diseases or tests unless they describe
  a symptom or problem; if they do, say briefly what it can be linked to ("this can be linked to ...") — never as certain.
- Put 2-5 concrete steps in `tips` (no medicines) and, when relevant, warning signs that need a doctor in `watch_for`.
- Personalise to the patient context (age, sex, pregnancy, allergies, conditions, current medicines).
- Plain words first: explain any medical idea with everyday words or a familiar comparison FIRST, then
  optionally "doctors call this …". "What if …?" questions: answer from THEIR HEALTH MEMORY plus clearly labelled
  general knowledge only.
- THEIR HEALTH MEMORY is the person's own history: answer questions about their past ("has this happened before?",
  "what usually works for me?", "compare with last month") from it only, with dates; if it has nothing, say so.
- Ask at most 1 short, natural follow-up question only if it would really help.
- Use the numbered SOURCES when relevant and cite them by number in `citations_used`. Do not invent sources.
- Write `reply` and every user-facing string in {LANG_NAMES.get(lang, 'English')}, in simple words a non-medical
  person understands. Keep the reply short: 2-5 sentences in one or two short paragraphs separated by a blank line,
  the single most important phrase in **double asterisks**, no headings, and no bullet lists in `reply` (steps go in
  `tips`, warning signs in `watch_for`).
- Respond ONLY with JSON matching the schema."""


async def _medicine_links(db: AsyncSession, text: str, limit: int = 3) -> list[dict]:
    """Generic medicine names mentioned in the question or answer -> links to their medicine page (price,
    cheaper options, how to take). Names come from the drug master, so only real medicines are linked."""
    from app.medicine import service as medicine_service

    words = re.findall(r"[a-z]{5,}", text.lower())
    names = await intent_mod._generic_names(db)
    out, seen = [], set()
    for w in words:
        if w in names and w not in seen:
            seen.add(w)
            hits = await medicine_service.search(db, w, limit=1)
            if hits:
                out.append({"name": w.capitalize(), "id": hits[0]["id"]})
            if len(out) >= limit:
                break
    return out


CHAT_DIFFERENTIAL = os.environ.get("CHAT_DIFFERENTIAL", "on").lower() not in ("off", "0", "false")


def _has_symptom_answer(history: list[dict] | None) -> bool:
    """This chat already had a symptom answer (the questions run once, for the first description)."""
    return any(h.get("role") == "assistant" and (h.get("structured") or {}).get("possible_conditions")
               for h in (history or []))


def _needs_clarify_translation(text: str, lang: str) -> bool:
    if not text or lang == "en":
        return False
    if not re.search(r"[A-Za-z]", text):
        return False
    if lang == "hi":
        return not re.search(r"[\u0900-\u097F]", text)
    return lang in PIVOT_LANGS


async def _clarify_turn(step, lang: str, rf) -> TurnResult:
    """A question from the chat's differential (yes / no / not sure), in the person's language."""
    st = dict(step.structured)
    if lang != "en" and (lang in PIVOT_LANGS or lang == "hi"):
        from app.speech import models as speech
        opts = st["clarify"]["options"]
        to_tr: list[str] = []
        idx_map: list[tuple[str, int]] = []
        reply_lines = (st.get("reply") or "").split("\n")
        reply_line_indices: list[tuple[int, int]] = []
        for li, ln in enumerate(reply_lines):
            if _needs_clarify_translation(ln, lang):
                reply_line_indices.append((li, len(to_tr)))
                to_tr.append(ln)
        for i, o in enumerate(opts):
            if _needs_clarify_translation(o.get("label") or "", lang):
                idx_map.append((f"opt:{i}", len(to_tr)))
                to_tr.append(o["label"])
        if to_tr:
            try:
                out = await asyncio.to_thread(speech.translate_many, to_tr, "en", lang)
                if reply_line_indices:
                    new_lines = list(reply_lines)
                    for li, pos in reply_line_indices:
                        new_lines[li] = out[pos]
                    translated_reply = "\n".join(new_lines)
                    st["reply"] = translated_reply
                    st["clarify"] = {**st["clarify"], "question": translated_reply}
                new_opts = list(opts)
                for tag, pos in idx_map:
                    idx = int(tag.split(":")[1])
                    new_opts[idx] = {**new_opts[idx], "label": out[pos]}
                st["clarify"] = {**st["clarify"], "options": new_opts}
            except Exception:  # noqa: BLE001 — English is still shown
                pass
    return TurnResult(reply=st["reply"], structured=st, red_flag=rf.as_dict(), disclaimer=disclaimer(lang), kind="clarify")


def _fixed_turn(kind: str, lang: str, rf) -> TurnResult:
    """Greeting, thanks, 'who are you', off-topic: a fixed reply plus shortcuts — no model, no diagnosis."""
    reply = intent_mod.fixed_reply(kind, lang)
    return TurnResult(reply=reply, structured={"kind": kind, "reply": reply, "actions": intent_mod.ACTIONS},
                      red_flag=rf.as_dict(), disclaimer=disclaimer(lang))


def _sources_block(hits: list[Hit]) -> str:
    if not hits:
        return "SOURCES: (none retrieved — rely on general medical knowledge and say so)"
    return "SOURCES:\n" + "\n\n".join(f"[{i + 1}] {h.title}\n{h.content[:1200]}" for i, h in enumerate(hits))


def _sanitize(structured: dict, n_sources: int) -> list[str]:
    warnings = []

    def _valid(c: list) -> list[int]:
        return [x for x in c if isinstance(x, int) and 1 <= x <= n_sources]

    for pc in structured.get("possible_conditions", []) or []:
        before = pc.get("citations") or []
        pc["citations"] = _valid(before)
        if len(pc["citations"]) != len(before):
            warnings.append("dropped_invalid_citation")
    structured["citations_used"] = _valid(structured.get("citations_used") or [])
    text_fields = ([structured.get("reply", "")] + list(structured.get("self_care", []) or [])
                   + list(structured.get("tips", []) or []))
    for t in text_fields:
        for pat in _BANNED_OUTPUT:
            if pat.search(t or ""):
                warnings.append(f"prohibited_phrase:{pat.pattern}")
    return warnings


async def answer(db: AsyncSession, cu: CurrentUser, text: str, *, lang: str = "en", session_id: str | None = None,
                 history: list[dict] | None = None, images: list[str] | None = None,
                 allow_cloud: bool = False, context: str = "", hint_kind: str | None = None,
                 on_delta: Callable[[str], None] | None = None, model_pref: str = "balanced",
                 spoken_prefix: str | None = None, force_english: bool = False,
                 use_record: bool = True, sos_enabled: bool = True, live_mode: bool = False,
                 speech_mode: str = "general", response_style: str | None = None) -> TurnResult:
    """context: records attached with 'Ask about this'; hint_kind: the kind of question that record implies.
    on_delta: receives the reply text written so far while the model is writing (a draft: the final TurnResult
    is authoritative, after every guard has run)."""
    import time as _time

    T: dict[str, float] = {}
    _clock = [_time.perf_counter()]

    def lap(stage: str) -> None:
        now = _time.perf_counter()
        T[stage] = round(now - _clock[0], 2)
        _clock[0] = now

    summary, age = await profile_summary(db, cu) if use_record else ("isolated chat session (no past records loaded)", None)
    trans_pref = "auto"
    eff_response_style = response_style or "direct"
    if cu:
        try:
            from app.security.settings import get_settings_row
            s_row = await get_settings_row(db, cu.account_id)
            trans_pref = getattr(s_row, "translation_model", None) or "auto"
            if speech_mode == "general":
                speech_mode = getattr(s_row, "speech_mode", None) or "general"
            if response_style is None:
                eff_response_style = getattr(s_row, "response_style", None) or "direct"
        except Exception:  # noqa: BLE001
            trans_pref = "auto"
    lap("profile")
    rf = red_flags.check(text, lang=lang, age_years=age) if sos_enabled else red_flags.RedFlagResult(False)
    if sos_enabled and not rf.triggered:
        # Warning signs split across messages ("chest pain" … "and my left arm feels heavy"): check the last
        # few user turns together, but only when no earlier turn already raised it on its own (no repeats).
        prev = [h["content"] for h in (history or []) if h.get("role") == "user"][-2:]
        if prev:
            joined = red_flags.check(" ".join([*prev, text]), lang=lang, age_years=age)
            if joined.triggered and not any(red_flags.check(p, lang=lang, age_years=age).triggered for p in prev):
                rf = joined
    if sos_enabled and not rf.triggered:
        from app.clinical.extract import extract_deterministic
        from app.safety.clinical_safety import evaluate_safety

        det = extract_deterministic(
            text,
            patient_id=cu.id if cu else "anonymous",
            encounter_id=session_id,
            language=lang,
            raw_transcript=text,
        )
        cs = evaluate_safety(text, lang=lang, age_years=age, extraction=det)
        if cs.escalate_now:
            rf = red_flags.RedFlagResult(
                True,
                rules=cs.rule_ids,
                kind="emergency" if cs.severity == "emergency" else "mental_health",
                messages=cs.messages,
                call_numbers=cs.call_numbers,
            )
    if rf.triggered and rf.kind in ("emergency", "mental_health"):
        return TurnResult(reply="\n\n".join(rf.messages), structured=None, red_flag=rf.as_dict(),
                          disclaimer=disclaimer(lang))
    # Purely conversational messages ("hi", "hello", "thanks", "who are you") get a fixed reply immediately
    # before chat_flow or translation can intercept or slow them down.
    if not images and (conv := intent_mod._phrase_intent(text)):
        if conv == "greeting" and cu is not None:
            try:
                from app.clinical.profile_onboarding import greeting_with_onboarding, profile_needs_onboarding
                miss_n, miss_a, miss_g, ex_n = await profile_needs_onboarding(db, cu)
                return TurnResult(
                    reply=greeting_with_onboarding(
                        lang, missing_name=miss_n, missing_age=miss_a, missing_gender=miss_g, existing_name=ex_n,
                    ),
                    structured=None, red_flag=rf.as_dict(), disclaimer=disclaimer(lang), kind="greeting",
                )
            except Exception:  # noqa: BLE001
                pass
        return _fixed_turn(conv, lang, rf)
    if not images and cu is not None:
        try:
            from app.clinical.profile_onboarding import capture_onboarding_identity
            cap = await capture_onboarding_identity(db, cu, text, lang=lang)
            if cap:
                if cap.get("age") is not None and age is None:
                    age = float(cap["age"])
                if cap.get("reply"):
                    return TurnResult(
                        reply=str(cap["reply"]), structured=None, red_flag=rf.as_dict(),
                        disclaimer=disclaimer(lang), kind="greeting",
                    )
        except Exception:  # noqa: BLE001
            pass
    # In the middle of the chat's questions: "yes" / "no" / "not sure" answer the pending question
    diff = None
    if session_id and not images and CHAT_DIFFERENTIAL:
        from app.triage import chat_flow
        eng0 = await to_english(text, lang) if lang in PIVOT_LANGS and not text.isascii() else ""
        step = await chat_flow.handle_reply(db, cu, session_id, text, eng0, lang if lang in chat_flow.T else "en")
        if step.kind == "ask":
            return await _clarify_turn(step, lang, rf)
        if step.kind == "finish":
            diff = step
            text = step.complaint or text  # the answer is about what they first described
        elif step.kind == "none" and step.context:
            context = (context + "\n\n" if context else "") + step.context
    # Fast-path Talk Live when cloud Gemini is active: Gemini understands bn/hi/en natively,
    # so avoid blocking 4-14s on local IndicTrans2 translation or 1s on Ollama llama-guard.
    live_cloud_fast = bool(live_mode and full_cloud_allowed.get(False) and model_pref in ("gemini_fast", "gemini", "cloud", "auto"))
    if live_cloud_fast and not force_english:
        english = text
        lap("translate")
    else:
        english = await to_english(text, lang, pref=trans_pref) if (force_english or lang in PIVOT_LANGS) else None
        lap("translate")
    guard_on_english = bool(english and english.strip() and english.strip() != text.strip())
    g = guard.rule_check(text, lang) if (guard_on_english or live_mode) else None
    if g is None:
        g = await guard.check_user_message(english if guard_on_english else text, "en" if guard_on_english else lang)
    lap("guard")
    if g.unsafe:
        msg = g.message
        if guard_on_english and msg:
            from app.speech import models as speech
            try:
                msg = (await asyncio.to_thread(speech.translate_many, [msg], "en", lang))[0]
            except Exception:  # noqa: BLE001 — the English message is still shown
                pass
        red = {"triggered": True, "kind": "mental_health" if "S11" in g.categories else "blocked",
               "rules": [f"llama_guard:{c}" for c in g.categories], "messages": [msg],
               "call_numbers": g.call_numbers or []}
        return TurnResult(reply=msg, structured=None, red_flag=red, disclaimer=disclaimer(lang),
                          warnings=[f"guard:{c}" for c in g.categories])

    if english is None:
        english = await to_english(text, lang, pref=trans_pref)
        lap("translate")
    try:
        await adapt_explain_depth(db, cu, english or text)
    except Exception:  # noqa: BLE001
        pass
    out_lang = lang
    # Outside models answer in the person's language and can stream that draft. The small local models still
    # answer in English for the pivot languages, then translate_answer() brings it back.
    pivot = english_pivot(force_english, model_pref, out_lang, english or "", text)
    if pivot:
        lang, on_delta = "en", None  # no live draft: it would be English
    kind = await intent_mod.classify(db, english if pivot else (f"{text} {english}" if english != text else text),
                                     has_image=bool(images))
    lap("intent")
    if hint_kind and kind in ("general_health", "off_topic", "greeting", "thanks_bye", "about_app"):
        kind = hint_kind  # "explain this" about an attached lab result is a report question
    if kind in ("general_health", "off_topic") and followup_kind(english, history) == "symptoms":
        kind = "symptoms"  # "how long will it last?" in a symptom chat keeps its urgency and possible causes
    if kind in intent_mod.FIXED_REPLY and not context:  # exact greeting / thanks / "who are you" phrases only
        return _fixed_turn(kind, lang, rf)
    if diff:
        kind = "symptoms"
    symptomatic = kind == "symptoms"
    speed = effective_model_pref(model_pref, kind, bool(images))
    if (symptomatic and diff is None and session_id and CHAT_DIFFERENTIAL and not images and not context
            and not rf.triggered and not live_mode):
        from app.triage import chat_flow
        complaint_en = english if (english or "").strip() and (english or "").strip() != text.strip() else text
        if await chat_flow.should_start(db, cu, session_id, complaint_en, history):
            mem0 = ""
            if use_record:
                try:
                    from app.memory.store import memory_block
                    mem0 = await memory_block(db, cu, english or "")
                except Exception:  # noqa: BLE001
                    mem0 = ""
            step = await chat_flow.start(db, cu, session_id, complaint_en, summary, age,
                                         out_lang if out_lang in chat_flow.T else "en", mem0,
                                         allow_cloud=allow_cloud, model_pref=speed)
            lap("plan")
            if step.kind == "ask":
                return await _clarify_turn(step, out_lang, rf)
            if step.kind == "finish":
                diff = step
    if diff is not None:
        context = (
            (context + "\n\n" if context else "")
            + diff.context
            + "\nCONSULTATION HANDOFF: You just finished asking the patient clarifying questions step by step. "
              "Open warmly by thanking them and briefly summarizing what their answers showed (present symptoms and reassuringly absent signs), "
              "then explain the narrowed possibilities, home care, and when to visit a doctor in simple words."
        )
        # The question round (the description, then question / yes / no …) is summed up in diff.context; left in
        # the history, the small model copies the last question back instead of answering (seen in Hindi).
        lead_complaint = (diff.complaint or "").split(";")[0].strip()
        cut = next((i for i in range(len(history or []) - 1, -1, -1)
                    if history[i].get("role") == "user" and (history[i].get("content") or "").strip() in ((diff.complaint or "").strip(), lead_complaint)), None)
        history = (history or [])[:cut] if cut is not None else (history or [])[:-1]
        if diff.low_confidence and allow_cloud and model_pref in ("auto", "balanced"):
            model_pref = "cloud"  # hybrid escalation: a selected cloud model reasons over the refined picture
        speed = effective_model_pref(model_pref, kind, bool(images))
    if not sos_enabled:
        context = (
            (context + "\n\n" if context else "")
            + "CONSULTATION MODE (SOS INTERRUPT DISABLED): The user turned off automatic 108/112 emergency stops so they can continue asking questions. "
              "Do NOT end the conversation or refuse to answer by repeating 'call 108/112' or 'go to emergency immediately'. "
              "Directly answer their question, discuss possibilities, suggest helpful follow-up questions, and mention warning signs calmly."
        )
    try:
        from app.clinical.labs import educate_lab_mentions
        lab_edu = educate_lab_mentions(english or text, age_years=age)
        if lab_edu:
            context = (context + "\n\n" if context else "") + lab_edu
    except Exception:  # noqa: BLE001
        pass
    if speech_mode == "elderly":
        context = (
            (context + "\n\n" if context else "")
            + "ELDERLY-FRIENDLY COMMUNICATION MODE: Speak warmly, patiently, and respectfully using simple everyday words "
              "(avoid complicated medical jargon). Keep sentences clear, ask at most one short easy-to-understand question at a time, "
              "gently confirm important details, and seamlessly accommodate fragmented descriptions, topic switches, repetitions, or corrections."
        )
    if eff_response_style == "direct":
        context = (
            (context + "\n\n" if context else "")
            + "NATURAL DIRECT DOCTOR-PATIENT MODE: Respond like a caring, experienced family doctor talking face-to-face. "
              "Be warm and human, but skip long introductory stories or repeating the patient's question back. "
              "Lead immediately with the direct clinical takeaway in 2–3 natural sentences, give practical self-care and clear diet/food guidance "
              "(what to eat/drink AND what to avoid/restrict in `nutrition`), and ask only 1 focused clarifying question if needed."
        )
    else:
        context = (
            (context + "\n\n" if context else "")
            + "DETAILED EXPLANATION MODE: Provide thorough, step-by-step clinical reasoning, detailed home-care steps, "
              "comprehensive diet/nutrition and food/activity restrictions, and clear warning signs."
        )
    context = (
        (context + "\n\n" if context else "")
        + "CONTINUOUS DOCTOR-PATIENT LOOP: Never end a consultation turn on a dead-end closing statement unless the user says goodbye "
          "or it is an emergency. Always keep the patient in the loop like an attentive doctor by ending `reply` with ONE natural, "
          "context-aware check-in question or practical offer (e.g., checking current medicines, suggesting diet/relief steps, "
          "or asking if anything else is bothering them), and populate `follow_up_questions` with 2–3 helpful next prompts."
    )
    # Instant & Live Cloud Fast: skip heavy local bge-m3 embedding/reranker so Talk Live stays sub-2s
    hits = [] if live_cloud_fast else await search(
        db, f"{retrieval_query(english, history)} {context}".strip(),
        collections=["guidelines_in", "patient_ed", "drug_monographs", "nutrition_in"],
        rerank_top=4 if speed == "instant" else 10,
    )
    lap("search")
    messages = [{"role": "system", "content": _system_prompt(lang) if symptomatic else _general_prompt(lang, kind)}]
    messages += conversation_messages(history)
    record = f"RECORD THE USER IS ASKING ABOUT (from their own health record):\n{context}\n\n" if context else ""
    try:  # the person's own health memory + active clinical reasoning state
        from app.clinical.extract import extract_deterministic
        from app.clinical.reasoning import ClinicalReasoningEngine
        from app.memory.context import PatientContextBuilder
        from app.memory.store import memory_block
        from app.safety.clinical_safety import evaluate_safety

        ep_mem = await memory_block(db, cu, english or "") if use_record else ""
        from app.models import Encounter
        enc_row = (
            await db.scalar(select(Encounter).where(Encounter.user_id == cu.id, Encounter.chat_session_id == session_id).limit(1))
            if (session_id and cu) else None
        )
        if use_record:
            mem_ctx = await PatientContextBuilder().build(db, cu, query_text=english or text, encounter_id=enc_row.id if enc_row else None)
        elif enc_row is not None:
            mem_ctx = await PatientContextBuilder().build(db, cu, query_text=english or text, encounter_id=enc_row.id, encounter_only=True)
        else:
            mem_ctx = None
        det_now = extract_deterministic(english or text, patient_id=cu.id if cu else "anonymous", encounter_id=session_id, language=lang, raw_transcript=text)
        saf_now = evaluate_safety(english or text, lang=lang, age_years=age, extraction=det_now)
        reasoning_block = ""
        if session_id and cu and mem_ctx is not None and enc_row is not None:
            r_out = await ClinicalReasoningEngine().evaluate_and_persist(
                db, cu, encounter_id=enc_row.id, user_text=english or text, extraction=det_now, mem=mem_ctx, safety=saf_now
            )
            reasoning_block = r_out.get("prompt_block", "")
        parts_mem = [p for p in (reasoning_block, mem_ctx.prompt_block if mem_ctx else "", ep_mem) if p]
        mem = "\n\n".join(parts_mem)
    except Exception:  # noqa: BLE001 — memory is context, never a reason to fail
        mem = ""
    record += f"THEIR HEALTH MEMORY (their own history, from earlier chats and readings):\n{mem}\n\n" if mem else ""
    heard = (f"ALREADY SAID ALOUD (the person interrupted after this): {spoken_prefix[:1000]}\n\n"
             if spoken_prefix else "")
    messages.append({"role": "user", "content": heard + f"PATIENT CONTEXT: {summary}\n\n{record}{_sources_block(hits)}\n\n"
                                                + (f"USER MESSAGE (translated from {LANG_NAMES.get(out_lang, out_lang)}): "
                                                   f"{english}" if pivot else
                                                   f"USER MESSAGE ({LANG_NAMES.get(lang, lang)}): {text}"
                                                   + (f"\n(English: {english})" if english != text else ""))})
    last = [""]

    def _draft(acc: str) -> None:
        if on_delta is None:
            return
        part = partial_reply(acc)
        if part and part != last[0]:
            last[0] = part
            on_delta(part)

    res: LlmResult = await clinical_chat(messages, schema=ANSWER_SCHEMA if symptomatic else GENERAL_SCHEMA,
                                         images=images, allow_cloud=allow_cloud,
                                         num_predict=1100, repeat_penalty=1.15,
                                         on_token=_draft if on_delta else None,
                                         model_pref=speed)  # cap: some prompts made the 4B model run away
    lap("model")
    try:
        structured = res.json()
        if not isinstance(structured, dict):
            raise ValueError("not an object")
    except ValueError:
        structured = {"reply": _reply_from_broken_json(res.text), "possible_conditions": [], "suggested_tests": [],
                      "specialist": "", "self_care": [], "follow_up_questions": []}
    if _is_refusal(structured) and not rf.triggered:
        # one retry with a correction: the person asked for help, so give practical help (never medicines)
        nudge = ("Your answer refused or only said to see a doctor. Answer again and help the person now: what it can "
                 "be, concrete things to do at home, warning signs, and when and which doctor to see. Do not name "
                 "medicines. Do not say you cannot give advice. Respond ONLY with JSON matching the schema.")
        try:
            res2 = await clinical_chat(messages + [{"role": "assistant", "content": res.text}, {"role": "user", "content": nudge}],
                                       schema=ANSWER_SCHEMA if symptomatic else GENERAL_SCHEMA, images=images,
                                       allow_cloud=allow_cloud, num_predict=1100, repeat_penalty=1.15, model_pref=speed)
            s2 = res2.json()
            if isinstance(s2, dict) and not _is_refusal(s2):
                structured, res = s2, res2
            lap("refusal_retry")
        except (ValueError, ProviderUnavailable):
            pass
    refusal_warnings = ["refusal_retry"] if "refusal_retry" in T else []
    if _strip_refusals(structured):
        refusal_warnings.append("removed_refusal_sentence")
        if not (structured.get("reply") or "").strip():
            structured["reply"] = NO_REPLY_LEAD.get(lang, NO_REPLY_LEAD["en"])
    if isinstance(structured.get("reply"), str):
        structured["reply"] = dedupe_repeats(structured["reply"])[0]
        structured["reply"], trimmed = tidy_reply(structured["reply"])
        if trimmed:
            refusal_warnings.append("reply_tidied")
    if diff is not None and diff.ranked:
        model_top = next(iter(structured.get("possible_conditions") or []), None)
        structured["possible_conditions"] = [
            {"name": r["name"], "name_en": r["name"], "likelihood": r["likelihood"], "urgency": r["urgency"],
             "supporting": r.get("supporting", []), "against": r.get("against", []), "citations": []}
            for r in diff.ranked]
        # The question plan can miss what the description itself points to (white patches on the tonsils →
        # tonsillitis): the answer model's own first pick is kept when it is not in the ranking.
        if model_top and model_top.get("name_en"):
            from rapidfuzz import fuzz
            mt = model_top["name_en"].lower()
            hit = next((i for i, r in enumerate(diff.ranked) if fuzz.partial_ratio(mt, r["name"].lower()) >= 80), None)
            if hit is None:
                structured["possible_conditions"] = structured["possible_conditions"][:2] + [
                    {**model_top, "likelihood": "moderate", "citations": model_top.get("citations", [])}]
            elif hit > 0 and not diff.ranked[0].get("supporting"):
                # The plan's starting odds can be off (influenza first for loose motions) and "no" answers do not
                # separate the candidates: a top pick that no answer supports does not outrank the model's own
                # reading of the whole description.
                pc = structured["possible_conditions"]
                pc.insert(0, pc.pop(hit))
        if lang not in ("en",) and not pivot:  # Hindi (answered natively): names in Hindi, English kept alongside
            from app.speech import models as speech
            pc = [c for c in structured["possible_conditions"] if (c.get("name") or "") == (c.get("name_en") or "")]
            try:
                names = await asyncio.to_thread(speech.translate_many, [c["name_en"] for c in pc], "en", lang)
                for c, n in zip(pc, names):
                    if n and n.strip() and n.strip().lower() != c["name_en"].lower():
                        c["name"] = n.strip()
            except Exception:  # noqa: BLE001 — English names are still correct
                pass
        if diff.urgency:
            top = structured["possible_conditions"][0]
            top["urgency"] = _raise(top.get("urgency", "self_care"), diff.urgency)
        if diff.specialist and not structured.get("specialist"):
            structured["specialist"] = diff.specialist
        structured["differential"] = {"asked": True, "urgency": diff.urgency}
    if not symptomatic:  # never show a diagnosis, tests or a specialist for a non-symptom question
        for k in ("possible_conditions", "suggested_tests", "specialist"):
            structured.pop(k, None)
        structured.setdefault("tips", structured.pop("self_care", None) or [])
    structured["kind"] = kind
    structured["suggest_check"] = symptomatic
    warnings = _sanitize(structured, len(hits))
    care = structured.get("self_care") or []
    kept = [c for c in care if not _MED_ADVICE.search(c or "")]
    if len(kept) != len(care):
        warnings.append("removed_medicine_advice")
        structured["self_care"] = kept + [MED_REFERRAL.get(lang, MED_REFERRAL["en"])]
    if kind != "medicine":  # food / lifestyle / report tips must not slip in medicine suggestions either
        tips = structured.get("tips") or []
        kept_tips = [x for x in tips if not _MED_ADVICE.search(x or "")]
        if len(kept_tips) != len(tips):
            warnings.append("removed_medicine_advice")
            structured["tips"] = kept_tips + [MED_REFERRAL.get(lang, MED_REFERRAL["en"])]
    if kind == "medicine":
        warnings += _medicine_guard(structured, f"{text} {english}", lang)
    warnings += _strip_doses(structured, kind, lang)
    warnings += refusal_warnings
    warnings += await _strip_medicine_names(db, structured, f"{text} {english}", lang)
    # any question about a medicine or a dose always ends with who to ask (whatever kind it was classified as)
    q = f"{text} {english}"
    listed = "self_care" if "self_care" in structured else "tips"
    shown = " ".join([structured.get("reply") or ""] + [x for k in ("self_care", "tips", "watch_for")
                                                        for x in (structured.get(k) or []) if isinstance(x, str)])
    if (_DOSE_Q.search(q) or (await _medicine_names(db)).search(q)) and not _ANY_REFERRAL.search(shown):
        structured[listed] = list(structured.get(listed) or []) + [MED_ASK.get(lang, MED_ASK["en"])]
        warnings.append("added_medicine_referral")
    long_fever = bool(re.search(r"(?i)fever|बुखार|bukhar|ज्वर", f"{text} {english}")) and any(
        (m.group(1).isdigit() and int(m.group(1)) >= 3) or not m.group(1).isdigit() and m.group(1) not in ("two", "दो")
        for m in _FEVER_DAYS.finditer(f"{text} {english}"))
    for pc in structured.get("possible_conditions", []) or []:
        label = f"{pc.get('name_en', '')} {pc.get('name', '')}"
        for pat, floor in URGENCY_FLOOR:
            if pat.search(label):
                new = _raise(pc.get("urgency", "self_care"), floor)
                if new != pc.get("urgency"):
                    warnings.append(f"urgency_raised:{pc.get('name_en')}")
                pc["urgency"] = new
        if long_fever:
            pc["urgency"] = _raise(pc.get("urgency", "self_care"), "see_doctor_24h")
    if any(w.startswith("prohibited_phrase") for w in warnings):
        structured["reply"] = (structured.get("reply", "") +
                               "\n\n⚠️ Please follow the dose and duration your doctor prescribed; do not change "
                               "medicines without consulting them.")
    if rf.triggered and not pivot:  # 'urgent' rules: keep answer but surface banner
        structured["reply"] = "\n\n".join(rf.messages) + "\n\n" + structured.get("reply", "")
    cited = sorted({c for pc in structured.get("possible_conditions", []) for c in pc.get("citations", [])}
                   | set(structured.get("citations_used", [])))
    # The small models often cite every number they were given: keep only relevant passages, one per page.
    seen_pages: set[str] = set()
    kept_cited = []
    for n in cited:
        page = hits[n - 1].source_url or hits[n - 1].title
        if page not in seen_pages and (hits[n - 1].score >= CONSULT_MIN or (n == 1 and hits[0].score >= CONSULT_TOP_MIN)):
            seen_pages.add(page)
            kept_cited.append(n)
    citations = [{"n": n, "title": hits[n - 1].title, "url": hits[n - 1].source_url,
                  "source": hits[n - 1].source_id} for n in kept_cited]
    seen_pages = {hits[n - 1].source_url or hits[n - 1].title for n in cited}
    consulted = []
    for i, h in enumerate(hits, 1):  # one entry per page (several passages can come from the same page)
        page = h.source_url or h.title
        if i not in cited and page not in seen_pages and (h.score >= CONSULT_MIN or (i == 1 and h.score >= CONSULT_TOP_MIN)):
            seen_pages.add(page)
            consulted.append({"title": h.title, "url": h.source_url, "source": h.source_id})
    structured["consulted"] = consulted[:3]
    # Grounding: drop unsupported dose/number claims, flag other claims the sources don't back (Phase 3)
    import asyncio as _asyncio

    from app.rag.grounding import ground
    if os.environ.get("GROUNDING", "on").lower() not in ("off", "0", "false"):
        grounded_on = [hits[n - 1] for n in cited] or hits[:3]
        new_reply, gr = await _asyncio.to_thread(ground, structured.get("reply", ""), grounded_on, lang)
        structured["reply"], structured["grounding"] = new_reply, gr.as_dict()
        if gr.removed:
            warnings.append(f"grounding_removed:{len(gr.removed)}")
    lap("checks_grounding")
    structured["medicine_links"] = await _medicine_links(db, f"{text} {structured.get('reply', '')}")
    lap("medicine_links")
    if kind == "medicine" and not images:  # a medicine named in the question: its salt, a cheaper same-salt option,
        try:                               # and how it went for them before, told as one short story
            from app.medicine.pack import names_in
            from app.medicine.resolve import resolve
            from app.medicine.storyline import storyline
            named = names_in(f"{text} {english}") + [l["name"] for l in structured["medicine_links"]]
            seen: set[str] = set()
            stories = []
            for n in named:
                if n.lower() in seen or len(stories) >= 2:
                    continue
                seen.add(n.lower())
                stories.append(await storyline(db, cu, await resolve(db, n), given_by_doctor=False))
            if stories:
                structured["storylines"] = stories
        except Exception:  # noqa: BLE001 — the answer stands without it
            log.exception("medicine storyline failed")
        lap("storyline")
    if pivot:
        if await translate_answer(structured, out_lang, pref=trans_pref):
            structured["translated_from"] = "en"
        else:
            warnings.append("answer_translation_failed")
        if rf.triggered:
            structured["reply"] = "\n\n".join(rf.messages) + "\n\n" + structured.get("reply", "")
        lang = out_lang
        lap("translate_answer")
    log.info("turn timings %s model=%s", T, res.model)
    log.info("retrieval hits=%s", [(round(h.score, 3), h.title[:40]) for h in hits])  # no user text in logs
    return TurnResult(reply=structured.get("reply", ""), structured=structured, red_flag=rf.as_dict(),
                      citations=citations, disclaimer=disclaimer(lang), provider=res.provider, model=res.model,
                      tier=res.tier, latency_ms=res.latency_ms, warnings=warnings, timings=T,
                      speed=answered_speed(res.provider, res.model, speed),
                      english=english if english != text else "", kind=kind)
