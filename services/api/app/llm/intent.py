"""What is the person asking? Routes each chat message before the model is called.

The answer schema used to require possible conditions, tests and a specialist for every message, so "Hi" or
"What should I eat for lunch?" got a diagnosis. Now:
  greeting / thanks_bye / about_app / off_topic -> fixed reply, no model call
  medicine / nutrition_lifestyle / report_question / general_health -> model, general schema (no diagnosis)
  symptoms -> model, symptom schema (conditions, urgency, tests) + "check step by step" suggestion

Layers: whole-utterance phrase lists -> keyword cues (symptom / medicine / food / report) -> bge-m3
nearest-centroid over seed examples -> default general_health. Red-flag rules and Llama Guard still run first.
"""
from __future__ import annotations

import math
import re
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

Intent = Literal["greeting", "thanks_bye", "about_app", "off_topic", "symptoms", "medicine",
                 "nutrition_lifestyle", "report_question", "general_health"]
CONVERSATIONAL = {"greeting", "thanks_bye", "about_app", "off_topic"}
# Only exact phrase matches get a fixed reply. "off_topic" comes from the fuzzy embedding layer, so it still goes to
# the model (told to answer anything health-related): a wrong guess must never refuse a real symptom question.
FIXED_REPLY = {"greeting", "thanks_bye", "about_app"}

# ---------------------------------------------------------------- whole-utterance phrases
_GREET = {
    "hi", "hii", "hiii", "hello", "helo", "hlo", "hey", "heya", "hey there", "hi there", "hello there", "yo",
    "good morning", "good afternoon", "good evening", "good night", "gm", "morning",
    "namaste", "namaskar", "namaskaram", "namaskara", "vanakkam", "sat sri akal", "salaam", "salam",
    "assalamualaikum", "ram ram", "radhe radhe", "jai shri krishna", "pranam", "kem cho", "nomoshkar",
    "how are you", "how r u", "how are you doing", "kaise ho", "kaise hain", "kaise hain aap", "aap kaise hain",
    "kya haal hai", "kya hal hai", "hello how are you", "hi how are you",
    "नमस्ते", "नमस्कार", "हैलो", "हेलो", "हाय", "प्रणाम", "राम राम", "कैसे हो", "आप कैसे हैं", "क्या हाल है",
    "সুপ্রভাত", "নমস্কার", "হ্যালো", "কেমন আছেন", "কেমন আছো", "নমস্কার কেমন আছেন", "ভালো আছি", "kemon achen", "kemon acho", "nomoshkar",
    "வணக்கம்", "నమస్తే", "నమస్కారం", "హలో", "નમસ્તે", "કેમ છો",
    "ನಮಸ್ಕಾರ", "ಹಲೋ", "നമസ്കാരം", "ହେଲୋ", "ନମସ୍କାର",
}
_THANKS = {
    "thanks", "thank you", "thank u", "thanku", "thx", "ty", "tq", "thanks a lot", "thank you so much",
    "ok", "okay", "ok thanks", "okay thanks", "ok thank you", "great", "nice", "good", "cool", "fine", "got it",
    "bye", "goodbye", "good bye", "see you", "see ya", "tata", "alvida",
    "dhanyavad", "dhanyawad", "shukriya", "theek hai", "thik hai", "accha", "achha", "acha", "badhiya",
    "धन्यवाद", "शुक्रिया", "ठीक है", "अच्छा", "बढ़िया", "अलविदा", "ধন্যবাদ", "অনেক ধন্যবাদ", "ভালো থাকবেন", "ঠিক আছে",
    "onek dhonyobad", "dhonyobad", "நன்றி", "ధన్యవాదాలు",
    "આભાર", "ಧನ್ಯವಾದ", "ಧನ್ಯವಾದಗಳು", "നന്ദി", "ଧନ୍ୟବାଦ",
}
_ABOUT = {
    "who are you", "what are you", "what can you do", "what do you do", "how do you work", "how can you help",
    "how can you help me", "what can you help with", "what is sushrutai", "what is this app", "what is this",
    "are you a doctor", "are you human", "are you a bot", "what is your name", "your name",
    "tum kaun ho", "aap kaun ho", "aap kya kar sakte ho", "tum kya kar sakte ho", "aap kaun hain",
    "तुम कौन हो", "आप कौन हैं", "आप क्या कर सकते हैं", "तुम क्या कर सकते हो", "आप कौन हो",
    "আপনি কে", "তুমি কে", "আপনি কী করতে পারেন", "তুমি কী করতে পারো", "apni ke",
}
_FILLER_WORDS = {"sushrutai", "sushrut", "ai", "dear", "bro", "buddy", "ji", "sir", "madam", "mam", "please", "plz",
                 "again", "there", "जी", "सर", "कृपया"}


