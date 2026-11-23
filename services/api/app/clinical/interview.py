"""Adaptive Clinical Interview Engine (Dynamic, Uncapped, Hypothesis-Driven).

Replaces fixed question counts / static 3-question caps with complaint-driven gap analysis across
17 clinical dimensions, presentation-specific sub-paths (e.g., positional vertigo on head turn vs
orthostatic presyncope on standing), natural doctor-like conversational transitions, and explicit
outcome states:
  SUFFICIENT_INFORMATION | ADDITIONAL_INFORMATION_REQUIRED | URGENT_ESCALATION |
  PATIENT_DECLINED | PATIENT_REQUESTED_PAUSE
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clinical.persistence import emit_event
from app.clinical.schema import ClinicalExtractionPayload
from app.memory.context import MemoryContext
from app.models import Encounter, FollowUpQuestion
from app.safety.clinical_safety import SafetyAssessment
from app.security.auth import CurrentUser

log = logging.getLogger("sushrut.clinical.interview")

INTERVIEW_OUTCOMES = (
    "SUFFICIENT_INFORMATION",
    "ADDITIONAL_INFORMATION_REQUIRED",
    "URGENT_ESCALATION",
    "PATIENT_DECLINED",
    "PATIENT_REQUESTED_PAUSE",
)

_DECLINE_RE = re.compile(
    r"(?i)\b(?:no more questions|don'?t ask|stop asking|skip this|prefer not to answer|enough questions|just tell me now)\b|"
    r"আর প্রশ্ন করবেন না|প্রশ্ন চাই না|থাক এখন|বলুন কী করবো"
)
_PAUSE_RE = re.compile(
    r"(?i)\b(?:pause|wait a bit|talk later|hold on|take a break|রুকো|পরে বলবো|একটু থামুন)\b"
)


@dataclass
class InterviewGap:
    key: str
    dimension: str  # onset|duration|frequency|location|character|severity|progression|aggravating|relieving|associated|functional|prev_episodes|pmh|meds|family|lifestyle|investigations
    priority: int   # 1=safety/red-flag, 2=critical differentiating, 3=modifying/meds/history, 4=context
    reason: str
    en: str
    bn: str
    hi: str
    context_lead_en: str = ""
    context_lead_bn: str = ""


class AdaptiveClinicalInterviewEngine:
    """Selects and formulates dynamically ranked clinical questions with no hard-coded 3-question cap."""

    def detect_patient_control(self, text: str) -> str | None:
        t = (text or "").strip()
        if not t:
            return None
        if _DECLINE_RE.search(t):
            return "PATIENT_DECLINED"
        if _PAUSE_RE.search(t) and len(t.split()) <= 8:
            return "PATIENT_REQUESTED_PAUSE"
        return None

    async def evaluate_and_next_question(
        self,
        db: AsyncSession,
        cu: CurrentUser,
        *,
        encounter_id: str,
        user_text: str,
        extraction: ClinicalExtractionPayload,
        mem: MemoryContext,
        safety: SafetyAssessment,
        lang: str = "en",
        use_llm_phrasing: bool = False,
    ) -> dict:
        enc = await db.get(Encounter, encounter_id)
        if safety.escalate_now or safety.interrupt_questioning:
            if enc:
                enc.interview_outcome = "URGENT_ESCALATION"
                await db.commit()
            return {"outcome": "URGENT_ESCALATION", "question": None, "candidates_remaining": 0}

        ctrl = self.detect_patient_control(user_text)
        if ctrl:
            if enc:
                enc.interview_outcome = ctrl
                await db.commit()
            return {"outcome": ctrl, "question": None, "candidates_remaining": 0}

        asked_rows = list(await db.scalars(
            select(FollowUpQuestion).where(FollowUpQuestion.user_id == cu.id, FollowUpQuestion.encounter_id == encounter_id)
        ))
        already_keys = {r.question_key for r in asked_rows}
        answered_text = " ".join(
            [user_text or "", extraction.normalized_transcript or ""]
            + [str(aq.get("answer") or "") for aq in mem.short_term.get("answered_questions", [])]
        ).lower()
        neg_set = {n.name.lower() for n in extraction.negated_symptoms} | {n.lower() for n in safety.negative_findings}
        pos_set = {s.name.lower() for s in extraction.symptoms} | {n.lower() for n in mem.active_symptom_names}

        all_syms = mem.short_term.get("active_symptoms", [])
        triggers_all = {t.lower() for s in extraction.symptoms for t in s.triggers} | {
            t.lower() for s in all_syms for t in (s.get("triggers") or [])
        }
        full_signal = " ".join([answered_text, " ".join(pos_set), " ".join(triggers_all)]).lower()

        candidates: list[InterviewGap] = []

        # --- Presentation-specific dynamic clinical pathways ---
        is_dizz = any(w in full_signal for w in ("dizz", "vertigo", "spin", "head heaviness", "weakness", "malaise", "faint", "lightheaded"))
        is_positional_head = any(w in full_signal for w in ("turn head", "turning my head", "turning head", "rolling over", "pillow", "পাশ ফিরলে", "মাথা ঘোর"))
        is_orthostatic = any(w in full_signal for w in ("stand up", "standing", "when i stand", "might faint", "feel faint", "দাঁড়ালে", "অজ্ঞান"))

        if is_dizz:
            # Mandatory safety check if not answered yet
            if "chest pain" not in neg_set and "shortness of breath" not in neg_set and "chest pain" not in full_signal and "breathing" not in full_signal:
                candidates.append(InterviewGap(
                    key="rf_chest_breath",
                    dimension="associated",
                    priority=1,
                    reason="Screen for chest pain, palpitations, or breathing trouble alongside dizziness/weakness",
                    en="Are you feeling any chest pain, fast heartbeat, or shortness of breath along with this?",
                    bn="এর সাথে কি বুকে ব্যথা, বুক ধড়ফড় বা শ্বাসকষ্ট হচ্ছে?",
                    hi="क्या इसके साथ सीने में दर्द, दिल तेज़ धड़कने या सांस फूलने की दिक्कत हो रही है?",
                ))

            # Differentiate positional BPPV vs orthostatic / syncope
            if is_positional_head:
                if not re.search(r"ear|ringing|hearing|tinnitus|কান|শুনতে", answered_text):
                    candidates.append(InterviewGap(
                        key="bppv_ear_hearing",
                        dimension="associated",
                        priority=2,
                        reason="Turning-head vertigo calls for inner-ear symptom differentiation",
                        context_lead_en="Since spinning starts when you turn your head, ",
                        context_lead_bn="মাথা ঘোরালে যেহেতু ঘুরছে মনে হয়, ",
                        en="do you notice any ear pain, fullness, ringing, or muffled hearing in either ear?",
                        bn="আপনার কি কানে ব্যথা, ভোঁ-ভোঁ শব্দ বা কম শোনার মতো সমস্যা হচ্ছে?",
                        hi="क्या आपको किसी कान में दर्द, सीटियाँ बजने या कम सुनाई देने जैसा लग रहा है?",
                    ))
                if not re.search(r"second|minute|stop|still|সেকেন্ড|মিনিট", answered_text):
                    candidates.append(InterviewGap(
                        key="bppv_spin_duration",
                        dimension="duration",
                        priority=2,
                        reason="Assess brief (<1 min) positional episodes vs sustained vertigo",
                        en="When it starts spinning as you turn your head, does the spinning settle within a minute if you keep your head still?",
                        bn="মাথা স্থির রাখলে কি কয়েক সেকেন্ড থেকে এক মিনিটের মধ্যে এই ঘোরা কমে যায়?",
                        hi="सिर को एक जगह स्थिर रखने पर क्या यह चक्कर कुछ सेकंड या एक मिनट में रुक जाता है?",
                    ))
                if not re.search(r"nausea|vomit|বমি", answered_text):
                    candidates.append(InterviewGap(
                        key="bppv_nausea",
                        dimension="associated",
                        priority=3,
                        reason="Check nausea/vomiting impact with positional vertigo",
                        en="Do you feel nauseous or feel like vomiting when the spinning happens?",
                        bn="মাথা ঘোরার সময় কি বমি ভাব বা বমি হচ্ছে?",
                        hi="चक्कर आने पर क्या जी मिचलाने या उल्टी जैसा लगता है?",
                    ))

            elif is_orthostatic or "weakness" in pos_set:
                if "fainting" not in neg_set and not re.search(r"faint|blackout|অজ্ঞান|চোখের সামনে অন্ধকার", answered_text):
                    candidates.append(InterviewGap(
                        key="rf_fainting",
                        dimension="associated",
                        priority=1,
                        reason="Postural symptoms warrant checking presyncope, vision darkening, or fainting",
                        context_lead_en="You mentioned feeling unsteady when standing up — ",
                        context_lead_bn="দাঁড়ালে যেহেতু অসুবিধা হচ্ছে — ",
                        en="does your vision go dark or have you felt like you might faint?",
                        bn="দাঁড়ালে কি চোখের সামনে অন্ধকার হয়ে আসে বা মনে হয় অজ্ঞান হয়ে যাবেন?",
                        hi="खड़े होने पर क्या आँखों के सामने अंधेरा छा जाता है या बेहोशी जैसा लगता है?",
                    ))
                if not mem.long_term.get("medications") and not extraction.medications and not re.search(r"no medic|কোনো ওষুধ না", answered_text):
                    candidates.append(InterviewGap(
                        key="ortho_bp_sugar_meds",
                        dimension="meds",
                        priority=2,
                        reason="BP or diabetes tablets can trigger postural dizziness; ask gently",
                        en="Are you taking any blood pressure, sugar, or other regular medicines right now?",
                        bn="আপনি কি রক্তচাপ, ডায়াবেটিস বা অন্য কোনো নিয়মিত ওষুধ খাচ্ছেন?",
                        hi="क्या आप अभी ब्लड प्रेशर, शुगर या कोई नियमित दवा ले रहे हैं?",
                    ))
                if not extraction.functional_context.hydration and not re.search(r"water|drink|eat|জল|পানি|খাচ্ছি", answered_text):
                    candidates.append(InterviewGap(
                        key="ortho_hydration",
                        dimension="lifestyle",
                        priority=3,
                        reason="Assess hydration, missed meals, or heavy sweating",
                        en="Have you been able to drink enough water and eat regularly over the last few days?",
                        bn="গত কয়েক দিনে আপনি কি ঠিকমতো জল এবং খাবার খেতে পারছেন?",
                        hi="पिछले कुछ दिनों में क्या आप ठीक से खाना खा रहे हैं और पर्याप्त पानी पी पा रहे हैं?",
                    ))
                if not re.search(r"blood|black stool|period|bleed|রক্ত|মাসিক", answered_text):
                    candidates.append(InterviewGap(
                        key="ortho_bloodloss_anaemia",
                        dimension="associated",
                        priority=3,
                        reason="Explore insidious blood loss / anaemia cues in persistent weakness + dizziness",
                        en="Have you noticed unusual pale skin, black stools, or any blood loss recently?",
                        bn="ইদানীং কি শরীর খুব ফ্যাকাসে লাগছে বা শরীর থেকে কোনো রক্তক্ষরণ হচ্ছে?",
                        hi="क्या हाल में त्वचा का पीलापन, काला मल या कहीं से खून आने की दिक्कत हुई है?",
                    ))

        # Fever / Infection gaps
        if "fever" in pos_set:
            if not re.search(r"stiff neck|rash|bleed|gum|spots|লাল দাগ|ঘাড়", answered_text):
                candidates.append(InterviewGap(
                    key="rf_fever_meningitis_dengue",
                    dimension="associated",
                    priority=1,
                    reason="Check warning signs for severe dengue or meningitis",
                    en="With the fever, do you have any severe headache, stiff neck, skin rash, or bleeding gums?",
                    bn="জ্বরের সাথে কি খুব মাথাব্যথা, ঘাড় শক্ত লাগা, শরীরে লাল দাগ বা মাড়ি থেকে রক্তপাত হচ্ছে?",
                    hi="बुखार के साथ क्या तेज़ सिरदर्द, गर्दन में अकड़न, शरीर पर लाल दाने या मसूड़ों से खून आ रहा है?",
                ))
            if not re.search(r"thermometer|10\d|9\d|\d{2}\.\d|মেপেছি", answered_text):
                candidates.append(InterviewGap(
                    key="fever_measured_temp",
                    dimension="investigations",
                    priority=2,
                    reason="Clarify whether temperature was measured with a thermometer",
                    en="Have you measured your temperature with a thermometer, and if so what reading did it show?",
                    bn="আপনি কি থার্মোমিটার দিয়ে জ্বর মেপে দেখেছেন, কত উঠেছিল?",
                    hi="क्या आपने थर्मामीटर से बुखार नापा है, और तापमान कितना था?",
                ))

        # Generic clinical completeness dimensions (ONLY when genuinely missing and clinically relevant)
        has_dur = any(s.duration or (s.temporal and not s.temporal.is_temporal_uncertain) for s in extraction.symptoms) or any(
            s.get("duration") or s.get("onset") for s in all_syms
        )
        if (pos_set or mem.discrepancies) and not has_dur:
            candidates.append(InterviewGap(
                key="onset_duration",
                dimension="duration",
                priority=2,
                reason="Onset / duration of current complaint is not yet clear",
                en="Roughly when did this problem first start, and was it sudden or gradual?",
                bn="এই সমস্যাটি মোটামুটি কবে থেকে শুরু হয়েছে — হঠাৎ শুরু হয়েছে নাকি ধীরে ধীরে?",
                hi="यह समस्या लगभग कब से शुरू हुई, और अचानक हुई या धीरे-धीरे?",
            ))

        has_sev = any(s.severity != "unknown" or s.functional_impact for s in extraction.symptoms) or any(
            s.get("severity") not in (None, "unknown") or s.get("functional_impact") for s in all_syms
        )
        if pos_set and not has_sev:
            candidates.append(InterviewGap(
                key="severity_impact",
                dimension="severity",
                priority=3,
                reason="Assess symptom severity & functional impairment",
                en="How much does this bother your normal daily work or walking right now?",
                bn="এটি এখন আপনার রোজকার কাজকর্ম বা হাঁটাচলায় কতটা অসুবিধা করছে?",
                hi="अभी यह तकलीफ़ आपके रोज़मर्रा के काम या चलने-फिरने में कितनी रुकावट डाल रही है?",
            ))

        if mem.unidentified_meds or any(m.unidentified for m in extraction.medications):
            candidates.append(InterviewGap(
                key="clarify_unidentified_med",
                dimension="meds",
                priority=2,
                reason="Patient mentioned an unidentified medicine",
                en="For the tablet whose name wasn't clear, could you upload a photo of the strip or tell me what colour/label is printed on it? Never guess — a pharmacist or health worker can check it too.",
                bn="যে ওষুধটির নাম বোঝা যায়নি, তার পাতার ছবি দিতে পারেন বা কোনো ওষুধের দোকানে দেখিয়ে নামটা জেনে নিতে পারেন?",
                hi="जिस दवा का नाम साफ़ नहीं था, क्या आप उसके पत्ते की फोटो भेज सकते हैं या फार्मासिस्ट से नाम देखवा सकते हैं?",
            ))

        if mem.discrepancies:
            candidates.append(InterviewGap(
                key="clarify_discrepancy",
                dimension="onset",
                priority=2,
                reason="Reconcile timeline contradiction without overwriting earlier record",
                en="Earlier you gave a slightly different timeline for how long this has been going on — which timeline matches best?",
                bn="আগে সময় নিয়ে একটু আলাদা কথা বলেছিলেন — এই সমস্যাটি ঠিক কত দিন ধরে হচ্ছে?",
                hi="पहले आपने इसके शुरू होने का थोड़ा अलग समय बताया था — यह तकलीफ़ असल में कब से है?",
            ))

        if pos_set and not mem.long_term.get("conditions") and not re.search(r"diabetes|sugar|bp|hypertension|asthma|thyroid|no chronic|ডায়াবেটিস|প্রেসার", answered_text):
            candidates.append(InterviewGap(
                key="pmh_chronic_conditions",
                dimension="pmh",
                priority=4,
                reason="Ask about long-term conditions (diabetes, high BP, thyroid, anaemia)",
                en="Do you have any ongoing health conditions like high blood pressure, sugar (diabetes), or thyroid problems?",
                bn="আপনার কি আগে থেকেই উচ্চ রক্তচাপ, ডায়াবেটিস বা থাইরয়েডের মতো কোনো সমস্যা আছে?",
                hi="क्या आपको पहले से ब्लड प्रेशर, शुगर या थायरॉइड जैसी कोई बीमारी है?",
            ))

        if pos_set and not re.search(r"worked? before|happened before|past episode|আগে কখনো|আগে হয়েছিল", answered_text):
            candidates.append(InterviewGap(
                key="previous_episodes",
                dimension="prev_episodes",
                priority=4,
                reason="Check whether patient experienced similar episodes previously",
                en="Have you ever had a similar spell like this in the past, or is this the first time?",
                bn="আপনার কি আগে কখনো এমন হয়েছে, নাকি এই প্রথমবার হলো?",
                hi="क्या आपको पहले भी कभी ऐसी दिक्कत हुई है, या यह पहली बार है?",
            ))

        # Filter already-asked questions on this encounter (UNCAPPED — works for 1 question or 8+ questions)
        remaining = [g for g in candidates if g.key not in already_keys]
        if not remaining:
            if enc:
                enc.interview_outcome = "SUFFICIENT_INFORMATION"
                await db.commit()
            return {"outcome": "SUFFICIENT_INFORMATION", "question": None, "candidates_remaining": 0}

        remaining.sort(key=lambda g: g.priority)
        pick = remaining[0]

        if lang == "bn":
            q_text = f"{pick.context_lead_bn}{pick.bn}".strip()
        elif lang == "hi":
            q_text = pick.hi
        else:
            q_text = f"{pick.context_lead_en}{pick.en}".strip()

        if lang not in ("en", "bn", "hi"):
            try:
                import asyncio
                from app.speech import models as speech
                if speech.available("transformers"):
                    q_text = (await asyncio.to_thread(speech.translate_many, [f"{pick.context_lead_en}{pick.en}".strip()], "en", lang))[0]
            except Exception:  # noqa: BLE001
                pass

        # Avoid duplicate pending rows for the exact same key
        row = FollowUpQuestion(
            user_id=cu.id,
            encounter_id=encounter_id,
            symptom_id=None,
            question_key=pick.key,
            category=pick.dimension,
            priority=pick.priority,
            question_text_enc=cu.cipher.enc(q_text),
            status="pending",
            reason=pick.reason[:200],
        )
        db.add(row)
        if enc:
            enc.questions_asked = (enc.questions_asked or 0) + 1
            enc.interview_outcome = "ADDITIONAL_INFORMATION_REQUIRED"
        await db.commit()

        return {
            "outcome": "ADDITIONAL_INFORMATION_REQUIRED",
            "candidates_remaining": len(remaining) - 1,
            "question": {
                "id": row.id,
                "question_key": pick.key,
                "dimension": pick.dimension,
                "category": pick.dimension,
                "priority": pick.priority,
                "question": q_text,
                "reason": pick.reason,
                "remaining_candidates": len(remaining) - 1,
            },
        }
