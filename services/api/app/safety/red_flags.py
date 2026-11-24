"""Deterministic emergency / red-flag detection. Runs BEFORE any LLM call and overrides output.

Patterns cover English, Hindi (Devanagari), romanised Hindi/Hinglish, and key phrases in
Bengali, Marathi, Gujarati, Tamil, Telugu, Kannada, Malayalam, Odia, Punjabi, Assamese and Urdu. Every pattern list is
intentionally recall-oriented: a false alarm costs a banner, a miss can cost a life.
Clinical owner must review changes (see docs/safety-review.md).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

EMERGENCY_NUMBERS = {"ambulance": "108", "emergency": "112"}
TELE_MANAS = "14416"  # Govt of India national tele-mental-health helpline (also 1-800-891-4416)


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str  # emergency | mental_health | urgent
    patterns: tuple[str, ...]
    message_en: str
    message_hi: str
    requires_all: tuple[tuple[str, ...], ...] = ()  # each group must match at least once


def _p(*xs: str) -> tuple[str, ...]:
    return xs


RULES: tuple[Rule, ...] = (
    Rule(
        "cardiac_chest_pain", "emergency",
        _p(r"chest (pain|tightness|pressure|heaviness)", r"pain in (my |the )?chest", r"heart attack",
           r"(सीने|छाती) (में|मे).{0,20}(दर्द|जकड़न|भारीपन)", r"दिल का दौरा",
           r"(seene|sine|chhati|chati) (me|mein|main) (dard|jakdan|bharipan)", r"dil ka daura",
           r"বুকে? (ব্যথা|চাপ|ভারী|জ্বালা)", r"হার্ট অ্যাটাক", r"buke? (byatha|betha|chap)",
           r"छातीत (दुखत|वेदना)", r"છાતીમાં દુખાવો", r"நெஞ்சு வலி", r"ఛాతీ నొప్పి",
           r"ಎದೆ ನೋವು", r"നെഞ്ചുവേദന", r"ଛାତି ଯନ୍ତ୍ରଣା",
           r"ਛਾਤੀ (ਵਿੱਚ|ਵਿਚ|'ਚ) ?ਦਰਦ", r"ਦਿਲ ਦਾ ਦੌਰਾ", r"বুকু(ৰ|ত) বিষ", r"سینے (میں|مے) (درد|جکڑن|بھاری)", r"دل کا دورہ"),
        "Chest pain can be a heart attack. Call 108 / 112 now, stop any activity and sit down. "
        "Do not drive yourself.",
        "सीने में दर्द दिल का दौरा हो सकता है। तुरंत 108 / 112 पर कॉल करें। खुद गाड़ी न चलाएँ।",
    ),
    Rule(
        "stroke_fast", "emergency",
        _p(r"face (drooping|droop|is drooping)", r"(sudden|suddenly).{0,20}(weakness|numbness).{0,30}(arm|leg|one side|face)",
           r"slurred speech", r"can'?t (speak|talk) (properly|clearly)", r"\bstroke\b", r"paraly[sz]",
           r"लकवा", r"मुँह (टेढ़ा|टेढा)", r"(बोलने में|बोलने मे) (दिक्कत|तकलीफ)", r"एक तरफ.{0,15}(कमजोरी|सुन्न)",
           r"lakwa", r"muh (tedha|terha)", r"pakshaghat|पक्षाघात", r"পক্ষাঘাত", r"মুখ বেঁকে", r"কথা জড়িয়ে",
           r"(এক দিক|হাত পা) অবশ", r"mukh beke|ek dik obosh", r"ପକ୍ଷାଘାତ", r"ਅਧਰੰਗ", r"فالج",
           # Hinglish / Hindi face droop and one-sided weakness ("papa ka face ek side latak gaya"): found missing
           # by the chat eval, 2026-11-21. Speech trouble counts only together with droop / one-sided weakness.
           r"(face|chehr[ae]|mun?h|mooh).{0,25}(latak|tedh|terh|tirch|tedi)", r"ek (side|taraf).{0,20}(latak|sunn|kamzor)",
           r"(चेहर[ाे]|मुं?ह|मुँह).{0,20}(लटक|टेढ़|टेढ)", r"एक (तरफ|साइड).{0,20}(लटक|सुन्न|कमज़ोर|कमजोर)"),
        "These can be signs of a stroke. Note the time symptoms started and call 108 / 112 immediately — "
        "treatment works best within the first hours.",
        "ये लकवे (स्ट्रोक) के लक्षण हो सकते हैं। लक्षण शुरू होने का समय नोट करें और तुरंत 108 / 112 पर कॉल करें।",
    ),
    Rule(
        "breathing", "emergency",
        _p(r"(can'?t|cannot|unable to|difficulty|trouble|hard to) breath", r"short(ness)? of breath.{0,20}(severe|sudden|rest)",
           r"gasping", r"choking", r"lips? (turning )?(blue|bluish)",
           r"सांस (नहीं|लेने में (बहुत )?(दिक्कत|तकलीफ))", r"साँस (नहीं|लेने में (बहुत )?(दिक्कत|तकलीफ))", r"दम घुट",
           r"saans (nahi|lene me (bahut )?(dikkat|takleef))", r"sans nahi aa", r"dam ghut",
           r"শ্বাস (নিতে কষ্ট|কষ্ট)", r"শ্বাসকষ্ট", r"দম বন্ধ", r"shwash ?koshto|shas nite koshto|dam bondho",
           r"श्वास घेण्यास त्रास", r"શ્વાસ લેવામાં તકલીફ", r"மூச்சு விட (முடியவில்லை|சிரமம்)",
           r"ఊపిరి (ఆడటం లేదు|తీసుకోవడం కష్టం)", r"ಉಸಿರಾಟದ ತೊಂದರೆ", r"ശ്വാസം മുട്ട", r"ନିଶ୍ୱାସ ନେବାରେ କଷ୍ଟ",
           r"ਸਾਹ (ਨਹੀਂ|ਲੈਣ (ਵਿੱਚ|ਵਿਚ|'ਚ) (ਤਕਲੀਫ਼|ਤਕਲੀਫ|ਮੁਸ਼ਕਲ|ਔਖ))", r"উশাহ (লোৱাত|ল'বলৈ) (কষ্ট|অসুবিধা)|উশাহ বন্ধ",
           r"سانس (نہیں|لینے میں (بہت )?(دشواری|تکلیف|مشکل))", r"دم گھٹ"),
        "Severe breathing difficulty is an emergency. Call 108 / 112 now. Sit upright and stay calm.",
        "सांस लेने में गंभीर तकलीफ आपातकाल है। तुरंत 108 / 112 पर कॉल करें। सीधे बैठें।",
    ),
    Rule(
        "unconscious_seizure", "emergency",
        _p(r"unconscious", r"not (waking|responding)", r"passed out", r"blacked out", r"lost consciousness",
           r"faint(ed)? .{0,20}(not|won'?t) wake",
           r"\bseizures?\b|\bconvulsions?\b|\b(having|getting) fits\b", r"बेहोश", r"दौरा पड़", r"मिर्गी", r"behosh", r"mirgi",
           r"অজ্ঞান", r"মূর্ছা", r"চেতনাহীন", r"জ্ঞান হারিয়ে", r"খিঁচুনি", r"khichuni|oggan",
           r"बेशुद्ध", r"બેભાન", r"மயக்கம்", r"స్పృహ", r"ಪ್ರಜ್ಞೆ ತಪ್ಪ", r"ബോധം (കെട്ട|പോയ)", r"ଚେତା ହରା",
           r"ਬੇਹੋਸ਼", r"ਦੌਰਾ ਪੈ", r"ਮਿਰਗੀ", r"অচেতন|জ্ঞান হেৰু", r"মৃগী", r"بے ?ہوش", r"مرگی", r"دورہ پڑ"),
        "Unconsciousness or a seizure needs emergency care. Call 108 / 112. Turn the person on their side; "
        "do not put anything in their mouth.",
        "बेहोशी या दौरा आपातकाल है। 108 / 112 पर कॉल करें। व्यक्ति को करवट पर लिटाएँ, मुँह में कुछ न डालें।",
    ),
    Rule(
        "severe_bleeding", "emergency",
        _p(r"(heavy|severe|uncontrolled|won'?t stop) bleeding", r"bleeding (heavily|a lot|won'?t stop)",
           r"vomit(ing)? blood", r"blood in vomit", r"(black|tarry) stool", r"coughing (up )?blood",
           r"রক্ত বমি", r"প্রচুর রক্তপাত", r"রক্ত পড়া বন্ধ হচ্ছে না", r"কাশির সাথে রক্ত", r"rokto bomi|roktopat",
           r"खून (की उल्टी|बहना बंद नहीं|बहुत)", r"khoon (ki ulti|band nahi)", r"रक्तस्राव",
           r"ਖ਼?ੂਨ ਦੀ ਉਲਟੀ", r"তেজ বমি|তেজ ওলা", r"خون کی (الٹی|قے)", r"خون (بند نہیں|بہت بہ)"),
        "Heavy bleeding or vomiting/coughing blood is an emergency. Apply firm pressure to any wound and call 108 / 112.",
        "भारी रक्तस्राव या खून की उल्टी आपातकाल है। घाव पर ज़ोर से दबाव डालें और 108 / 112 पर कॉल करें।",
    ),
    Rule(
        "severe_allergy_anaphylaxis", "emergency",
        _p(r"anaphyla", r"severe allergic reaction", r"(lips|tongue|throat|face).{0,20}swelling.{0,30}(breath|swallow|allergy)?",
           r"swelling of (the )?(lips|tongue|throat|face)", r"গলা ফুলে", r"জিহ্বা ফুলে", r"মুখ ফুলে শ্বাস",
           r"गला सूज", r"जीभ सूज", r"gala sooj"),
        "Swelling of the face, lips, tongue or throat can be a severe allergic reaction (anaphylaxis). Call 108 / 112 immediately.",
        "चेहरे, होंठ, जीभ या गले में सूजन गंभीर एलर्जी (एनाफिलेक्सिस) हो सकती है। तुरंत 108 / 112 पर कॉल करें।",
    ),
    Rule(
        "severe_confusion", "emergency",
        _p(r"severe confusion", r"suddenly confused", r"disoriented", r"delirium", r"talking nonsense",
           r"প্রলাপ বকছে", r"অস্বাভাবিক বিভ্রান্তি", r"চিনতে পারছে না", r"बौखला|प्रलाप|पहचान नहीं पा रहा"),
        "Sudden severe confusion or disorientation needs urgent medical care. Call 108 / 112 now.",
        "अचानक गंभीर भ्रम या होश में गड़बड़ी को तुरंत आपातकालीन देखभाल की ज़रूरत है। 108 / 112 पर कॉल करें।",
    ),
    Rule(
        "poisoning_overdose", "emergency",
        _p(r"poison", r"overdose", r"swallowed .{0,30}(pills|tablets|acid|kerosene|pesticide|phenyl)",
           r"(took|taken|ate|swallowed|consumed|kha (li|liya|liye)).{0,25}\b([1-9]\d+|many|lots of|a lot of|whole (strip|bottle))\b.{0,20}(pills|tablets|goli|गोली)",
           r"(snake|scorpion) ?(bite|bit|bitten)", r"(bit|bitten|stung) by (a )?(snake|scorpion)", r"pesticide", r"ज़हर|जहर", r"सांप ने काट", r"साँप ने काट", r"बिच्छू",
           r"zeher|zahar", r"saap ne kaat", r"keetnashak|कीटनाशक", r"(?<!ৰ )(?<!ত )(?<!র )বিষ(?!\s*(?:হৈছে|হয়|কৰিছে|কৰে|পাইছে|পাইছো|লাগিছে)|াই|ুৱা)",
           r"বিষাক্ত|বিহ খা", r"সাপে (কামড়|কামোৰ)", r"साप चावला", r"பாம்பு கடி", r"పాము కాటు",
           r"ਜ਼ਹਿਰ|ਜਹਿਰ", r"ਸੱਪ ਨੇ (ਡੰਗ|ਡੱਸ)", r"زہر", r"سانپ (نے )?(کاٹ|ڈس)", r"بچھو"),
        "Possible poisoning, overdose or venomous bite — call 108 / 112 now and go to the nearest hospital. "
        "Do not induce vomiting. Take the container/strip with you.",
        "ज़हर, ओवरडोज़ या साँप/बिच्छू का काटना — तुरंत 108 / 112 पर कॉल करें और नज़दीकी अस्पताल जाएँ। उल्टी न कराएँ।",
    ),
    Rule(
        "pregnancy_emergency", "emergency",
        _p(r"pregnan.{0,40}(bleeding|severe pain|fits|seizure|baby not moving|water broke)",
           r"(bleeding|severe pain).{0,40}pregnan", r"गर्भ.{0,30}(खून|रक्तस्राव|तेज़ दर्द)",
           r"pregnant.{0,30}(khoon|dard)"),
        "Bleeding, severe pain, fits or reduced baby movements in pregnancy need urgent care. Call 108 / 102 "
        "(free maternal transport) or go to the nearest hospital now.",
        "गर्भावस्था में खून आना, तेज़ दर्द, दौरे या शिशु की हलचल कम होना — तुरंत 108 / 102 पर कॉल करें।",
    ),
    Rule(
        "meningitis_signs", "emergency",
        _p(r"(stiff neck|neck stiffness).{0,40}fever", r"fever.{0,40}(stiff neck|neck stiffness)",
           r"worst headache (of my life|ever)", r"thunderclap headache", r"गर्दन (में )?अकड़.{0,30}बुखार"),
        "Fever with a stiff neck, or a sudden 'worst ever' headache, needs emergency evaluation. Call 108 / 112.",
        "बुखार के साथ गर्दन में अकड़न, या अचानक बहुत तेज़ सिरदर्द — तुरंत 108 / 112 पर कॉल करें।",
    ),
    Rule(
        "animal_bite_rabies", "urgent",
        _p(r"(dog|cat|monkey|bat)\b.{0,30}\b(bit|bites|bite|bitten|scratch)", r"(bitten|bit|scratched) by (a |the |my )?(stray |street |pet )?(dog|cat|monkey|bat)",
           r"(kutt[ae]|kutton|billi|bandar).{0,30}(kaat|kat li|kata|kaata|panja|nakhun)",
           r"(कुत्त|बिल्ली|बंदर).{0,30}(काट|पंजा|नाखून)"),
        "An animal bite or scratch can pass on rabies, which is fatal once symptoms start but preventable. Wash the "
        "wound with soap and running water for 15 minutes, then go today to a hospital or health centre for the "
        "anti-rabies vaccine (free at government hospitals). Do not put chilli, turmeric or oil on the wound.",
        "जानवर के काटने या खरोंचने से रेबीज़ हो सकता है — लक्षण आने पर यह जानलेवा है, पर इसे रोका जा सकता है। घाव को "
        "साबुन और बहते पानी से 15 मिनट धोएँ, फिर आज ही अस्पताल या स्वास्थ्य केंद्र जाकर एंटी-रेबीज़ टीका लगवाएँ "
        "(सरकारी अस्पताल में मुफ़्त)। घाव पर मिर्च, हल्दी या तेल न लगाएँ।",
    ),
    Rule(
        "dengue_warning", "urgent",
        _p(r"(dengue|fever).{0,60}(bleeding gums|gums (are )?bleeding|bleeding (from|in) (the |my )?gums|nose ?bleed|"
           r"red spots|severe abdominal pain|persistent vomiting|keep vomiting|vomiting (repeatedly|again and again|a lot))",
           r"(मसूड़ों|नाक) से खून.{0,30}बुखार", r"बुखार.{0,40}(मसूड़ों|नाक) से खून"),
        "Fever with bleeding, rash spots, severe belly pain or repeated vomiting can be severe dengue. "
        "Go to a hospital today — do not take ibuprofen/aspirin.",
        "बुखार के साथ खून आना, लाल चकत्ते, पेट में तेज़ दर्द या बार-बार उल्टी गंभीर डेंगू हो सकता है। आज ही अस्पताल जाएँ।",
    ),
    Rule(
        "self_harm", "mental_health",
        _p(r"(kill|hurt|harm) (myself|me)", r"suicid", r"end (my|this) life", r"want to die", r"no reason to live",
           r"आत्महत्या", r"(खुद को|अपने आप को) (मार|खत्म|नुकसान)", r"मर जाना चाहता", r"मर जाना चाहती",
           r"jeena nahi chahta|jeena nahi chahti", r"khudkushi|खुदकुशी", r"marna chahta|marna chahti",
           r"আত্মহত্যা", r"आत्महत्या", r"આત્મહત્યા", r"தற்கொலை", r"ఆత్మహత్య", r"ಆತ್ಮಹತ್ಯೆ", r"ആത്മഹത്യ", r"ଆତ୍ମହତ୍ୟା",
           r"ਖ਼?ੁਦਕੁਸ਼ੀ|ਆਤਮ ?ਹੱਤਿਆ", r"ਮਰਨਾ ਚਾਹੁੰਦ", r"আত্মহত্যা|আত্মঘাতী", r"মৰিব বিচাৰ",
           r"خودکشی|خود کشی", r"مرنا چاہت", r"(خود|اپنے آپ) کو (مار|ختم|نقصان)"),
        "You don't have to go through this alone. Please call Tele-MANAS at 14416 (free, 24×7, many Indian "
        "languages) or 112 if you are in immediate danger. If you can, reach out to someone you trust right now.",
        "आप अकेले नहीं हैं। कृपया टेली-मानस 14416 (मुफ़्त, 24×7) पर कॉल करें, या तत्काल खतरे में 112 पर। "
        "किसी भरोसेमंद व्यक्ति से अभी बात करें।",
    ),
)

def _nfc(x: str) -> str:
    """One Unicode form for text and patterns: letters with a nukta (ਸ਼ ਜ਼ ज़ ড়) can be typed precomposed or as two
    code points, and a pattern written one way must still match text typed the other way."""
    return unicodedata.normalize("NFC", x)


_COMPILED = [(r, [re.compile(_nfc(p), re.IGNORECASE) for p in r.patterns]) for r in RULES]


@dataclass
class RedFlagResult:
    triggered: bool
    rules: list[str] = field(default_factory=list)
    kind: str | None = None  # most severe kind
    messages: list[str] = field(default_factory=list)
    call_numbers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"triggered": self.triggered, "rules": self.rules, "kind": self.kind,
                "messages": self.messages, "call_numbers": self.call_numbers}


_SEVERITY = {"emergency": 3, "mental_health": 3, "urgent": 2}

# For the 8 languages without full rule messages: one short call-to-action in the person's own language, shown
# before the detailed English message, so "what to do now" is never only in English. Needs native-speaker review.
LEAD: dict[str, dict[str, str]] = {
    "bn": {"emergency": "এটি জরুরি অবস্থা হতে পারে। এখনই 108 বা 112 নম্বরে ফোন করুন।",
           "mental_health": "আপনি একা নন। এখনই টেলি-মানস 14416 (বিনামূল্যে, 24 ঘণ্টা) বা 112 নম্বরে ফোন করুন।",
           "urgent": "আজই একজন ডাক্তার দেখান।"},
    "ta": {"emergency": "இது அவசர நிலையாக இருக்கலாம். உடனே 108 அல்லது 112-ஐ அழையுங்கள்.",
           "mental_health": "நீங்கள் தனியாக இல்லை. இப்போதே டெலி-மானஸ் 14416 (இலவசம், 24 மணி நேரமும்) அல்லது 112-ஐ அழையுங்கள்.",
           "urgent": "இன்றே மருத்துவரைப் பாருங்கள்."},
    "te": {"emergency": "ఇది అత్యవసర పరిస్థితి కావచ్చు. వెంటనే 108 లేదా 112కు కాల్ చేయండి.",
           "mental_health": "మీరు ఒంటరి కాదు. ఇప్పుడే టెలి-మానస్ 14416 (ఉచితం, 24 గంటలు) లేదా 112కు కాల్ చేయండి.",
           "urgent": "ఈరోజే డాక్టర్‌ను కలవండి."},
    "mr": {"emergency": "ही आणीबाणी असू शकते. आत्ताच 108 किंवा 112 वर फोन करा.",
           "mental_health": "तुम्ही एकटे नाही. आत्ताच टेली-मानस 14416 (मोफत, 24 तास) किंवा 112 वर फोन करा.",
           "urgent": "आजच डॉक्टरांना दाखवा."},
    "gu": {"emergency": "આ કટોકટી હોઈ શકે છે. હમણાં જ 108 અથવા 112 પર ફોન કરો.",
           "mental_health": "તમે એકલા નથી. હમણાં જ ટેલી-માનસ 14416 (મફત, 24 કલાક) અથવા 112 પર ફોન કરો.",
           "urgent": "આજે જ ડૉક્ટરને બતાવો."},
    "kn": {"emergency": "ಇದು ತುರ್ತು ಪರಿಸ್ಥಿತಿ ಆಗಿರಬಹುದು. ಈಗಲೇ 108 ಅಥವಾ 112ಕ್ಕೆ ಕರೆ ಮಾಡಿ.",
           "mental_health": "ನೀವು ಒಬ್ಬಂಟಿಯಲ್ಲ. ಈಗಲೇ ಟೆಲಿ-ಮಾನಸ್ 14416 (ಉಚಿತ, 24 ಗಂಟೆ) ಅಥವಾ 112ಕ್ಕೆ ಕರೆ ಮಾಡಿ.",
           "urgent": "ಇಂದೇ ವೈದ್ಯರನ್ನು ಭೇಟಿ ಮಾಡಿ."},
    "ml": {"emergency": "ഇത് ഒരു അടിയന്തര സാഹചര്യമാകാം. ഉടൻ 108 അല്ലെങ്കിൽ 112 എന്ന നമ്പറിൽ വിളിക്കുക.",
           "mental_health": "നിങ്ങൾ ഒറ്റയ്ക്കല്ല. ഇപ്പോൾ തന്നെ ടെലി-മനസ് 14416 (സൗജന്യം, 24 മണിക്കൂറും) അല്ലെങ്കിൽ 112 വിളിക്കുക.",
           "urgent": "ഇന്നുതന്നെ ഡോക്ടറെ കാണുക."},
    "or": {"emergency": "ଏହା ଜରୁରୀକାଳୀନ ଅବସ୍ଥା ହୋଇପାରେ। ଏବେ ହିଁ 108 କିମ୍ବା 112କୁ ଫୋନ କରନ୍ତୁ।",
           "mental_health": "ଆପଣ ଏକା ନୁହଁନ୍ତି। ଏବେ ହିଁ ଟେଲି-ମାନସ 14416 (ମାଗଣା, 24 ଘଣ୍ଟା) କିମ୍ବା 112କୁ ଫୋନ କରନ୍ତୁ।",
           "urgent": "ଆଜି ହିଁ ଡାକ୍ତର ଦେଖାନ୍ତୁ।"},
    "pa": {"emergency": "ਇਹ ਐਮਰਜੈਂਸੀ ਹੋ ਸਕਦੀ ਹੈ। ਹੁਣੇ 108 ਜਾਂ 112 'ਤੇ ਫ਼ੋਨ ਕਰੋ।",
           "mental_health": "ਤੁਸੀਂ ਇਕੱਲੇ ਨਹੀਂ ਹੋ। ਹੁਣੇ ਟੈਲੀ-ਮਾਨਸ 14416 (ਮੁਫ਼ਤ, 24 ਘੰਟੇ) ਜਾਂ 112 'ਤੇ ਫ਼ੋਨ ਕਰੋ।",
           "urgent": "ਅੱਜ ਹੀ ਡਾਕਟਰ ਨੂੰ ਦਿਖਾਓ।"},
    "as": {"emergency": "এইটো জৰুৰীকালীন অৱস্থা হ'ব পাৰে। এতিয়াই 108 বা 112 নম্বৰত ফোন কৰক।",
           "mental_health": "আপুনি অকলশৰীয়া নহয়। এতিয়াই টেলি-মানস 14416 (বিনামূলীয়া, 24 ঘণ্টা) বা 112 নম্বৰত ফোন কৰক।",
           "urgent": "আজিয়েই এজন চিকিৎসকক দেখুৱাওক।"},
    "ur": {"emergency": "یہ ایمرجنسی ہو سکتی ہے۔ ابھی 108 یا 112 پر فون کریں۔",
           "mental_health": "آپ اکیلے نہیں ہیں۔ ابھی ٹیلی-مانس 14416 (مفت، 24 گھنٹے) یا 112 پر فون کریں۔",
           "urgent": "آج ہی ڈاکٹر کو دکھائیں۔"},
}


_NEG_PRE = re.compile(
    r"(?i)(?:^|[\s,.;।!?])(?:no|not|never|denies|denied|without|neither|nor|haven'?t\s+had|don'?t\s+have|"
    r"do\s+not\s+have|didn'?t\s+have|কোনো|নাই|নেই|নয়|नहीं)\s+(?:any\s+|more\s+)?$"
)
_NEG_POST = re.compile(r"^(?:\s+at\s+all)?\s*(?:নাই|নেই|হয়\s*নি|হয়নি|नहीं(?:\s+है)?)(?=$|[\s,.;।!?])")


def _has_positive_match(text: str, pats: list[re.Pattern[str]]) -> bool:
    for p in pats:
        for m in p.finditer(text):
            pre = text[max(0, m.start() - 32):m.start()]
            post = text[m.end():min(len(text), m.end() + 20)]
            if _NEG_PRE.search(pre) or _NEG_POST.search(post):
                continue
            return True
    return False


def check(text: str, lang: str = "en", age_years: float | None = None) -> RedFlagResult:
    text = _nfc(text or "")
    hits: list[Rule] = []
    for rule, pats in _COMPILED:
        if _has_positive_match(text, pats):
            hits.append(rule)

    # Profile-aware rule: fever in an infant under 3 months is always an emergency.
    if age_years is not None and age_years < 0.25 and re.search(r"fever|बुखार|bukhar|temperature|ज्वर|জ্বর|জ্বৰ|காய்ச்சல்|జ్వరం|ਬੁਖ਼ਾਰ|ਬੁਖਾਰ|بخار", text, re.I):
        hits.append(Rule("infant_fever", "emergency", (), "Any fever in a baby under 3 months needs a doctor "
                         "immediately. Go to the nearest hospital or call 108.",
                         "3 महीने से छोटे शिशु में कोई भी बुखार — तुरंत अस्पताल जाएँ या 108 पर कॉल करें।"))

    if not hits:
        return RedFlagResult(False)
    hi = lang == "hi"
    numbers: list[str] = []
    for r in hits:
        nums = [TELE_MANAS, "112"] if r.kind == "mental_health" else ["108", "112"]
        numbers += [n for n in nums if n not in numbers]
    kind = max(hits, key=lambda r: _SEVERITY[r.kind]).kind
    messages = [r.message_hi if hi else r.message_en for r in hits]
    if lang in LEAD:
        leads = [LEAD[lang][k] for k in dict.fromkeys(r.kind for r in sorted(hits, key=lambda r: -_SEVERITY[r.kind]))]
        messages = leads + messages
    return RedFlagResult(
        True,
        rules=[r.id for r in hits],
        kind=kind,
        messages=messages,
        call_numbers=numbers,
    )