def _strip_filler(p: str) -> str:
    """Drop polite / filler words word by word (a regex word boundary does not work for Indic scripts)."""
    return " ".join(w for w in p.split() if w not in _FILLER_WORDS)


def _norm(text: str) -> str:
    t = text.lower()
    t = re.sub(r"[☀-➿\U0001F300-\U0001FAFF]", " ", t)  # emoji
    t = re.sub(r"[.,!?।॥:;\"'“”‘’()\-…]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _phrase_intent(text: str) -> Intent | None:
    n = _norm(text)
    if not n or len(n.split()) > 8:
        return None
    # split on punctuation / "and" BEFORE normalising, so "hey there, how are you?" is two short phrases
    raw = re.split(r"[,.!?।;]+|\s(?:and|और|&)\s", text.lower())
    parts = [_norm(p) for p in raw if _norm(p)] or [n]
    kinds: list[Intent] = []
    for p in parts:
        p2 = _strip_filler(p)
        if p2 in _GREET or p in _GREET:
            kinds.append("greeting")
        elif p2 in _THANKS or p in _THANKS:
            kinds.append("thanks_bye")
        elif p2 in _ABOUT or p in _ABOUT:
            kinds.append("about_app")
        elif not p2:
            continue
        else:
            return None  # something else is in the message: not purely conversational
    if not kinds:
        return None
    return "about_app" if "about_app" in kinds else "greeting" if "greeting" in kinds else "thanks_bye"


# ---------------------------------------------------------------- keyword cues
SYMPTOM = re.compile(
    r"(?i)fever|temperature (is|of|high)|high temp|\bpain(ful|s)?\b|\baches?\b|\baching\b|\bhurts?\b|\bhurting\b|\bsore(ness)?\b|"
    r"\bcough|(?<!cold )\b(a|the|have|got|caught|with) cold\b|cold and cough|runny nose|blocked nose|sneez|\bvomit|"
    r"nausea|throwing up|diarrh|loose motion|constipat|headache|migraine|dizz|\bfaint|giddy|breathless|"
    r"short(ness)? of breath|can'?t breathe|breathing (problem|difficult|trouble)|wheez|chest (pain|tight)|\brash|"
    r"\bitch|\bswell|\bswollen|\bbleed|blood in|burning (urine|sensation|feet)|\bcramps?\b|fatigue|\btired\b|"
    r"weakness|\bweak\b|\bnumb\b|numbness|tingl|palpitat|insomnia|can'?t sleep|period pain|missed (my )?period|"
    r"burning (while|when|during)|passing urine|while urinating|pain (while|when) urinat|frequent urination|"
    r"irregular period|discharge|\binjur|\bwound|\bcut myself|\bbitten|\bbite\b|\bstung\b|\bsting\b|\blump\b|"
    r"infection|allerg|jaundice|yellow (eyes|skin)|\bulcer|toothache|ear ?ache|red eye|blurred vision|unwell|"
    r"\bsick\b|\bill\b|not feeling (well|good)|feeling low|body ache|joint pain|can'?t eat|cannot eat|"
    r"not able to eat|no appetite|loss of appetite|"
    r"swallow|\bdysphagia\b|sore throat|"
    r"bukhar|bukhaar|\bdard\b|khansi|khaansi|jukam|zukam|\bulti\b|\bdast\b|chakkar|saans (phool|lene)|\bsujan\b|"
    r"soojan|khujli|\bjalan\b|kamzori|kamjori|thakan|thakaan|pet (dard|me|mein|kharab)|sir ?dard|sar ?dard|"
    r"tabiyat|tabiyet|bhook nahi|kharash|gal(a|e) (me|mein|dukh|kharab|baith|kharash)|badan dard|kamar dard|"
    r"jodo?n? (me|mein) dard|ghutno?n? (me|mein) dard|khasi|zukham|naak beh|aankh (lal|me jalan)|"
    r"बुखार|ज्वर|दर्द|खांसी|खाँसी|जुकाम|ज़ुकाम|सर्दी लग|उल्टी|दस्त|चक्कर|सांस|साँस|सूजन|खुजली|जलन|कमजोरी|"
    r"कमज़ोरी|थकान|पेट दर्द|पेट में|सिर दर्द|सिरदर्द|तबीयत|भूख नहीं|"
    r"জ্বর|ব্যথা|কাশি|বমি|মাথা\s*ঘোর|মাথা\s*ভারী|দুর্বল|শরীর\s*খারাপ|শরীর\s*ভালো\s*না|শ্বাসকষ্ট|শ্বাস\s*নিতে\s*কষ্ট|হাঁটু|কোমর|ঝিনঝিন|ঝিমঝিম|"
    r"matha\s*ghur\w*|buk\s*e?\s*(?:betha|byatha)|shash\s*nite\s*koshto|shorir\s*kharap|durbol|jwor|pet\s*e?\s*(?:byatha|betha)|jhim\s*jhim|"
    r"காய்ச்சல்|வலி|இருமல்|வாந்தி|జ్వరం|నొప్పి|దగ్గు|వాంతి|दुखणे|खोकला|"
    r"તાવ|દુખાવો|ઉધરસ|ઉલટી|ಜ್ವರ|ನೋವು|ಕೆಮ್ಮು|ವಾಂತಿ|പനി|വേദന|ചുമ|ഛർദ്ദി|ଜ୍ୱର|ଯନ୍ତ୍ରଣା|କାଶ|ବାନ୍ତି")
# looks like a symptom word but is not
SYMPTOM_FALSE = re.compile(r"(?i)cold (drink|water|coffee|storage)|burn (fat|calories)|pain ?killers?")

MEDICINE = re.compile(
    r"(?i)\btablets?\b|\btabs?\b|capsules?|\bsyrup|injection|\bdoses?\b|dosage|\bmg\b|medicines?|medication|"
    r"\bdrugs?\b|\bpills?\b|side ?effects?|\bgeneric|substitute|jan aushadhi|pharmacy|chemist|antibiotics?|"
    r"antacid|pain ?killers?|supplements?|"
    r"\bdawai|\bdawa\b|\bdavai|\bgoli\b|दवा|दवाई|गोली|टैबलेट|सिरप|इंजेक्शन|"
    r"\bdolo\b|crocin|calpol|combiflam|disprin|brufen|\bpan[ -]?(40|d)\b|rantac|digene|\beno\b|vicks|benadryl|"
    r"allegra|azithral|augmentin|metformin|glycomet|telma|amlodipine|ecosprin|thyronorm|eltroxin|shelcal|"
    r"becosules|zincovit|limcee|\bors\b|electral|cetirizine|paracetamol|ibuprofen|omeprazole|pantoprazole|"
    r"atorvastatin")
# an actual question about a medicine (vs merely mentioning one taken while describing symptoms)
MEDICINE_Q = re.compile(
    r"(?i)can i (take|give|have|use)|should i (take|give|stop)|is it safe|safe (to|for|in)|side ?effects?|"
    r"what is\b|what are\b|what'?s\b|kya hai|kya hota|kaise le|kab le|how (to|do i|should i) take|when (to|should i) take|"
    r"dose of|dosage|alternative|substitute|cheaper|generic|price|cost|interact|together with|along with|"
    r"instead of|for what|used for|kis liye|किस लिए|क्या है|ले सकते|ले सकता|ले सकती|कैसे लें|कब लें")
FOOD = re.compile(
    r"(?i)\beat\b|eating|\bfoods?\b|\bdiet\b|\bmeals?\b|breakfast|lunch|dinner|snack|recipe|\bdrinks?\b|"
    r"water intake|fruits?\b|vegetables?|protein|calorie|lose weight|weight loss|gain weight|weight gain|"
    r"\bcurd\b|\bdahi\b|\bmilk\b|\brice\b|\broti\b|chapati|\bdal\b|\bghee\b|\beggs?\b|chicken|\bfish\b|\btea\b|"
    r"coffee|\bjuice|salad|millets?|paneer|\bnuts\b|honey|jaggery|\bgur\b|belly fat|body fat|\bfat loss|"
    r"(good|bad|safe|ok|okay) (for|in) (a )?(diabetic|diabetics|diabetes|bp|blood pressure|heart|kidney|pregnancy|"
    r"thyroid|cholesterol)|"
    r"khana|khaana|nashta|bhojan|kya khaye|kya khau|kya khana|खाना|भोजन|नाश्ता|खाएं|खाऊं|खाऊँ|खाएँ|आहार")
# asking what to eat — answered as nutrition even when a symptom is mentioned ("what to eat in fever?")
FOOD_Q = re.compile(
    r"(?i)what (should|can|to|do) (i |we )?(eat|drink|have)|what (food|foods|diet)|which (food|foods|fruit|fruits)|"
    r"(diet|food|foods) (for|in|during|to avoid|to eat|chart|plan)|best (food|foods|diet)|foods? (that|to)|recipe|"
    r"healthy (lunch|dinner|breakfast|snack|meal)|kya khaye|kya khaaye|kya khau|kya khana chahiye|kya khayen|"
    r"क्या खाएं|क्या खाऊं|क्या खाना|क्या खाएँ")
ACTIVITY = re.compile(
    r"(?i)exercise|workout|\bwalk(ing)?\b|yoga|\bgym\b|fitness|\bsleep\b|"
    r"lifestyle|healthy habit|stress (relief|management)|vyayam|व्यायाम|योग|नींद")
REPORT = re.compile(
    r"(?i)\breports?\b|\bresults?\b|\blab\b|blood test|test (value|result|report)|hba1c|\bcbc\b|\blft\b|\bkft\b|"
    r"\btsh\b|lipid profile|thyroid (test|report)|my (sugar|hb|haemoglobin|hemoglobin|cholesterol|creatinine) "
    r"(is|level|was)|normal range|reference range|x-?ray report|scan report|ultrasound report|"
    r"\b(ldl|hdl|vldl|triglycerides?|sgpt|sgot|alt|ast|creatinine|urea|tsh|t3|t4|hba1c|a1c|h(a)?emoglobin|hb|"
    r"platelets?|esr|crp|vitamin d|b12|ferritin|uric acid|bilirubin|psa)\b[^.?!]{0,20}\d|"
    r"रिपोर्ट|जांच|जाँच")

_generics: set[str] | None = None


async def _generic_names(db: AsyncSession) -> set[str]:
    global _generics
    if _generics is None:
        from app.models import Generic
        _generics = {g for g in await db.scalars(select(Generic.inn_name)) if g and len(g) >= 5}
    return _generics


def keyword_intent(text: str, generic_hit: bool = False) -> Intent | None:
    sym = bool(SYMPTOM.search(SYMPTOM_FALSE.sub(" ", text)))
    med = generic_hit or bool(MEDICINE.search(text))
    # A question *about* food or a medicine is answered as that, even if a symptom is mentioned
    # ("what to eat in fever?", "can I take Dolo for fever?"): those answers must not become a diagnosis list.
    # A medicine merely mentioned while describing symptoms ("fever 2 days, took Crocin") stays a symptom question.
    if FOOD_Q.search(text):
        return "nutrition_lifestyle"
    if med and (MEDICINE_Q.search(text) or not sym):
        return "medicine"
    rep = bool(REPORT.search(text))
    if rep and not sym:
        return "report_question"
    if sym:
        return "symptoms"
    if FOOD.search(text) or ACTIVITY.search(text):
        return "nutrition_lifestyle"
    return None


# ---------------------------------------------------------------- embedding fallback
SEEDS: dict[str, list[str]] = {
    "off_topic": ["what is the capital of france", "tell me a joke", "who won the cricket match yesterday",
                  "write a poem about the sea", "what is the weather today", "convert 5 km to miles",
                  "recommend a good movie", "what is the price of gold", "help me with my maths homework",
                  "who is the prime minister", "translate this sentence to french", "book a train ticket",
                  "kal ka match kaun jeeta", "mujhe ek joke sunao", "मौसम कैसा है"],
    "general_health": ["how can i stay healthy", "how to improve immunity", "is it safe to get vaccinated",
                       "how much water should i drink a day", "what is blood pressure", "what causes diabetes",
                       "how to prevent dengue", "what is a normal heart rate", "how often should i get a health checkup",
                       "how to reduce stress", "is smoking harmful", "what is cholesterol",
                       "sehat kaise achhi rakhe", "immunity kaise badhaye", "डायबिटीज क्या है"],
}
_centroids: dict[str, list[float]] | None = None


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


async def _embedding_intent(text: str) -> Intent | None:
    global _centroids
    from app.rag.store import _embed
    if _centroids is None:
        cents: dict[str, list[float]] = {}
        for label, ex in SEEDS.items():
            vecs = await _embed(ex)
            if not vecs:
                return None
            cents[label] = [sum(col) / len(vecs) for col in zip(*vecs)]
        _centroids = cents
    v = await _embed([text])
    if not v:
        return None
    sims = {k: _cos(v[0], c) for k, c in _centroids.items()}
    best = max(sims, key=sims.get)
    other = min(sims, key=sims.get)
    # off-topic only with a clear margin: when unsure, treat it as a health question (the safer default)
    if best == "off_topic" and sims["off_topic"] - sims[other] >= 0.04:
        return "off_topic"
    return None


async def classify(db: AsyncSession, text: str, *, has_image: bool = False) -> Intent:
    if not has_image and (p := _phrase_intent(text)):
        return p
    words = {w for w in re.findall(r"[a-z]{5,}", text.lower())}
    generic_hit = bool(words & await _generic_names(db))
    if k := keyword_intent(text, generic_hit):
        return k
    if has_image:
        return "general_health"
    if e := await _embedding_intent(text):
        return e
    return "general_health"


# ---------------------------------------------------------------- fixed replies (no model call)
ACTIONS = [{"key": "checkSymptoms", "href": "/ask/check"}, {"key": "readRx", "href": "/medicines/rx"},
           {"key": "cheaperMeds", "href": "/medicines"}, {"key": "myReports", "href": "/records"}]

FIXED: dict[str, dict[str, str]] = {
    "greeting": {
        "en": "Namaste! I'm SushrutAI, your health assistant. Tell me how you're feeling, ask about a medicine "
              "or your report, or ask what to eat — by voice or by typing.",
        "hi": "नमस्ते! मैं SushrutAI हूँ, आपका स्वास्थ्य सहायक। बताइए आप कैसा महसूस कर रहे हैं, किसी दवा या "
              "रिपोर्ट के बारे में पूछिए, या खाने के बारे में — बोलकर या लिखकर।",
        "bn": "নমস্কার! আমি SushrutAI, আপনার স্বাস্থ্য সহায়ক। আজ আপনার শরীর কেমন লাগছে, কোনো উপসর্গ, ওষুধ "
              "বা রিপোর্ট সম্পর্কে কিছু জানতে চাইলে বলুন বা লিখে জানান।",
    },
    "thanks_bye": {
        "en": "You're welcome. Take care! If you feel unwell, I'm here — and in an emergency call 108 or 112.",
        "hi": "आपका स्वागत है। अपना ध्यान रखें! तबीयत खराब लगे तो मैं यहाँ हूँ — आपात स्थिति में 108 या 112 पर कॉल करें।",
        "bn": "স্বাগতম। নিজের যত্ন নেবেন! শরীর খারাপ লাগলে আমি পাশে আছি — আর জরুরি অবস্থায় 108 বা 112 নম্বরে কল করুন।",
    },
    "about_app": {
        "en": "I'm SushrutAI, a health-information assistant — not a doctor. I can help you understand symptoms "
              "and when to see a doctor, read your prescription and lab reports, find cheaper generic medicines, "
              "remind you of doses, and prepare a summary for your doctor. I run on this device and keep your "
              "data private.",
        "hi": "मैं SushrutAI हूँ, एक स्वास्थ्य-जानकारी सहायक — डॉक्टर नहीं। मैं लक्षण समझने और डॉक्टर के पास कब जाना है "
              "यह बताने, आपका पर्चा और लैब रिपोर्ट पढ़ने, सस्ती जेनेरिक दवाएँ ढूँढने, दवा याद दिलाने और डॉक्टर के लिए "
              "सारांश बनाने में मदद कर सकता हूँ। मैं इसी डिवाइस पर चलता हूँ और आपका डेटा निजी रखता हूँ।",
        "bn": "আমি SushrutAI, একটি স্বাস্থ্য-তথ্য সহায়ক — চিকিৎসক নই। আমি উপসর্গ বুঝতে, কখন ডাক্তার দেখাবেন তা "
              "বুঝতে, প্রেসক্রিপশন ও ল্যাব রিপোর্ট পড়তে, সাশ্রয়ী জেনেরিক ওষুধ খুঁজতে এবং ডাক্তারের জন্য "
              "সারাংশ তৈরি করতে সাহায্য করি। আপনার তথ্য সুরক্ষিত ও গোপন রাখা হয়।",
    },
    "off_topic": {
        "en": "I can only help with health questions — symptoms, medicines, reports, food and fitness. "
              "What would you like to know about your health?",
        "hi": "मैं केवल स्वास्थ्य से जुड़े सवालों में मदद कर सकता हूँ — लक्षण, दवाएँ, रिपोर्ट, खान-पान और फिटनेस। "
              "आप अपनी सेहत के बारे में क्या जानना चाहेंगे?",
        "bn": "আমি কেবল স্বাস্থ্য সংক্রান্ত প্রশ্নে সাহায্য করতে পারি — উপসর্গ, ওষুধ, রিপোর্ট, খাবার ও সুস্থতা। "
              "আপনার স্বাস্থ্য নিয়ে কী জানতে চান?",
    },
}


def fixed_reply(intent: str, lang: str) -> str:
    msgs = FIXED[intent]
    return msgs.get(lang, msgs["en"])
