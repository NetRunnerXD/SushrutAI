"""AdaptiveClinicalQuestionEngine: complaint-specific, non-repetitive doctor-like follow-up questioning."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.clinical.context_manager import ConsultationContext
from app.clinical.knowledge_graph import analyze_clinical_differentials
from app.clinical.routing import RoutingDecision


@dataclass(frozen=True)
class QuestionPlan:
    should_ask: bool
    sufficient_information: bool
    question: str | None
    topic: str | None
    reason: str
    score: int = 0
    distinguishing_topics: tuple[str, ...] = field(default_factory=tuple)


def _lang_text(lang: str, en: str, bn: str, hi: str) -> str:
    return bn if lang == "bn" else hi if lang == "hi" else en


def plan_next_question(
    ctx: ConsultationContext,
    routing: RoutingDecision,
    *,
    lang: str = "en",
) -> QuestionPlan:
    """Decide whether to ask 1 focused clinical question OR deliver the full medical assessment."""
    # Level 1 emergency: NEVER delay with routine questioning
    if routing.urgency_level == 1:
        return QuestionPlan(False, True, None, None, "Emergency warning signs require immediate care.")

    # Stop questioning once we have sufficient context or >=3 follow-up turns already answered
    diff = analyze_clinical_differentials(
        ctx.merged_clinical_text,
        symptoms=ctx.symptoms,
        answered_topics=ctx.answered_topics,
        lang=lang,
    )
    has_duration = "duration" in ctx.answered_topics or bool(ctx.durations)
    has_modifier = bool(ctx.aggravating or ctx.negatives or len(ctx.symptoms) >= 2)
    if ctx.followup_turn_count >= 3 or (has_duration and has_modifier and not diff.missing_information):
        return QuestionPlan(False, True, None, None, "Sufficient clinical context collected for full guidance.")

    dept = routing.primary_department
    asked = ctx.answered_topics
    sig = ctx.merged_clinical_text.lower()

    # Complaint-specific adaptive question banks (ordered by clinical priority)
    bank: list[tuple[str, str, str, str]] = []
    if dept == "Cardiology" or "chest pain" in ctx.symptoms:
        bank = [
            ("current_now",
             "Is this chest pain happening right now, and is it severe or spreading to your arm, jaw, or back?",
             "এই বুকে ব্যথা কি এখনই হচ্ছে, আর ব্যথা কি খুব তীব্র বা বাঁ হাত, চোয়াল বা পিঠের দিকে ছড়াচ্ছে?",
             "क्या यह सीने का दर्द अभी हो रहा है, और क्या यह तेज़ है या हाथ, जबड़े या पीठ में फैल रहा है?"),
            ("warning_signs",
             "Along with this, do you have sweating, dizziness, faintness, or shortness of breath?",
             "এর সাথে কি ঘাম হচ্ছে, মাথা ঘুরছে, অজ্ঞান লাগছে বা শ্বাস নিতে কষ্ট হচ্ছে?",
             "क्या इसके साथ पसीना आ रहा है, चक्कर आ रहे हैं या सांस लेने में दिक्कत है?"),
            ("aggravating",
             "Does it come on when walking or exerting yourself, or even while resting?",
             "এই ব্যথা কি হাঁটাচলা বা পরিশ্রমের সময় হয়, নাকি বিশ্রামের সময়ও হয়?",
             "क्या यह चलते समय या मेहनत करने पर होता है, या आराम करते समय भी होता है?"),
        ]
    elif dept == "Pulmonology" or "shortness of breath" in ctx.symptoms or "cough" in ctx.symptoms:
        bank = [
            ("current_now",
             "Are you having difficulty breathing right now at rest, or does it mostly happen occasionally or on exertion?",
             "এই শ্বাসকষ্ট কি এখনই বিশ্রাম অবস্থায় হচ্ছে, নাকি মাঝে মাঝে বা পরিশ্রমের সময় হয়?",
             "क्या अभी आराम करते समय सांस फूल रही है, या यह कभी-कभी या चलने पर होता है?"),
            ("duration",
             "How long have you had this cough or breathing discomfort?",
             "এই কাশি বা শ্বাসকষ্ট মোটামুটি কতদিন ধরে হচ্ছে?",
             "यह खांसी या सांस की तकलीफ लगभग कितने समय से है?"),
            ("warning_signs",
             "Do you also have fever, wheezing, or chest pain?",
             "এর সাথে কি জ্বর, বুকে সাঁই সাঁই শব্দ বা বুকে ব্যথা আছে?",
             "क्या इसके साथ बुखार, सीने में घरघराहट या दर्द भी है?"),
        ]
    elif dept == "Orthopaedics" or "knee pain" in ctx.symptoms or "back pain" in ctx.symptoms:
        bank = [
            ("duration",
             "When did this pain start, and did it begin after any fall or injury?",
             "এই ব্যথা কবে থেকে শুরু হয়েছে, আর কোনো চোট বা পড়ে যাওয়ার পর কি শুরু হয়েছে?",
             "यह दर्द कब से शुरू हुआ, और क्या किसी चोट या गिरने के बाद शुरू हुआ?"),
            ("aggravating",
             "Does the pain worsen when walking or climbing stairs, and is there any swelling or fever?",
             "হাঁটলে বা সিঁড়ি ভাঙলে কি ব্যথা বাড়ে, আর হাঁটু ফোলা বা জ্বর আছে কি?",
             "क्या चलने या सीढ़ियाँ चढ़ने पर दर्द बढ़ता है, और क्या सूजन या बुखार भी है?"),
        ]
    elif dept == "Neurology" or "headache" in ctx.symptoms or "dizziness" in ctx.symptoms:
        bank = [
            ("duration",
             "When did this start — was it sudden or gradual, and have you had similar episodes before?",
             "এটি কবে থেকে শুরু হয়েছে — হঠাৎ শুরু হয়েছে নাকি ধীরে ধীরে, আর আগেও কি এমন হয়েছে?",
             "यह कब से शुरू हुआ — अचानक या धीरे-धीरे, और क्या पहले भी ऐसा हुआ है?"),
            ("warning_signs",
             "Do you have fever, vomiting, blurred vision, or any numbness/weakness on one side?",
             "এর সাথে কি জ্বর, বমি, চোখ ঝাপসা দেখা বা শরীরের একদিকে অবশ ভাব আছে?",
             "क्या इसके साथ बुखार, उल्टी, धुंधला दिखना या एक तरफ सुन्नपन है?"),
        ]
    elif dept in ("Gastroenterology", "Hepatology", "General Surgery") or "abdominal pain" in ctx.symptoms:
        bank = [
            ("location",
             "Where in your abdomen is the pain located — upper abdomen, lower abdomen, right/left side, or all over?",
             "আপনার পেটের কোন জায়গায় ব্যথা হচ্ছে — উপরের দিকে, নিচের দিকে, ডান বা বাঁ পাশে, নাকি পুরো পেটে?",
             "आपके पेट के किस हिस्से में दर्द है — ऊपरी हिस्से में, निचले हिस्से में, दाएँ/बाएँ तरफ़ या पूरे पेट में?"),
            ("duration",
             "When did the abdominal pain start, and is it continuous or cramping?",
             "পেটের ব্যথা কবে থেকে শুরু হয়েছে, আর এটি কি একটানা হচ্ছে নাকি মোচড় দিয়ে হচ্ছে?",
             "पेट दर्द कब से शुरू हुआ है, और यह लगातार है या मरोड़ के साथ आता है?"),
            ("associated",
             "Do you also have vomiting, fever, loose motions, or burning urination?",
             "এর সাথে কি বমি, জ্বর, পাতলা পায়খানা বা প্রস্রাবে জ্বালা হচ্ছে?",
             "क्या इसके साथ उल्टी, बुखार, दस्त या पेशाब में जलन भी हो रही है?"),
        ]
    elif "fever" in ctx.symptoms or "জ্বর" in sig:
        bank = [
            ("duration",
             "How many days have you had the fever, and have you checked the temperature?",
             "জ্বর কয়দিন ধরে আছে এবং থার্মোমিটারে কত তাপমাত্রা দেখেছেন?",
             "बुखार कितने दिनों से है और क्या आपने तापमान नापा है?"),
            ("associated",
             "Along with fever, do you have cough, body ache, burning urination, or skin rash?",
             "জ্বরের সাথে কি কাশি, গায়ে ব্যথা, প্রস্রাবে জ্বালা বা গায়ে কোনো র‍্যাশ আছে?",
             "बुखार के साथ क्या खांसी, बदन दर्द, पेशाब में जलन या शरीर पर दाने हैं?"),
        ]
    else:
        bank = [
            ("duration",
             "How long have you been experiencing these symptoms, and are they getting better or worse?",
             "এই সমস্যাগুলো কতদিন ধরে হচ্ছে এবং সময়ের সাথে বাড়ছে নাকি কমছে?",
             "ये लक्षण कितने समय से हैं और समय के साथ बढ़ रहे हैं या कम हो रहे हैं?"),
            ("history_meds",
             "Do you have any existing conditions like diabetes or high BP, or take any regular medicines?",
             "আপনার কি আগে থেকেই ডায়াবেটিস বা উচ্চ রক্তচাপের সমস্যা আছে, বা কোনো নিয়মিত ওষুধ খান?",
             "क्या आपको पहले से शुगर या ब्लड प्रेशर की समस्या है, या कोई नियमित दवा लेते हैं?"),
        ]

    missing_set = set(diff.missing_information)
    scored: list[tuple[int, int, str, str]] = []
    for idx, (topic, q_en, q_bn, q_hi) in enumerate(bank):
        if topic in asked:
            continue
        score = 50 - idx * 5
        if topic in missing_set:
            score += 40
        if topic in ("warning_signs", "current_now"):
            score += 25
        if topic == "duration" and not has_duration:
            score += 45
        if diff.multi_symptom_linked and topic in ("associated", "warning_signs"):
            score += 15
        scored.append((score, -idx, topic, _lang_text(lang, q_en, q_bn, q_hi)))

    if scored:
        scored.sort(reverse=True)
        best_score, _, best_topic, best_q = scored[0]
        return QuestionPlan(
            should_ask=True,
            sufficient_information=False,
            question=best_q,
            topic=best_topic,
            reason=f"Ranked highest (score={best_score}) to distinguish causes and check safety.",
            score=best_score,
            distinguishing_topics=tuple(diff.missing_information),
        )

    return QuestionPlan(False, True, None, None, "All key clarifying questions already answered.")
