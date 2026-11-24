"""Adaptive symptom triage: Bayesian updating + expected-information-gain question selection.

The model (MedGemma) is called once to propose candidate conditions and a likelihood table
P(finding present | condition) for discriminating findings, personalised to age/sex/region/season.
Everything after that — choosing the next question, updating beliefs, stopping — is deterministic
and runs instantly. Red-flag rules are re-checked on every answer.
"""
from __future__ import annotations

import math
import re

from app.llm.orchestrator import SPECIALISTS
from app.triage.urgency import condition_urgency

MAX_QUESTIONS = 12
STOP_TOP = 0.60
STOP_MARGIN = 0.25
ANSWER_LIKELIHOOD_FLOOR = 0.02  # avoid zeroing a condition on one answer

# The model names conditions per finding (common_in / rare_in) instead of writing numeric tables: numeric tables
# from the local 4B model were mostly position templates (0.4, 0.3, 0.2, ... in list order), and its urgency labels
# were always the first enum value. Urgency is therefore assigned deterministically (app/triage/urgency.py).
TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "conditions": {"type": "array", "minItems": 3, "maxItems": 6, "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "prior": {"type": "number"},
            "specialist": {"type": "string", "enum": SPECIALISTS}},
            "required": ["name", "prior", "specialist"]}},
        "findings": {"type": "array", "minItems": 4, "maxItems": 10, "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "question": {"type": "string"},
            "red_flag": {"type": "boolean"},
            "topic": {"type": "string", "enum": ["general", "pregnancy", "menstrual", "sexual", "child", "elderly"]},
            "common_in": {"type": "array", "maxItems": 6, "items": {"type": "string"}},
            "rare_in": {"type": "array", "maxItems": 6, "items": {"type": "string"}}},
            "required": ["id", "question", "common_in", "rare_in", "red_flag", "topic"]}},
    },
    "required": ["conditions", "findings"],
}
LK_COMMON, LK_UNSAID, LK_RARE = 0.75, 0.25, 0.05


# States with high malaria burden per NCVBDC reporting (state codes as stored in the profile).
MALARIA_HIGH_BURDEN = {"OR", "OD", "CG", "CT", "JH", "MZ", "ML", "TR", "MP", "AR", "WB"}
MONSOON_MONTHS = {6, 7, 8, 9, 10, 11}
_FEVER = re.compile(r"(?i)fever|bukhar|bukhaar|बुखार|ज्वर|জ্বর|காய்ச்சல்|జ్వరం|ताप|તાવ|ಜ್ವರ|പനി|ଜ୍ୱର|temperature")
_CHILLS = re.compile(r"(?i)chill|rigor|shiver|kaanp|कंपकंपी|ठंड|alternate day|every other day|tertian|din chhod")
_COUGH = re.compile(r"(?i)cough|khansi|खांसी|খাঁসি|কাশি|இருமல்|దగ్గు|ખાંસી|ಕೆಮ್ಮು|ചുമ|କାଶ")
_WEEKS = re.compile(r"(?i)(\d+)\s*(week|weeks|wk|hafte|हफ्ते|सप्ताह)|(\d+)\s*(month|months|mahine|महीने)|"
                    r"(1[4-9]|[2-9]\d)\s*(days?|din|दिन)")


def must_consider(complaint: str, state_code: str | None, month: int) -> list[str]:
    """Deterministic public-health rules: conditions that must be on the list for this presentation in India."""
    out: list[str] = []
    fever = bool(_FEVER.search(complaint))
    if fever and (month in MONSOON_MONTHS or (state_code or "").upper() in MALARIA_HIGH_BURDEN):
        out += ["Dengue", "Malaria", "Typhoid (enteric fever)"]
    if fever and _CHILLS.search(complaint) and "Malaria" not in out:
        out.append("Malaria")
    if _COUGH.search(complaint) and _WEEKS.search(complaint):
        out.append("Pulmonary tuberculosis")  # NTEP: cough >= 2 weeks = presumptive TB, needs sputum testing
    return out


