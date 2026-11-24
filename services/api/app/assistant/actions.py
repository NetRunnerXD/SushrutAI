"""Voice actions: turn what someone says into something the app does.

Deterministic parsing (English, Hindi, Hinglish), unit-tested. Anything that saves data is returned as a
*proposal* the person confirms; nothing is written here. Questions ("is 130/85 normal?") are never actions —
they go to the assistant.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz


@dataclass
class Action:
    action: str                      # log_vital | log_dose | dose_status | navigate | read_latest | switch_profile
    params: dict = field(default_factory=dict)
    confirm: bool = False            # True = writes data: the person must say/press yes first


_QUESTION = re.compile(r"(?i)\?|^\s*(is|are|what|why|how|should|can|could|does|do|will|kya|kyon|kaise|क्या|क्यों|कैसे)\b")
_NUM = r"(\d{2,3}(?:\.\d)?)"

SLOT_WORDS = {
    "morning": r"morning|breakfast|subah|savere|सुबह|सवेरे",
    "afternoon": r"afternoon|lunch|noon|dopahar|dopeher|दोपहर",
    "night": r"night|bedtime|dinner|evening|raat|shaam|रात|शाम",
}


def slot_of(text: str) -> str | None:
    for slot, pat in SLOT_WORDS.items():
        if re.search(rf"(?i)\b(?:{pat})", text):
            return slot
    return None


# ---------------------------------------------------------------- vitals
_BP = re.compile(rf"(?i)(?:bp|b\.p\.|blood pressure|बीपी|ब्लड प्रेशर|रक्तचाप)?\D{{0,25}}?\b(\d{{2,3}})\s*"
                 rf"(?:/|over|by|upon|baata|bata|बटा|बाई|ऊपर|par)\s*(\d{{2,3}})\b")
_SUGAR = re.compile(rf"(?i)(?:sugar|glucose|शुगर|शक्कर|ग्लूकोज़?)\D{{0,25}}?{_NUM}|{_NUM}\s*(?:mg/?dl)?\s*(?:sugar|शुगर)")
_WEIGHT = re.compile(rf"(?i)(?:weight|vajan|wajan|wazan|वजन|वज़न)\D{{0,15}}?{_NUM}|{_NUM}\s*(?:kg|kgs|kilo|kilos|किलो)\b")
_PULSE = re.compile(rf"(?i)(?:pulse|heart ?rate|nabz|nadi|नब्ज़?|नाड़ी|धड़कन)\D{{0,15}}?{_NUM}")
_SPO2 = re.compile(rf"(?i)(?:oxygen|spo2|sp o2|saturation|ऑक्सीजन)\D{{0,15}}?{_NUM}")
_TEMP = re.compile(rf"(?i)(?:temperature|\btemp\b|thermometer|तापमान)\D{{0,15}}?{_NUM}")  # not "fever 101": that is a question
_FASTING = re.compile(r"(?i)fasting|empty stomach|khali pet|खाली पेट|फास्टिंग|before (breakfast|food)")
_AFTER_MEAL = re.compile(r"(?i)after (food|meal|eating|lunch|dinner|breakfast)|\bpp\b|post ?prandial|khane ke baad|खाने के बाद")


def parse_vitals(text: str) -> list[dict]:
    out: list[dict] = []
    if m := _BP.search(text):
        s, d = int(m.group(1)), int(m.group(2))
        if 60 <= s <= 260 and 30 <= d <= 160 and s > d:
            out += [{"type": "bp_sys", "value": s}, {"type": "bp_dia", "value": d}]
    if m := _SUGAR.search(text):
        v = float(m.group(1) or m.group(2))
        if 20 <= v <= 900:
            kind = "glucose_f" if _FASTING.search(text) else "glucose_pp" if _AFTER_MEAL.search(text) else "glucose_r"
            out.append({"type": kind, "value": v})
    if m := _WEIGHT.search(text):
        v = float(m.group(1) or m.group(2))
        if 2 <= v <= 300:
            out.append({"type": "weight", "value": v})
    if m := _PULSE.search(text):
        v = float(m.group(1))
        if 25 <= v <= 250:
            out.append({"type": "hr", "value": v})
    if m := _SPO2.search(text):
        v = float(m.group(1))
        if 50 <= v <= 100:
            out.append({"type": "spo2", "value": v})
    if m := _TEMP.search(text):
        v = float(m.group(1))
        if 93 <= v <= 110:  # said in Fahrenheit, as most Indian thermometers show
            v = round((v - 32) * 5 / 9, 1)
        if 30 <= v <= 44:
            out.append({"type": "temp", "value": v})
    return out


# ---------------------------------------------------------------- doses
_TOOK = re.compile(r"(?i)\b(i took|i have taken|i've taken|took|taken|have taken|mark(ed)? .{0,40}taken|"
                   r"le li|le liya|kha li|kha liya|li hai|liya hai)\b|ले ली|ले लिया|खा ली|खा लिया|ली है|लिया है")
_DID_I = re.compile(r"(?i)did i take|have i taken|did i have|maine .{0,30}(li|liya|khayi|khai)\s*(kya|\?)|"
                    r"kya maine|क्या मैंने|मैंने .{0,30}(ली|लिया|खाई) क्या")
_ALL_MEDS = re.compile(r"(?i)\b(all|my) (medicines?|tablets?|pills?|meds|doses?)\b|\bmedicines?\b|\btablets?\b|"
                       r"\bdawai|\bdawa\b|\bgoli|दवा|दवाई|गोली|गोलियां|गोलियाँ")


def match_medicines(text: str, meds: list[dict]) -> list[dict]:
    """Medicines named in the text (fuzzy on the first word: "pan forty" ~ "Pan 40" is handled by the number-free
    first token), or all of them when the person says "my medicines"."""
    t = text.lower()
    hits = []
    for m in meds:
        first = re.split(r"[\s\-]", m["name"].lower())[0]
        if len(first) >= 3 and (first in t or max((fuzz.ratio(first, w) for w in re.findall(r"[a-zऀ-ॿ]{3,}", t)), default=0) >= 85):
            hits.append(m)
    if not hits and _ALL_MEDS.search(text):
        return meds
    return hits


# ---------------------------------------------------------------- navigation
_OPEN = re.compile(r"(?i)\b(open|show|go to|take me to|see|view|kholo|khol do|dikhao|dikha do)\b|खोलो|खोल दो|दिखाओ|दिखा दो|ले चलो")
PLACES: list[tuple[str, str]] = [
    (r"visit summary|doctor summary|summary for (the )?doctor", "/me/visit"),
    (r"calendar|appointments?|कैलेंडर", "/"),
    (r"timeline|health history|my history", "/records?tab=timeline"),
    (r"chats?|conversations?|baatcheet|बातचीत|चैट", "/ask?chats=1"),
    (r"(sugar|glucose|शुगर)( trend| graph| chart)?", "/records?tab=vitals"),
    (r"report library|my documents|documents|uploads|दस्तावेज़", "/records?tab=library"),
    (r"vaccin|immuni|teeka|टीका|टीके|check-?ups?|pregnan", "/me/care"),
    (r"famil|परिवार", "/me/family"),
    (r"settings?|सेटिंग", "/me/settings"),
    (r"prescriptions?|parcha|पर्चा|पर्चे", "/medicines/rx"),
    (r"(blood )?pressure|\bbp\b|vitals?|sugar chart|weight chart|readings?|बीपी", "/records?tab=vitals"),
    (r"lab|reports?|tests?|रिपोर्ट|जांच|जाँच", "/records?tab=labs"),
    (r"x-?rays?|scans?|images?", "/records?tab=images"),
    (r"records?|history|रिकॉर्ड", "/records"),
    (r"medicines?|tablets?|dawai|दवा|दवाई", "/medicines"),
    (r"symptom check|check symptoms|लक्षण", "/ask/check"),
    (r"profile|प्रोफ़ाइल|प्रोफाइल", "/me"),
    (r"home|होम", "/"),
]
_READ_LATEST = re.compile(r"(?i)(read|tell|explain|sunao|batao|padho)\b.{0,25}\b(last|latest|recent|new|pichl\w*|nayi|naya)\b.{0,15}\b(report|result|test)|"
                          r"(last|latest|recent) (lab )?(report|result)s?\b.{0,10}(read|say|tell)|"
                          r"\b(pichl\w*|aakhri|akhri|nayi|naya|last|latest)\b.{0,15}\b(report|result)s?\b.{0,10}\b(sunao|batao|padho|padh do|suna do)\b|"
                          r"(पिछली|नई|आखिरी) रिपोर्ट (सुनाओ|बताओ|पढ़ो)")
_SWITCH = re.compile(r"(?i)\b(switch to|change to|open)\s+(.+?)(?:'s)?\s*(profile|record|account)?\s*$|"
                     r"^(.+?) (ka|ki|ke) (profile|record)\s*(kholo|dikhao)?$")
_SELF = re.compile(r"(?i)^(me|myself|my own|mine|mera|meri|main|मैं|मेरा|मेरी)$")


# Conversation controls, matched on the whole (short) utterance in English (other languages arrive here already
# translated to English by the router). The web app runs these itself: stop / pause / resume the voice loop,
# repeat, a simpler or a longer explanation, yes / no for a pending confirmation, open the emergency sheet.
CONTROLS: list[tuple[str, str]] = [
    ("emergency", r"help( me)?|emergency|sos|(call )?(an )?ambulance|call 108|call 112|save me"),
    ("stop", r"stop( it| talking| speaking)?|quiet|be quiet|shut up|enough|that'?s enough"),
    ("pause", r"pause|wait|hold on|one (minute|moment|second)|just a (minute|moment|second)"),
    ("resume", r"resume|continue|go on|carry on|start (listening )?again|i'?m back"),
    ("repeat", r"repeat( that| it)?|say (that |it )?again|again|once more|what did you say|pardon"),
    ("simpler", r"explain (it |that )?(more )?simply|simpler|in simple words|i (don'?t|do not|didn'?t) understand|make it simple|easy words"),
    ("more", r"tell me more|more( details?)?|explain more|in detail|go deeper|continue explaining|read (the )?(full|whole|rest)( answer)?"),
    ("yes", r"yes|yeah|yep|ok(ay)?|sure|go ahead|do it|confirm|correct|right|that'?s right|proceed"),
    ("no", r"no|nope|cancel|don'?t|do not|never ?mind|not now|leave it"),
    ("read", r"read (this|the page|it|this page|aloud|the screen)|read it out"),
]
_CONTROL_RX = [(name, re.compile(rf"(?i)^\s*(please\s+)?(?:{pat})(\s+please)?[\s.!?]*$")) for name, pat in CONTROLS]
_SUMMARY = re.compile(r"(?i)\b(prepare|make|create|generate|give me|show)\b.{0,20}\b(summary|report|brief|overview)\b.{0,20}\b(for )?(the )?(doctor|visit)?|"
                      r"\bdoctor('?s)? summary\b|\bsummary for (the )?doctor\b|\bvisit summary\b")


_MY_PERIOD = re.compile(r"(?i)\bhow (was|has been) my (week|month)\b|\b(weekly|monthly) (health )?summary\b|"
                        r"\bsummary of my (week|month)\b|\bmera (hafta|mahina) kaisa\b")


_CALL_NEAREST = re.compile(r"(?i)\b(call|phone|dial)\b.{0,15}\b(hospital|ambulance|clinic)\b.{0,15}(near me|nearby|nearest|closest|paas)|\b(call|phone|dial|connect)\b.{0,25}\b(nearest|nearby|closest|near me|paas ka|nazdeeki)\b.{0,20}\b(hospital|ambulance|clinic|doctor)\b|\b(nazdeeki|paas ke) (hospital|aspatal)\b.{0,15}(call|phone|lagao)|नज़दीकी अस्पताल.{0,15}(फ़ोन|कॉल)")


_FILLER = re.compile(r"(?i)^(?:(?:uh+|um+|umm+|hmm+|er+|ah+|so|well|acha|achha|accha|arre|haan toh|ok(?:ay)?(?: so)?)[,.]?\s+)+")


def control_of(text: str) -> str | None:
    """'stop', 'pause', 'explain simply', … when the whole utterance is a conversation control (leading filler
    sounds like "uh", "um", "achha" are ignored)."""
    t = _FILLER.sub("", text.strip()).strip() or text.strip()
    if not t or len(t.split()) > 6:
        return None
    for name, rx in _CONTROL_RX:
        if rx.match(t):
            return name
    return None


def parse(text: str, *, meds: list[dict] | None = None, family: list[dict] | None = None) -> Action | None:
    t = text.strip()
    if not t:
        return None
    if (c := control_of(t)) is not None:
        return Action("control", {"intent": c})
    if _CALL_NEAREST.search(t):
        return Action("call_nearest")
    if _SUMMARY.search(t) and not _QUESTION.search(t):
        return Action("prepare_summary")
    if m := _MY_PERIOD.search(t):
        said = (m.group(0) or "").lower()
        return Action("memory_summary", {"days": 31 if ("month" in said or "mahina" in said) else 7})
    if _READ_LATEST.search(t):
        return Action("read_latest")
    if _DID_I.search(t):
        return Action("dose_status", {"slot": slot_of(t)})
    question = bool(_QUESTION.search(t))
    # switch profile: "switch to Asha", "Asha ka record"
    if family is not None and (m := _SWITCH.search(t)):
        who = (m.group(2) or m.group(4) or "").strip().lower()
        if _SELF.match(who):
            return Action("switch_profile", {"id": None, "name": None})
        best = max(family, key=lambda f: fuzz.ratio(who, f["name"].lower()), default=None)
        if best and fuzz.ratio(who, best["name"].lower()) >= 80:
            return Action("switch_profile", {"id": best["id"], "name": best["name"], "relation": best.get("relation")})
    if _OPEN.search(t) and not question:
        for pat, href in PLACES:
            if re.search(rf"(?i){pat}", t):
                return Action("navigate", {"href": href})
    if question:
        return None  # "is 130/85 normal?" is a question for the assistant, not something to save
    from app.llm.intent import SYMPTOM
    if SYMPTOM.search(t):
        return None  # "BP 150/95 and I have a headache" needs advice: the assistant answers (and can log it)
    if _TOOK.search(t) and meds:
        chosen = match_medicines(t, meds)
        if chosen:
            return Action("log_dose", {"slot": slot_of(t), "medicines": [{"id": m["id"], "name": m["name"]} for m in chosen]},
                          confirm=True)
    vitals = parse_vitals(t)
    if vitals:
        return Action("log_vital", {"readings": vitals}, confirm=True)
    return None


# ---------------------------------------------------------------- what to say
VITAL_SAY = {"bp_sys": ("BP", "mmHg"), "glucose_f": ("fasting sugar", "mg/dL"), "glucose_pp": ("after-meal sugar", "mg/dL"),
             "glucose_r": ("sugar", "mg/dL"), "weight": ("weight", "kg"), "hr": ("pulse", "per minute"),
             "spo2": ("oxygen", "%"), "temp": ("temperature", "°C")}
VITAL_SAY_HI = {"bp_sys": "बीपी", "glucose_f": "खाली पेट शुगर", "glucose_pp": "खाने के बाद की शुगर", "glucose_r": "शुगर",
                "weight": "वज़न", "hr": "नब्ज़", "spo2": "ऑक्सीजन", "temp": "तापमान"}


def describe_readings(readings: list[dict], lang: str) -> str:
    by = {r["type"]: r["value"] for r in readings}
    parts = []
    for k, v in by.items():
        if k == "bp_dia":
            continue
        val = f"{by['bp_sys']:g}/{by['bp_dia']:g}" if k == "bp_sys" and "bp_dia" in by else f"{v:g}"
        if lang == "hi":
            parts.append(f"{VITAL_SAY_HI.get(k, k)} {val}")
        else:
            name, unit = VITAL_SAY.get(k, (k, ""))
            parts.append(f"{name} {val} {unit}".strip())
    return ", ".join(parts)
