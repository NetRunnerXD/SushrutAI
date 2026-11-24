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
    context_lead_hi: str = ""


def _infer_answer_type(q_text: str, dimension: str) -> str:
    if dimension == "duration" and re.search(r"(?i)how long|how many days|since when|कब से|कितने दिन|কত দিন", q_text):
        return "duration"
    if dimension == "severity" and re.search(r"(?i)mild|moderate|severe|how bad|कितना|कैसा|কতটা", q_text):
        return "severity"
    if re.search(r"(?i)^(?:are|is|do|does|did|have|has|was|were|will|would|could|can|with\b|when it starts|since |क्या|আপনার কি|এর সাথে কি)", q_text.strip()):
        return "yes_no"
    return "free_text"


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
        is_symptom_turn: bool = False,
        allow_emergency_interrupt: bool = True,
    ) -> dict:
        enc = await db.get(Encounter, encounter_id)
        if allow_emergency_interrupt and (safety.escalate_now or safety.interrupt_questioning):
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

        # Respiratory / Cough / Shortness of breath
        is_resp = any(s in pos_set for s in ("cough", "shortness of breath", "sore throat")) or any(
            w in full_signal for w in ("cough", "phlegm", "wheez", "sputum", "kashi", "khansi", "কাশি", "खांसी")
        )
        if is_resp:
            if not re.search(r"blood|sputum|phlegm|dry|wet|mucus|কফ|রক্ত|बलगम", answered_text):
                candidates.append(InterviewGap(
                    key="resp_sputum_blood",
                    dimension="character",
                    priority=1,
                    reason="Differentiate dry vs productive cough and screen for blood-streaked sputum",
                    en="Is the cough dry or bringing up phlegm, and have you seen any blood in your sputum?",
                    bn="কাশি কি শুকনো নাকি কফ উঠছে, এবং কফের সাথে কি কখনো রক্ত দেখা গেছে?",
                    hi="क्या खांसी सूखी है या बलगम आ रहा है, और क्या बलगम में कभी खून दिखा है?",
                ))
            if not re.search(r"wheez|whistl|night|dust|allergy|শ্বাস|হাঁপানি|सीटी", answered_text):
                candidates.append(InterviewGap(
                    key="resp_wheeze_triggers",
                    dimension="aggravating",
                    priority=2,
                    reason="Screen for asthma/bronchitis patterns (wheezing, night waking, dust trigger)",
                    en="Do you hear any whistling or wheezing when breathing, or does it worsen at night or with dust?",
                    bn="শ্বাস নেওয়ার সময় কি শোঁ শোঁ শব্দ হয়, বা রাতে বা ধুলোয় কাশি বাড়ে?",
                    hi="क्या सांस लेते समय सीटी जैसी आवाज़ आती है, या रात में या धूल से खांसी बढ़ती है?",
                ))

        # Headache / Migraine
        is_headache = any(s in pos_set for s in ("headache", "head heaviness")) or any(
            w in full_signal for w in ("headache", "migraine", "sir dard", "matha betha", "মাথাব্যথা", "সর্দি", "सिरदर्द")
        )
        if is_headache:
            if not re.search(r"stiff neck|fever|blur|double vision|light|vomit|ঘাড়|আলো|চোখ|रोशनी", answered_text):
                candidates.append(InterviewGap(
                    key="rf_headache_neuro",
                    dimension="associated",
                    priority=1,
                    reason="Screen for red flags in headache (sudden severe onset, neck stiffness, vision changes, fever)",
                    en="Did this headache come on suddenly like a thunderclap, or with fever, neck stiffness, or blurred vision?",
                    bn="এই মাথাব্যথা কি হঠাৎ খুব তীব্রভাবে শুরু হয়েছে, বা সাথে জ্বর, ঘাড় শক্ত লাগা বা চোখে ঝাপসা দেখা আছে?",
                    hi="क्या यह सिरदर्द अचानक बहुत तेज़ शुरू हुआ, या इसके साथ बुखार, गर्दन में अकड़न या धुंधला दिखना है?",
                ))
            if not re.search(r"one side|temple|throb|band|forehead|একপাশ|एक तरफ", answered_text):
                candidates.append(InterviewGap(
                    key="headache_location_character",
                    dimension="location",
                    priority=2,
                    reason="Differentiate unilateral throbbing migraine vs bilateral tension/sinus headache",
                    en="Is the pain on one side of the head and throbbing, or like pressure across your forehead or both sides?",
                    bn="ব্যথা কি মাথার একপাশে দপদপ করছে, নাকি কপাল জুড়ে বা দুই পাশে চাপ ধরার মতো লাগছে?",
                    hi="क्या दर्द सिर के एक तरफ़ धड़कता हुआ है, या पूरे माथे और दोनों तरफ़ दबाव जैसा लगता है?",
                ))

        # GI / Abdominal pain / Acidity / Diarrhea / Nausea / Vomiting
        is_gi = any(s in pos_set for s in ("abdominal pain", "nausea", "vomiting", "diarrhea", "acidity", "constipation")) or any(
            w in full_signal for w in ("stomach", "belly", "abdom", "acidity", "heartburn", "bloat", "stool", "pet", "পেট", "অম্বল", "पेट")
        )
        if is_gi:
            if not re.search(r"blood|black stool|keep water|unable to drink|রক্ত|কালো পায়খানা|खून", answered_text):
                candidates.append(InterviewGap(
                    key="rf_gi_dehydration_bleed",
                    dimension="associated",
                    priority=1,
                    reason="Screen for GI bleeding or severe dehydration (unable to keep fluids down)",
                    en="Have you seen any blood in vomit or black stools, or are you unable to keep water down?",
                    bn="বমি বা পায়খানার সাথে কি রক্ত বা কালো পায়খানা হয়েছে, বা জল খেলেও কি উঠে যাচ্ছে?",
                    hi="क्या उल्टी में खून या काला मल आया है, या पानी पीने पर भी उल्टी हो रही है?",
                ))
            if not re.search(r"upper|lower|right|left|after eat|empty stomach|meal|খাওয়ার পর|খালি পেটে|खाने के", answered_text):
                candidates.append(InterviewGap(
                    key="gi_location_meals",
                    dimension="aggravating",
                    priority=2,
                    reason="Narrow GI etiology by abdominal location and relationship to meals",
                    en="Where exactly is the discomfort (upper or lower belly), and does eating make it better or worse?",
                    bn="পেটের ঠিক কোন অংশে ব্যথা বা অস্বস্তি হচ্ছে, এবং খাওয়ার পর কি এটি বাড়ে না কমে?",
                    hi="पेट के किस हिस्से में दर्द है (ऊपर या नीचे), और खाना खाने से यह बढ़ता है या कम होता है?",
                ))

        # Skin / Rash
        if "rash" in pos_set or any(w in full_signal for w in ("rash", "itch", "hives", "চুলকানি", "ফুসকুড়ি", "खुजली")):
            if not re.search(r"new medicine|soap|food|spread|fever|বিস্তার|নতুন ওষুধ|दवा", answered_text):
                candidates.append(InterviewGap(
                    key="skin_trigger_spread",
                    dimension="aggravating",
                    priority=2,
                    reason="Clarify rash triggers (new medicine/product/food) and spread or fever",
                    en="Did the rash start after any new medicine, food, or soap, and is it spreading quickly or with fever?",
                    bn="এই র‍্যাশ বা চুলকানি কি কোনো নতুন ওষুধ, খাবার বা সাবানের পর শুরু হয়েছে, এবং সাথে জ্বর আছে কি?",
                    hi="क्या ये चकत्ते किसी नई दवा, खाने या साबुन के बाद शुरू हुए, और क्या साथ में बुखार भी है?",
                ))

        # Musculoskeletal / Back / Joint pain
        if "body ache" in pos_set or any(w in full_signal for w in ("back pain", "neck pain", "knee pain", "joint pain", "কোমর", "হাঁটু", "कमर")):
            if not re.search(r"fall|injury|lift|numb|tingl|leg|swell|পড়ে|ঝিঁঝিঁ|ফোলা|सुन्न", answered_text):
                candidates.append(InterviewGap(
                    key="msk_trauma_radiation",
                    dimension="aggravating",
                    priority=2,
                    reason="Check injury/trauma history, swelling, or nerve radiation (numbness/tingling)",
                    en="Did this start after a fall or heavy lifting, and do you feel any swelling, numbness, or tingling going down an arm or leg?",
                    bn="এটি কি পড়ে গিয়ে বা ভারী কিছু তোলার পর শুরু হয়েছে, এবং ফোলা বা হাত-পায়ে ঝিঁঝিঁ বা অবশ ভাব আছে কি?",
                    hi="क्या यह चोट लगने या भारी सामान उठाने के बाद शुरू हुआ, और क्या सूजन या हाथ-पैर में झुनझुनी या सुन्नपन है?",
                ))

        # Generic clinical completeness dimensions (ONLY when genuinely missing and clinically relevant)
        has_signal = bool(pos_set or mem.discrepancies or is_symptom_turn)
        has_dur = any(s.duration or (s.temporal and not s.temporal.is_temporal_uncertain) for s in extraction.symptoms) or any(
            s.get("duration") or s.get("onset") for s in all_syms
        )
        if has_signal and not has_dur:
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
        if has_signal and not has_sev:
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

        if has_signal and not mem.long_term.get("conditions") and not re.search(r"diabetes|sugar|bp|hypertension|asthma|thyroid|no chronic|ডায়াবেটিস|প্রেসার", answered_text):
            candidates.append(InterviewGap(
                key="pmh_chronic_conditions",
                dimension="pmh",
                priority=4,
                reason="Ask about long-term conditions (diabetes, high BP, thyroid, anaemia)",
                en="Do you have any ongoing health conditions like high blood pressure, sugar (diabetes), or thyroid problems?",
                bn="আপনার কি আগে থেকেই উচ্চ রক্তচাপ, ডায়াবেটিস বা থাইরয়েডের মতো কোনো সমস্যা আছে?",
                hi="क्या आपको पहले से ब्लड प्रेशर, शुगर या थायरॉइड जैसी कोई बीमारी है?",
            ))

        if has_signal and not re.search(r"worked? before|happened before|past episode|আগে কখনো|আগে হয়েছিল", answered_text):
            candidates.append(InterviewGap(
                key="previous_episodes",
                dimension="prev_episodes",
                priority=4,
                reason="Check whether patient experienced similar episodes previously",
                en="Have you ever had a similar spell like this in the past, or is this the first time?",
                bn="আপনার কি আগে কখনো এমন হয়েছে, নাকি এই প্রথমবার হলো?",
                hi="क्या आपको पहले भी कभी ऐसी दिक्कत हुई है, या यह पहली बार है?",
            ))

        # Profile & Third-Party Subject gap-filling (prioritized early in the consultation)
        from app.clinical.subject_context import detect_subject_relation
        subj = detect_subject_relation(user_text or "")
        if not subj.get("is_third_party"):
            subj = detect_subject_relation(answered_text)
        demo = mem.long_term.get("demographics", {})
        if subj.get("is_third_party"):
            rel = str(subj.get("relation") or "family member")
            if subj.get("age") is None and "subject_gap_age" not in already_keys:
                candidates.insert(0, InterviewGap(
                    key="subject_gap_age",
                    dimension="subject_gap",
                    priority=1,
                    reason=f"Age of {rel} is essential for safe triage, dosing, and risk assessment",
                    en=f"To give accurate guidance for your {rel}, how old are they?",
                    bn=f"আপনার {rel}-এর জন্য সঠিক পরামর্শ দিতে, ওনার বয়স কত জানতে পারি?",
                    hi=f"आपके {rel} के लिए सही सलाह देने के लिए, उनकी उम्र कितनी है?",
                ))
            if subj.get("gender") is None and "subject_gap_gender" not in already_keys:
                candidates.insert(1 if candidates and candidates[0].key == "subject_gap_age" else 0, InterviewGap(
                    key="subject_gap_gender",
                    dimension="subject_gap",
                    priority=2,
                    reason=f"Gender of {rel} helps narrow relevant conditions",
                    en=f"Could you share your {rel}'s gender?",
                    bn=f"আপনার {rel}-এর লিঙ্গ (পুরুষ/মহিলা) জানাবেন?",
                    hi=f"क्या आप अपने {rel} का जेंडर (पुरुष/महिला) बता सकते हैं?",
                ))
            if "subject_gap_context" not in already_keys and not re.search(
                r"\b(?:diabetes|bp|hypertension|asthma|thyroid|heart|kidney|medicine|medication|allergy|no condition|none|nothing)\b",
                answered_text,
            ):
                candidates.append(InterviewGap(
                    key="subject_gap_context",
                    dimension="subject_gap",
                    priority=2,
                    reason=f"Existing conditions or regular medicines of {rel} shape safe recommendations",
                    en=f"Does your {rel} have any existing health conditions (like diabetes, BP, asthma) or take regular medicines?",
                    bn=f"আপনার {rel}-এর কি আগে থেকে কোনো অসুখ (যেমন ডায়াবেটিস, প্রেসার, হাঁপানি) আছে বা নিয়মিত কোনো ওষুধ খান?",
                    hi=f"क्या आपके {rel} को पहले से कोई बीमारी (जैसे शुगर, बीपी, अस्थमा) है या कोई नियमित दवा चलती है?",
                ))
        else:
            if not demo.get("dob") and "profile_gap_age" not in already_keys:
                candidates.append(InterviewGap(
                    key="profile_gap_age",
                    dimension="profile_gap",
                    priority=2,
                    reason="Age helps tailor risk assessment, specialist routing, and safe guidance",
                    en="To tailor this better for you, may I ask your age?",
                    bn="আপনার জন্য আরও সঠিক পরামর্শ দিতে, আপনার বয়স কত জানতে পারি?",
                    hi="आपके लिए सही सलाह देने के लिए, क्या मैं आपकी उम्र जान सकता हूँ?",
                ))
            if not demo.get("sex") and "profile_gap_gender" not in already_keys:
                candidates.append(InterviewGap(
                    key="profile_gap_gender",
                    dimension="profile_gap",
                    priority=2,
                    reason="Gender helps personalize clinical risk and screening guidance",
                    en="To personalize your health profile, what is your gender?",
                    bn="আপনার প্রোফাইল অনুযায়ী সঠিক পরামর্শ দিতে, আপনার লিঙ্গ (পুরুষ/মহিলা) জানাবেন?",
                    hi="आपकी प्रोफ़ाइल के अनुसार सही सलाह देने के लिए, आपका जेंडर (पुरुष/महिला) क्या है?",
                ))
            if is_resp and not demo.get("smoking") and "profile_gap_smoking" not in already_keys:
                candidates.append(InterviewGap(
                    key="profile_gap_smoking",
                    dimension="profile_gap",
                    priority=3,
                    reason="Smoking or tobacco history helps evaluate cough and breathing symptoms",
                    en="Do you currently smoke or use tobacco, or have you in the past?",
                    bn="আপনি কি বর্তমানে ধূমপান বা তামাক ব্যবহার করেন, বা আগে করতেন?",
                    hi="क्या आप अभी धूम्रपान या तंबाकू का सेवन करते हैं, या पहले करते थे?",
                ))

        # Filter already-asked questions on this encounter (UNCAPPED — works for 1 question or 8+ questions)
        remaining = [g for g in candidates if g.key not in already_keys]
        if not remaining:
            if enc:
                enc.interview_outcome = "SUFFICIENT_INFORMATION"
                await db.commit()
            # Keep the patient-doctor loop alive with a non-repeating, practical care-loop check-in
            care_loop_pool = [
                (
                    "care_loop_meds",
                    "Are you currently taking any regular medicines or home remedies for this, so I can check if anything might interact?",
                    "আপনি কি এখন এর জন্য কোনো নিয়মিত ওষুধ বা ঘরোয়া টোটকা নিচ্ছেন, যাতে আমি দেখে নিতে পারি কোনো সমস্যা হবে কিনা?",
                    "क्या आप अभी इसके लिए कोई नियमित दवा या घरेलू उपाय ले रहे हैं, ताकि मैं देख सकूँ कि कोई परेशानी तो नहीं होगी?",
                ),
                (
                    "care_loop_diet",
                    "Would you like practical suggestions on what foods or drinks can help you recover comfortably right now?",
                    "এখন তাড়াতাড়ি সুস্থ হতে কী ধরনের খাবার বা পানীয় খাওয়া ভালো এবং কী এড়িয়ে চলা উচিত, তা কি বলব?",
                    "अभी जल्दी आराम के लिए क्या खाना-पीना बेहतर रहेगा और किन चीज़ों से परहेज़ करना चाहिए, क्या मैं बताऊँ?",
                ),
                (
                    "care_loop_redflags",
                    "Would you like me to walk through the key warning signs to watch for over the next day or two?",
                    "আগামী এক-দুই দিনে কোন কোন লক্ষণ দেখলে দ্রুত ডাক্তারের কাছে যাওয়া উচিত, তা কি সংক্ষেপে বলে দেব?",
                    "अगले एक-दो दिनों में किन चेतावनी संकेतों पर तुरंत डॉक्टर को दिखाना चाहिए, क्या मैं संक्षेप में बताऊँ?",
                ),
                (
                    "care_loop_anything_else",
                    "Is there any other symptom, medicine, or concern on your mind that we haven't covered yet?",
                    "আপনার মনে কি আরও কোনো লক্ষণ, ওষুধ বা প্রশ্ন আছে যা আমরা এখনো আলোচনা করিনি?",
                    "क्या आपके मन में कोई और लक्षण, दवा या सवाल है जिस पर हमने अभी तक बात नहीं की?",
                ),
            ]
            cont_q: str | None = None
            for ck, en_q, bn_q, hi_q in care_loop_pool:
                if ck not in already_keys:
                    cont_q = bn_q if lang == "bn" else hi_q if lang == "hi" else en_q
                    db.add(FollowUpQuestion(
                        user_id=cu.id,
                        encounter_id=encounter_id,
                        symptom_id=None,
                        question_key=ck,
                        category="care_loop",
                        priority=5,
                        question_text_enc=cu.cipher.enc(cont_q),
                        status="pending",
                        reason="Keep the doctor-patient consultation loop active with practical next steps",
                    ))
                    await db.commit()
                    break
            return {
                "outcome": "SUFFICIENT_INFORMATION",
                "question": None,
                "continuation_question": cont_q,
                "candidates_remaining": 0,
            }

        remaining.sort(key=lambda g: g.priority)
        pick = remaining[0]

        if lang == "bn":
            q_text = f"{pick.context_lead_bn}{pick.bn}".strip()
        elif lang == "hi":
            q_text = f"{pick.context_lead_hi}{pick.hi}".strip()
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
                "why_asking": pick.reason,
                "answer_type": _infer_answer_type(q_text, pick.dimension),
                "remaining_candidates": len(remaining) - 1,
            },
        }