def build_prompt(complaint: str, profile: str, lang_name: str, season_hint: str,
                 required: list[str] | None = None) -> str:
    return f"""A person in India describes: "{complaint}"
Patient context: {profile}. {season_hint}

Task (for a symptom-checker that asks yes/no questions):
1. List 3–6 candidate conditions that could explain this, including common Indian causes where relevant,
   with a prior probability for each (priors should sum to about 1) given the context.
   Always include at least one serious condition that must not be missed if plausible.{_required_line(required)}
2. Do NOT ask about anything the person already said (it is known to be present).
   List 6–10 yes/no findings (symptoms/signs a layperson can answer) that best DISTINGUISH these conditions.
   Include the danger signs a doctor would check for this complaint, with `red_flag` true.
   For each finding, `common_in` = the conditions (exact names from your list) in which this finding is usually
   present, and `rare_in` = the conditions in which it is usually absent. A finding should be common in some
   conditions and rare in others — that is what makes it useful.
   Write each `question` in {lang_name}, short and simple, answerable with yes / no / not sure.
   Set `topic` (e.g. "pregnancy" for pregnancy questions).
Use short ids (f1, f2, …) and compact JSON without extra whitespace.
Respond with JSON only."""


def _required_line(required: list[str] | None) -> str:
    if not required:
        return ""
    return ("\n   You MUST include these conditions in the list (Indian public-health rules for this presentation): "
            + ", ".join(required) + ".")


def missing_required(llm: dict, required: list[str]) -> list[str]:
    names = " | ".join(c.get("name", "").lower() for c in llm.get("conditions", []))
    return [r for r in required if r.split(" (")[0].lower().split()[-1] not in names]


def normalise(ps: list[float]) -> list[float]:
    s = sum(ps)
    return [p / s for p in ps] if s > 0 else [1 / len(ps)] * len(ps)


def entropy(ps: list[float]) -> float:
    return -sum(p * math.log2(p) for p in ps if p > 0)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _match(name: str, conds: list[str]) -> int | None:
    """Index of the condition the model meant (exact, then containment, then shared words)."""
    n = _norm(name)
    if not n:
        return None
    normed = [_norm(c) for c in conds]
    if n in normed:
        return normed.index(n)
    for i, c in enumerate(normed):
        if n in c or c in n:
            return i
    words = set(n.split()) - {"acute", "chronic", "fever", "infection", "disease", "the"}
    best = max(range(len(conds)), key=lambda i: len(words & set(normed[i].split())))
    return best if words & set(normed[best].split()) else None


def likelihood_row(f: dict, conds: list[str]) -> list[float] | None:
    """P(finding present | condition) for each condition, from named lists (or a numeric row from older plans)."""
    if "common_in" in f or "rare_in" in f:
        row = [LK_UNSAID] * len(conds)
        for nm in f.get("rare_in") or []:
            if (i := _match(nm, conds)) is not None:
                row[i] = LK_RARE
        for nm in f.get("common_in") or []:  # 'common' wins if the model put a condition in both lists
            if (i := _match(nm, conds)) is not None:
                row[i] = LK_COMMON
        return row if len(set(row)) > 1 else None  # does not distinguish anything: drop the question
    lk = [min(0.99, max(0.01, float(x))) for x in (f.get("likelihoods") or [])][:len(conds)]
    return lk if len(lk) == len(conds) else None


def _order_correlation(row: list[float]) -> float:
    """Spearman correlation between a likelihood row and the column order (ties broken by position)."""
    n = len(row)
    rank = [0] * n
    for k, i in enumerate(sorted(range(n), key=lambda i: row[i])):
        rank[i] = k
    m = (n - 1) / 2
    den = sum((i - m) ** 2 for i in range(n))
    return sum((rank[i] - m) * (i - m) for i in range(n)) / den if den else 0.0


def is_template_table(rows: list[list[float]]) -> bool:
    """True when most numeric rows just follow the order the conditions were listed in (0.4, 0.3, 0.2, ...):
    such a table encodes list position, not medicine. Needs >= 4 conditions to tell apart from chance."""
    rows = [r for r in rows if len(r) >= 4 and len(set(r)) > 1]
    return len(rows) >= 2 and sum(abs(_order_correlation(r)) >= 0.9 for r in rows) / len(rows) >= 0.5


def _same_question(q: str, findings: list[dict]) -> bool:
    from rapidfuzz import fuzz

    norm = lambda s: re.sub(r"[\W_]+", " ", s or "").strip().lower()  # noqa: E731
    return any(fuzz.ratio(norm(q), norm(x["question"])) >= 90 for x in findings)


