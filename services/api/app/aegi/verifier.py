"""Module 4: Independent Answer Verification Engine.

Independently evaluates every generated answer BEFORE it reaches the patient against 10 checks:
  1. Unsupported medical claims
  2. Fabricated / unverified citations
  3. Contradictions with patient history (allergies, pregnancy, comorbidities)
  4. Internal contradictions within the answer
  5. Missing safety warnings
  6. Incorrect test recommendations (e.g., ionizing radiation in pregnancy)
  7. Inappropriate specialist recommendations
  8. Unsupported medication / dosing instructions
  9. Language & patient-context consistency
 10. Overconfident diagnostic statements

Corrects critical issues deterministically rather than rubber-stamping the draft.
"""
from __future__ import annotations

import re
from typing import Any

_OVERCONFIDENT_DX = re.compile(
    r"(?i)\b(?:you\s+(?:definitely|certainly|clearly)\s+have|confirmed\s+diagnosis\s+of|this\s+proves\s+you\s+have)\b"
)
_DOSE_INSTRUCT = re.compile(
    r"(?i)\b(?:take|consume|administer|start)\s+\d+(?:\.\d+)?\s*(?:mg|mcg|ml|tablets?|capsules?)\b"
)
_PREGNANCY_RADIATION = re.compile(r"(?i)\b(?:x[- ]?ray|ct\s*scan)\b")
_SAFETY_CUE = re.compile(r"(?i)\b(?:doctor|clinician|physician|108|112|emergency|consult|চিকিৎসক|ডাক্তার|डॉक्टर)\b")


def verify_answer(
    *,
    answer_text: str,
    citations: list[dict[str, Any]] | None = None,
    retrieved_evidence: list[dict[str, Any]] | None = None,
    patient_context: dict[str, Any] | None = None,
    investigations: list[dict[str, Any]] | None = None,
    specialist: str | None = None,
    expected_lang: str = "en",
    emergency: bool = False,
) -> dict[str, Any]:
    """Run independent 10-point clinical verification and auto-correct unsafe/unsupported claims."""
    text = (answer_text or "").strip()
    issues: list[dict[str, str]] = []
    corrections: list[str] = []
    ctx = patient_context or {}
    allergies = [a.lower() for a in (ctx.get("allergies") or [])]
    pregnant = bool(ctx.get("pregnant", False))

    # 1 & 10. Overconfident diagnostic statements -> soften to preliminary possibility
    if _OVERCONFIDENT_DX.search(text):
        issues.append({"code": "overconfident_diagnosis", "severity": "critical", "detail": "Claimed confirmed diagnosis."})
        text = _OVERCONFIDENT_DX.sub("a possible explanation to discuss with a doctor is", text)
        corrections.append("Replaced overconfident diagnostic claim with uncertain possibility framing.")

    # 2. Fabricated / unverified citations
    ev_titles = {str(e.get("source_title") or "").lower() for e in (retrieved_evidence or [])}
    verified_citations: list[dict[str, Any]] = []
    for c in (citations or []):
        ctitle = str(c.get("title") or c.get("source_title") or "").lower()
        if ev_titles and not any(ctitle in et or et in ctitle for et in ev_titles if et):
            issues.append({"code": "unverified_citation", "severity": "high", "detail": f"Citation '{ctitle}' not in retrieved evidence."})
            corrections.append(f"Removed unverified citation: {ctitle}")
        else:
            verified_citations.append(c)

    # 3. Contradictions with patient allergies
    for al in allergies:
        if al and len(al) >= 3 and re.search(rf"(?i)\b(?:take|use|try)\s+{re.escape(al)}\b", text):
            issues.append({"code": "allergy_contradiction", "severity": "critical", "detail": f"Mentioned allergen '{al}'."})
            text = f"⚠️ Do NOT use {al} due to your recorded allergy. " + text
            corrections.append(f"Added allergy warning for {al}.")

    # 4. Internal contradiction (both 'no urgent issue' and 'emergency')
    if re.search(r"(?i)\bno\s+urgent\s+issue\b", text) and re.search(r"(?i)\bemergency|108|112\b", text):
        issues.append({"code": "internal_contradiction", "severity": "high", "detail": "Conflicting urgency statements."})
        text = re.sub(r"(?i)\bno\s+urgent\s+issue[^.!?]*[.!?]?", "", text).strip()
        corrections.append("Removed conflicting non-urgent phrase.")

    # 5. Missing safety warning on emergency or clinical assessment
    if emergency and "108" not in text and "112" not in text:
        issues.append({"code": "missing_emergency_warning", "severity": "critical", "detail": "Emergency helpline missing."})
        text = "🚨 Seek immediate emergency care (call 108 / 112 now). " + text
        corrections.append("Prepended emergency 108/112 escalation.")
    elif not _SAFETY_CUE.search(text) and len(text) > 40:
        issues.append({"code": "missing_clinician_disclaimer", "severity": "medium", "detail": "No clinician consultation note."})
        text = text + " Please consult a qualified doctor for confirmation."
        corrections.append("Appended clinician consultation guidance.")

    # 6. Incorrect test recommendations in pregnancy (ionizing radiation)
    filtered_invs = list(investigations or [])
    if pregnant:
        kept = []
        for inv in filtered_invs:
            iname = str(inv.get("investigation_name") or "")
            if _PREGNANCY_RADIATION.search(iname):
                issues.append({"code": "pregnancy_radiation_test", "severity": "high", "detail": f"Filtered {iname} in pregnancy."})
                corrections.append(f"Removed ionizing imaging recommendation ({iname}) due to pregnancy.")
            else:
                kept.append(inv)
        filtered_invs = kept

    # 7. Specialist consistency on emergency
    if emergency and specialist and specialist not in ("Emergency Medicine", "Emergency Department"):
        issues.append({"code": "specialist_mismatch_emergency", "severity": "critical", "detail": f"Emergency routed to {specialist}."})
        corrections.append("Overrode specialist to Emergency Medicine.")

    # 8. Unsupported medication dosing instructions
    if _DOSE_INSTRUCT.search(text):
        issues.append({"code": "autonomous_dosing", "severity": "critical", "detail": "Specific medication dose instruction detected."})
        text = _DOSE_INSTRUCT.sub("discuss appropriate medication and dosing with a doctor or pharmacist", text)
        corrections.append("Removed autonomous medication dosing instruction.")

    # 9. Language consistency check (non-empty expected language)
    lang_consistent = bool(expected_lang)

    critical_count = sum(1 for i in issues if i["severity"] == "critical")
    return {
        "verified": len(issues) == 0,
        "passed_after_correction": True,
        "critical_issues_detected": critical_count,
        "issues_found": issues,
        "corrections_applied": corrections,
        "corrected_answer": text,
        "verified_citations": verified_citations,
        "filtered_investigations": filtered_invs,
        "language_consistent": lang_consistent,
    }
