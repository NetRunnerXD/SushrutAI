"""ConversationContextManager: maintains multi-turn consultation memory and resolves short replies."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.clinical.extract import extract_deterministic

_YES_RE = re.compile(r"(?i)^\s*(?:yes|yeah|yep|haan|haa|han|ji|হ্যাঁ|হাঁ|আছে|হচ্ছে|হয়|हाँ|हां|जी)\b")
_NO_RE = re.compile(r"(?i)^\s*(?:no|nope|nah|nahi|nahin|না|নেই|নাই|হয়নি|नहीं|ना)\b")
_DURATION_REPLY_RE = re.compile(
    r"(?i)(?:\d+\s*(?:days?|weeks?|months?|years?|din|দিন|সপ্তাহ|মাস|বছর|दिन|सप्ताह)|"
    r"এক\s*সপ্তাহ|দুই\s*দিন|তিন\s*দিন|গতকাল|সকাল\s*থেকে|অনেকদিন|yesterday|since morning|last week|ago)"
)
_TIME_OF_DAY_RE = re.compile(r"(?i)(?:রাতে|সকালে|দুপুরে|আগের\s*মতোই|at night|in the morning|same as before)")


@dataclass
class ConsultationContext:
    chief_complaint: str
    user_turns: list[str]
    assistant_turns: list[str]
    symptoms: list[str] = field(default_factory=list)
    negatives: list[str] = field(default_factory=list)
    durations: list[str] = field(default_factory=list)
    aggravating: list[str] = field(default_factory=list)
    answered_topics: set[str] = field(default_factory=set)
    resolved_short_replies: list[str] = field(default_factory=list)
    merged_clinical_text: str = ""
    followup_turn_count: int = 0

    def prompt_block(self) -> str:
        lines = [f"- Chief Complaint: {self.chief_complaint}"]
        if self.symptoms:
            lines.append(f"- Reported Symptoms: {', '.join(self.symptoms)}")
        if self.durations:
            lines.append(f"- Duration/Onset: {', '.join(self.durations)}")
        if self.aggravating:
            lines.append(f"- Triggers/Aggravating Factors: {', '.join(self.aggravating)}")
        if self.negatives:
            lines.append(f"- Denied / Absent Symptoms: {', '.join(self.negatives)}")
        if self.resolved_short_replies:
            lines.append(f"- Resolved Follow-ups: {'; '.join(self.resolved_short_replies)}")
        if self.answered_topics:
            lines.append(f"- Topics Already Covered (DO NOT REPEAT): {', '.join(sorted(self.answered_topics))}")
        return "CONSULTATION MEMORY (combine ALL turns):\n" + "\n".join(lines)


def _infer_question_topic(q: str) -> str | None:
    low = (q or "").lower()
    if re.search(r"where in your|where is the pain|which joint|কোন জায়গায়|किस हिस्से में", low):
        return "location"
    if re.search(r"happening now|happening right now|right now|at the moment|এখনও|এখন কি|এখনই|अभी हो रहा|अभी आराम", low):
        return "current_now"
    if re.search(r"sweat|breath|faint|dizz|chest|blurred vision|numbness|weakness on one side|wheezing|ঘাম|শ্বাস|অজ্ঞান|মাথা ঘোর|ঝাপসা|অবশ|সাঁই সাঁই|पसीना|सांस|धुंधला|सुन्नपन|घरघराहट", low):
        return "warning_signs"
    if re.search(r"also have|along with|loose motions|burning urination|body ache|skin rash|পায়খানা|প্রস্রাবে জ্বালা|গায়ে ব্যথা|র‍্যাশ|उल्टी|दस्त|बदन दर्द|दाने", low):
        return "associated"
    if re.search(r"when did|how long|how many days|kobe|kotodin|কবে থেকে|কতদিন|কয়দিন|कब से|कितने दिन|कितने समय", low):
        return "duration"
    if re.search(r"walk|stairs|stand|trigger|worse|better|হাঁটলে|দাঁড়ালে|বাড়ে|কমে|चलने|बढ़ता", low):
        return "aggravating"
    if re.search(r"medicine|tablet|diabetes|bp|blood pressure|history|ওষুধ|ডায়াবেটিস|প্রেসার|दवा|शुगर|ब्लड प्रेशर", low):
        return "history_meds"
    if re.search(r"fever|জ্বর|temperature|बुखार", low):
        return "fever"
    return None


def build_consultation_context(
    history: list[dict] | None,
    current_text: str,
    *,
    lang: str = "en",
) -> ConsultationContext:
    """Reconstruct cumulative clinical context across multi-turn voice/text history and resolve short replies."""
    hist = history or []
    user_turns: list[str] = []
    assistant_turns: list[str] = []
    resolved: list[str] = []
    answered_topics: set[str] = set()
    durations: list[str] = []
    aggravating: list[str] = []
    extra_fragments: list[str] = []

    last_assistant_q = ""
    for item in hist:
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        if role == "assistant":
            assistant_turns.append(content)
            last_assistant_q = content
            if topic := _infer_question_topic(content):
                # Mark topic as asked; if user replies next, it's answered
                pass
        elif role == "user":
            user_turns.append(content)
            if last_assistant_q:
                topic = _infer_question_topic(last_assistant_q)
                if topic:
                    answered_topics.add(topic)
                if len(content.split()) <= 8 and (_YES_RE.match(content) or _NO_RE.match(content) or _DURATION_REPLY_RE.search(content) or _TIME_OF_DAY_RE.search(content)):
                    resolved.append(f"Q({topic or 'followup'}): '{last_assistant_q[:80]}' -> Patient answered: '{content}'")
                    extra_fragments.append(f"{topic or 'detail'}: {content}")

    cur = (current_text or "").strip()
    user_turns.append(cur)
    if last_assistant_q:
        topic = _infer_question_topic(last_assistant_q)
        if topic:
            answered_topics.add(topic)
        if len(cur.split()) <= 8 and (_YES_RE.match(cur) or _NO_RE.match(cur) or _DURATION_REPLY_RE.search(cur) or _TIME_OF_DAY_RE.search(cur)):
            resolved.append(f"Q({topic or 'followup'}): '{last_assistant_q[:80]}' -> Patient answered: '{cur}'")
            extra_fragments.append(f"{topic or 'detail'}: {cur}")

    combined = " . ".join([*user_turns, *extra_fragments]).strip()
    det = extract_deterministic(combined, patient_id="ctx", encounter_id=None, language=lang, raw_transcript=combined)
    syms = list(dict.fromkeys(s.name for s in det.symptoms))
    negs = list(dict.fromkeys(n.name for n in det.negated_symptoms))

    for u in user_turns:
        if _DURATION_REPLY_RE.search(u):
            durations.append(u)
            answered_topics.add("duration")
        if re.search(r"(?i)walk|walking|stairs|standing|exertion|at rest|হাঁটলে|সিঁড়ি|দাঁড়ালে|চললে", u):
            aggravating.append(u)
            answered_topics.add("aggravating")
        if re.search(r"(?i)fever|জ্বর|बुखार", u):
            answered_topics.add("fever")
        if re.search(r"(?i)no (?:chest pain|sweating|fever)|জ্বর নেই|ঘাম নেই|শ্বাসকষ্ট নেই", u):
            answered_topics.add("warning_signs")

    return ConsultationContext(
        chief_complaint=user_turns[0] if user_turns else cur,
        user_turns=user_turns,
        assistant_turns=assistant_turns,
        symptoms=syms,
        negatives=negs,
        durations=list(dict.fromkeys(durations)),
        aggravating=list(dict.fromkeys(aggravating)),
        answered_topics=answered_topics,
        resolved_short_replies=resolved,
        merged_clinical_text=combined,
        followup_turn_count=max(0, len(user_turns) - 1),
    )