def init_state(llm: dict, *, sex: str | None, age: float | None, pregnant: bool = False) -> dict:
    conds = llm.get("conditions", [])[:6]
    n = len(conds)
    if n < 2:
        raise ValueError("Triage needs at least two candidate conditions")
    names = [c["name"] for c in conds]
    high_risk = pregnant or (age is not None and (age < 5 or age >= 65))
    numeric = [likelihood_row(f, names) for f in llm.get("findings", [])[:10]
               if not ("common_in" in f or "rare_in" in f)]
    template = is_template_table([r for r in numeric if r])
    findings = []
    for f in llm.get("findings", [])[:10]:
        lk = likelihood_row(f, names)
        if lk is None:
            continue
        if template and "likelihoods" in f:
            if not f.get("red_flag"):
                continue  # meaningless numbers: the answer would move beliefs at random
            lk = [0.5] * n  # still ask danger signs (a 'yes' forces "see a doctor today"), but don't reweight
        topic = f.get("topic", "general")
        # Personalisation filters
        if topic in ("pregnancy", "menstrual") and (sex == "male" or (age is not None and (age < 10 or age > 55))):
            continue
        if topic == "child" and age is not None and age >= 18:
            continue
        fid = re.sub(r"\W+", "_", str(f["id"]))[:40]
        if _same_question(f["question"], findings) or any(x["id"] == fid for x in findings):
            continue  # small models repeat a question in the plan: it would be asked twice
        findings.append({"id": fid, "question": f["question"], "red_flag": bool(f.get("red_flag")), "likelihoods": lk})
    if len(findings) < 2:
        raise ValueError("Too few usable questions")
    return {
        "conditions": [{"name": c["name"], "urgency": condition_urgency(c["name"], high_risk=high_risk),
                        "specialist": c.get("specialist") if c.get("specialist") in SPECIALISTS else "General Physician"}
                       for c in conds],
        "posterior": normalise([max(0.01, float(c.get("prior", 1 / n))) for c in conds]),
        "findings": findings,
        "answers": {},
        "asked": 0,
        "high_risk": high_risk,
        "plan_quality": "template_table" if template else "ok",
    }


def expected_information_gain(post: list[float], lk: list[float]) -> float:
    p_yes = sum(p * l for p, l in zip(post, lk))
    p_no = 1 - p_yes
    h = entropy(post)
    h_yes = entropy(normalise([p * l for p, l in zip(post, lk)])) if p_yes > 0 else 0
    h_no = entropy(normalise([p * (1 - l) for p, l in zip(post, lk)])) if p_no > 0 else 0
    return h - (p_yes * h_yes + p_no * h_no)


def next_question(state: dict) -> dict | None:
    pending = [f for f in state["findings"] if f["id"] not in state["answers"]]
    if not pending:
        return None
    red = [f for f in pending if f["red_flag"]]
    if red:  # danger signs are always asked first
        return red[0]
    return max(pending, key=lambda f: expected_information_gain(state["posterior"], f["likelihoods"]))


def update(state: dict, finding_id: str, answer: str) -> dict:
    f = next((x for x in state["findings"] if x["id"] == finding_id), None)
    if f is None:
        raise KeyError(finding_id)
    state["answers"][finding_id] = answer
    state["asked"] += 1
    if answer in ("yes", "no"):
        lk = f["likelihoods"] if answer == "yes" else [1 - l for l in f["likelihoods"]]
        state["posterior"] = normalise([p * max(ANSWER_LIKELIHOOD_FLOOR, l) for p, l in zip(state["posterior"], lk)])
    return state


def should_stop(state: dict) -> tuple[bool, str]:
    post = sorted(state["posterior"], reverse=True)
    if post[0] >= STOP_TOP and (len(post) < 2 or post[0] - post[1] >= STOP_MARGIN):
        return True, "confident"
    if state["asked"] >= MAX_QUESTIONS:
        return True, "max_questions"
    if next_question(state) is None:
        return True, "no_more_questions"
    return False, ""


def red_flag_answered(state: dict) -> list[str]:
    return [f["question"] for f in state["findings"] if f["red_flag"] and state["answers"].get(f["id"]) == "yes"]


def band(p: float) -> str:
    return "high" if p >= 0.5 else "moderate" if p >= 0.2 else "low"


def summary(state: dict) -> list[dict]:
    order = sorted(range(len(state["conditions"])), key=lambda i: -state["posterior"][i])
    out = []
    for i in order:
        c = state["conditions"][i]
        support = [f["question"] for f in state["findings"]
                   if state["answers"].get(f["id"]) == "yes" and f["likelihoods"][i] >= 0.6]
        against = [f["question"] for f in state["findings"]
                   if state["answers"].get(f["id"]) == "no" and f["likelihoods"][i] >= 0.6]
        out.append({"name": c["name"], "probability": round(state["posterior"][i], 3),
                    "likelihood": band(state["posterior"][i]), "urgency": c["urgency"],
                    "specialist": c["specialist"], "supporting": support, "against": against})
    return out
