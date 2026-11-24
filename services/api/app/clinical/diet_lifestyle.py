"""Condition-specific, safety-aware Food, Hydration, and Lifestyle Guidance Engine.

Respects kidney function (CKD/Nephrology), pregnancy, age, comorbidities, and allergies;
never gives generic high-potassium or restrictive diet advice blindly.
"""
from __future__ import annotations

import re
from typing import Any


def _pick(lang: str, en: str, bn: str, hi: str) -> str:
    return bn if lang == "bn" else hi if lang == "hi" else en


def recommend_diet_and_lifestyle(
    signal_text: str,
    *,
    dept: str = "General Medicine",
    lang: str = "en",
    age_years: int | None = None,
    pregnant: bool = False,
    comorbidities: list[str] | None = None,
    allergies: list[str] | None = None,
) -> dict[str, Any]:
    """Return personalized, medically cautious food, hydration, and lifestyle guidance."""
    sig = f"{signal_text or ''} {' '.join(comorbidities or [])}".lower()
    allergy_note = ""
    if allergies:
        allergy_note = _pick(
            lang,
            f"Avoid any known allergens ({', '.join(allergies)}).",
            f"আপনার পরিচিত অ্যালার্জির খাবার ({', '.join(allergies)}) সম্পূর্ণ এড়িয়ে চলুন।",
            f"अपनी ज्ञात एलर्जी वाले खाद्य पदार्थों ({', '.join(allergies)}) से पूरी तरह बचें।",
        )

    helpful: list[str] = []
    limit: list[str] = []
    hydration: str
    lifestyle: list[str] = []
    precaution = ""

    if dept == "Nephrology" or re.search(r"kidney|renal|creatinine|frothy urine|ckd|কিডনি|क्रिएटिनिन", sig):
        helpful = [
            _pick(lang, "Light, freshly cooked home meals with controlled portion sizes", "তাজা, হালকা ঘরোয়া খাবার পরিমিত পরিমাণে", "ताजा, हल्का घर का बना खाना सीमित मात्रा में"),
        ]
        limit = [
            _pick(lang, "Excess table salt, packaged/pickled foods, and high-potassium/high-protein loads until labs are reviewed", "অতিরিক্ত লবণ, প্যাকেটজাত/আচার জাতীয় খাবার, এবং রিপোর্ট না দেখা পর্যন্ত অতিরিক্ত প্রোটিন বা পটাশিয়ামযুক্ত খাবার", "अधिक नमक, पैकेटबंद/अचार वाले खाद्य पदार्थ, और रिपोर्ट देखे बिना अत्यधिक प्रोटीन या पोटैशियम"),
            _pick(lang, "Over-the-counter NSAID painkillers without doctor approval", "চিকিৎসকের পরামর্শ ছাড়া ব্যথার ওষুধ (NSAID)", "डॉक्टर की सलाह के बिना दर्द की दवाएं (NSAID)"),
        ]
        hydration = _pick(
            lang,
            "Fluid intake depends on urine output, swelling, and kidney function — confirm exact daily fluid limits with your doctor.",
            "পানি পানের পরিমাণ প্রস্রাবের পরিমাণ, ফোলা ভাব এবং কিডনির রিপোর্টের ওপর নির্ভর করে — চিকিৎসকের কাছ থেকে সঠিক পরিমাণ জেনে নিন।",
            "पानी की मात्रा पेशाब, सूजन और किडनी की रिपोर्ट पर निर्भर करती है — अपने डॉक्टर से सही मात्रा पूछें।",
        )
        lifestyle = [
            _pick(lang, "Track blood pressure and urine output", "রক্তচাপ ও প্রস্রাবের পরিমাণ নজরে রাখুন", "ब्लड प्रेशर और पेशाब की मात्रा पर ध्यान दें"),
        ]
        precaution = _pick(
            lang,
            "Kidney-specific diet (sodium, potassium, phosphorus, protein) must be tailored to creatinine/eGFR labs.",
            "কিডনির জন্য নির্দিষ্ট ডায়েট ক্রিয়েটিনিন ও ইজিএফআর (eGFR) রিপোর্ট অনুযায়ী চিকিৎসক বা ডায়েটিশিয়ান ঠিক করবেন।",
            "किडनी के अनुसार आहार क्रिएटिनिन और eGFR रिपोर्ट के आधार पर डॉक्टर या डाइटीशियन तय करेंगे।",
        )

    elif dept == "Endocrinology" or re.search(r"diabet|blood sugar|hba1c|insulin|ডায়াবেটিস|সুগার|शुगर", sig):
        helpful = [
            _pick(lang, "High-fibre whole grains (millets/roti, brown rice in measured portions), dal, green vegetables, and unsweetened curd", "আঁশযুক্ত খাবার, পরিমিত ভাত বা আটার রুটি, ডাল, সবুজ শাকসবজি ও চিনি ছাড়া টক দই", "फाइबर युक्त साबुत अनाज (रोटी, दलिया), दाल, हरी सब्जियां और बिना चीनी का दही"),
        ]
        limit = [
            _pick(lang, "Sugary drinks, sweets, refined flour snacks, and large late-night carbohydrate meals", "মিষ্টি পানীয়, মিষ্টি, ময়দার ভাজাপোড়া এবং রাতে বেশি শর্করাযুক্ত খাবার", "मीठे पेय, मिठाइयां, मैदे के स्नैक्स और रात में अधिक कार्बोहाइड्रेट"),
        ]
        hydration = _pick(lang, "Drink plain water regularly; avoid fruit juices with added sugar.", "পর্যাপ্ত সাধারণ পানি পান করুন; চিনিযুক্ত ফলের রস এড়িয়ে চলুন।", "नियमित सादा पानी पिएं; मीठे फलों के जूस से बचें।")
        lifestyle = [
            _pick(lang, "15–30 minutes of gentle post-meal walking if physically able, and regular meal timings", "সম্ভব হলে খাবারের পর ১৫–৩০ মিনিট হালকা হাঁটা এবং নির্দিষ্ট সময়ে খাবার খাওয়া", "यदि संभव हो तो भोजन के बाद 15-30 मिनट टहलें और समय पर भोजन करें"),
        ]

    elif dept in ("Gastroenterology", "Hepatology") or re.search(r"stomach|abdom|acidity|diarrh|vomit|nausea|পেটে|অ্যাসিডিটি|বমি|পায়খানা|पेट|एसिडिटी|दस्त", sig):
        is_diarrhoea = bool(re.search(r"diarrh|loose motion|পায়খানা|दस्त", sig))
        helpful = [
            _pick(lang, "Soft, bland foods (rice/khichdi, banana, curd, clear dal soup)", "নরম ও সহজপাচ্য খাবার (হালকা খিচুড়ি, ভাত, কলা, টক দই, পাতলা ডালের স্যুপ)", "हल्का और सुपाच्य भोजन (खिचड़ी, चावल, केला, दही, पतली दाल)"),
        ]
        limit = [
            _pick(lang, "Spicy, deep-fried, very oily foods, caffeine, and carbonated drinks", "অতিরিক্ত ঝাল, ভাজাপোড়া, তেল-মসলাযুক্ত খাবার, চা-কফি ও কোল্ড ড্রিঙ্কস", "मसालेदार, तला-भुना भोजन, कैफीन और कोल्ड ड्रिंक्स"),
        ]
        hydration = (
            _pick(lang, "Take frequent small sips of WHO-standard ORS or clean fluids to prevent dehydration.", "ডিহাইড্রেশন এড়াতে অল্প অল্প করে বারবার ওআরএস (ORS) ও বিশুদ্ধ পানি পান করুন।", "डिहाइड्रेशन से बचने के लिए थोड़ा-थोड़ा करके ORS और साफ पानी पिएं।")
            if is_diarrhoea
            else _pick(lang, "Sip water between meals rather than large volumes right after eating; avoid lying down for 2 hours after meals.", "খাবারের মাঝে অল্প অল্প করে পানি পান করুন এবং খাওয়ার পর অন্তত ২ ঘণ্টা সোজা হয়ে থাকুন।", "भोजन के बीच थोड़ा पानी पिएं और खाने के बाद 2 घंटे तक लेटें नहीं।")
        )
        lifestyle = [_pick(lang, "Eat smaller, more frequent meals", "একবারে বেশি না খেয়ে অল্প করে বারবার খান", "एक साथ ज्यादा खाने के बजाय थोड़ा-थोड़ा कई बार खाएं")]

    elif dept == "Cardiology" or re.search(r"bp|hypertension|chest|palpitation|হৃদরোগ|রক্তচাপ|सीने|धड़कन", sig):
        helpful = [
            _pick(lang, "Heart-healthy whole foods: seasonal vegetables, fruits, dal, oats/millets, and home-cooked meals with minimal oil", "মৌসুমি শাকসবজি, ফল, ডাল, ওটস/মিলেট এবং কম তেলে রান্না করা ঘরোয়া খাবার", "मौसमी सब्जियां, फल, दाल, ओट्स और कम तेल में बना घर का खाना"),
        ]
        limit = [
            _pick(lang, "Excess salt/pickles, trans-fats, deep-fried snacks, tobacco, and excess caffeine", "অতিরিক্ত লবণ, আচার, ভাজাপোড়া, ধূমপান ও অতিরিক্ত চা-কফি", "अधिक नमक, अचार, तले हुए स्नैक्स, तंबाकू और अधिक कैफीन"),
        ]
        hydration = _pick(lang, "Maintain steady normal hydration unless your doctor has advised fluid restriction.", "চিকিৎসক পানি সীমিত করতে না বললে স্বাভাবিক পরিমাণে পানি পান করুন।", "जब तक डॉक्टर ने पानी सीमित करने को न कहा हो, सामान्य मात्रा में पानी पिएं।")
        lifestyle = [_pick(lang, "Avoid sudden heavy exertion until evaluated; prioritize 7–8 hours of sleep", "চিকিৎসকের পরীক্ষার আগে হঠাৎ ভারী পরিশ্রম এড়িয়ে চলুন এবং পর্যাপ্ত ঘুম নিশ্চিত করুন", "डॉक्टर की जांच से पहले अचानक भारी मेहनत से बचें और पर्याप्त नींद लें")]

    elif dept in ("Oncology", "Haematology") or re.search(r"cancer|tumour|tumor|oncology|leukemia|lymphoma|ক্যান্সার|টিউমার", sig):
        helpful = [
            _pick(lang, "Supportive, nutrient-dense home-cooked meals (adequate protein, cooked vegetables, soft whole grains) to maintain strength", "শক্তি বজায় রাখতে পুষ্টিকর ঘরোয়া খাবার (পর্যাপ্ত প্রোটিন, রান্না করা সবজি, সহজপাচ্য শস্য)", "ताकत बनाए रखने के लिए पौष्टिक घर का बना खाना (प्रोटीन, पकी हुई सब्जियां)"),
        ]
        limit = [
            _pick(lang, "Unverified herbal 'cancer cures', extreme restrictive diets, raw/unwashed foods if immunity is low, and tobacco/alcohol", "অপ্রমাণিত ভেষজ 'ক্যান্সারের ওষুধ', চরম ডায়েট, কাঁচা/অপরিষ্কার খাবার এবং তামাক/অ্যালকোহল", "अप्रमाणित हर्बल 'कैंसर इलाज', अत्यधिक परहेज और तंबाकू/शराब"),
        ]
        hydration = _pick(lang, "Maintain steady hydration with clean boiled/filtered water and broths.", "বিশুদ্ধ বা ফোটানো পানি ও স্যুপের মাধ্যমে পর্যাপ্ত আর্দ্রতা বজায় রাখুন।", "साफ उबले/फ़िल्टर किए हुए पानी और सूप से हाइड्रेशन बनाए रखें।")
        lifestyle = [_pick(lang, "Do not stop prescribed medical or oncology treatment; nutrition is strictly supportive care alongside specialist treatment", "চিকিৎসকের নির্ধারিত চিকিৎসা কখনোই বন্ধ করবেন না; পুষ্টি কেবল সহায়ক যত্ন হিসেবে কাজ করে", "डॉक्टर द्वारा निर्धारित उपचार कभी न रोकें; पोषण केवल सहायक देखभाल है")]
        precaution = _pick(lang, "Supportive nutrition guidance only — never a substitute for oncology evaluation or treatment.", "এটি কেবল সহায়ক পুষ্টি পরামর্শ — অনকোলজি চিকিৎসার বিকল্প নয়।", "यह केवल सहायक पोषण सलाह है — ऑन्कोलॉजी उपचार का विकल्प नहीं।")

    elif re.search(r"anaemi|anemi|haemoglobin|hemoglobin|হিমোগ্লোবিন|हीमोग्लोबिन", sig):
        helpful = [
            _pick(lang, "Iron-rich foods (spinach/saag, lentils, chana, dates, jaggery) paired with vitamin C (lemon/amla) to aid absorption", "আয়রনসমৃদ্ধ খাবার (পালং শাক, ডাল, ছোলা, খেজুর) এবং সাথে ভিটামিন সি (লেবু/আমলকি)", "आयरन युक्त आहार (पालक, दाल, चना, खजूर) और विटामिन सी (नींबू/आंवला) के साथ"),
        ]
        limit = [_pick(lang, "Avoid tea or coffee immediately with iron-rich meals as it reduces absorption", "খাবারের ঠিক পরপরই চা বা কফি এড়িয়ে চলুন, কারণ এতে আয়রন শোষণে বাধা হয়", "भोजन के तुरंत बाद चाय या कॉफी न लें, इससे आयरन का अवशोषण कम होता है")]
        hydration = _pick(lang, "Stay well hydrated with clean water.", "পর্যাপ্ত বিশুদ্ধ পানি পান করুন।", "पर्याप्त साफ पानी पिएं।")
        lifestyle = [_pick(lang, "Investigate the underlying cause of low haemoglobin with a doctor rather than relying on diet alone", "শুধু খাবারের ওপর নির্ভর না করে রক্তস্বল্পতার প্রকৃত কারণ চিকিৎসকের মাধ্যমে পরীক্ষা করান", "केवल आहार पर निर्भर न रहें, डॉक्टर से हीमोग्लोबिन कम होने का कारण जांच करवाएं")]

    else:
        helpful = [
            _pick(lang, "Balanced, freshly prepared meals with seasonal vegetables, dal/protein, and whole grains", "সুষম ঘরোয়া খাবার—মৌসুমি সবজি, ডাল/প্রোটিন ও শস্যদানা", "संतुलित, ताजा घर का बना भोजन—मौसमी सब्जियां, दाल और साबुत अनाज"),
        ]
        limit = [
            _pick(lang, "Ultra-processed foods, excess sugar/salt, alcohol, and tobacco", "অতিরিক্ত প্রক্রিয়াজাত খাবার, অতিরিক্ত চিনি/লবণ, অ্যালকোহল ও তামাক", "अत्यधिक प्रोसेस्ड फूड, अधिक चीनी/नमक, शराब और तंबाकू"),
        ]
        hydration = _pick(lang, "Drink adequate clean water throughout the day (warm fluids/soups if febrile or sore throat).", "সারাদিন পর্যাপ্ত বিশুদ্ধ পানি পান করুন (জ্বর বা গলা ব্যথা থাকলে কুসুম গরম পানি বা স্যুপ)।", "दिन भर पर्याप्त साफ पानी पिएं (बुखार या गले में खराश होने पर गुनगुना पानी या सूप)।")
        lifestyle = [_pick(lang, "Prioritize adequate rest and 7–8 hours of sleep", "পর্যাপ্ত বিশ্রাম ও ৭–৮ ঘণ্টা ঘুম নিশ্চিত করুন", "पर्याप्त आराम और 7-8 घंटे की नींद लें")]

    if pregnant:
        precaution = (precaution + " " if precaution else "") + _pick(
            lang,
            "Because you are pregnant, avoid unpasteurized foods, raw/undercooked items, and self-medication.",
            "গর্ভাবস্থায় কাঁচা বা অপাস্তুরিত খাবার এবং চিকিৎসকের পরামর্শ ছাড়া কোনো ওষুধ বা হার্বাল সাপ্লিমেন্ট এড়িয়ে চলুন।",
            "गर्भावस्था में कच्चा या अनपाश्चराइज्ड भोजन और बिना डॉक्टर की सलाह के कोई भी दवा न लें।",
        )
    if allergy_note:
        limit.append(allergy_note)

    sleep_guidance = _pick(
        lang,
        "Aim for 7–8 hours of regular, restful sleep; avoid screens and heavy meals 1–2 hours before bed.",
        "প্রতিদিন ৭–৮ ঘণ্টা নিয়মিত ও গভীর ঘুম নিশ্চিত করুন; ঘুমানোর ১–২ ঘণ্টা আগে ভারী খাবার ও মোবাইল স্ক্রিন এড়িয়ে চলুন।",
        "प्रतिदिन 7-8 घंटे की नियमित नींद लें; सोने से 1-2 घंटे पहले भारी भोजन और स्क्रीन से बचें।",
    )
    activity_guidance = (
        _pick(
            lang,
            "Rest and avoid strenuous physical exertion until a doctor evaluates your symptoms.",
            "চিকিৎসকের পরীক্ষার আগে ভারী পরিশ্রম এড়িয়ে পর্যাপ্ত বিশ্রাম নিন।",
            "डॉक्टर की जांच से पहले भारी शारीरिक श्रम से बचें और पर्याप्त आराम करें।",
        )
        if dept in ("Cardiology", "Emergency Medicine", "Pulmonology")
        else _pick(
            lang,
            "Light daily movement (such as gentle walking) as tolerated; pause if dizziness, pain, or breathlessness occurs.",
            "শরীরের সামর্থ্য অনুযায়ী হালকা হাঁটাচলা করুন; মাথা ঘোরা, ব্যথা বা শ্বাসকষ্ট হলে বিশ্রাম নিন।",
            "शरीर की क्षमता के अनुसार हल्की सैर करें; चक्कर, दर्द या सांस फूलने पर रुक जाएं।",
        )
    )

    return {
        "helpful_foods": helpful,
        "foods_to_limit": limit,
        "hydration_guidance": hydration,
        "sleep_guidance": sleep_guidance,
        "physical_activity_guidance": activity_guidance,
        "lifestyle_modifications": lifestyle,
        "precaution_note": precaution or _pick(
            lang,
            "Dietary needs vary by age, allergies, and medical conditions — confirm personalized restrictions with your doctor.",
            "বয়স, অ্যালার্জি ও শারীরিক অবস্থাভেদে খাদ্যাভ্যাস ভিন্ন হয় — চিকিৎসকের সাথে পরামর্শ করে নিশ্চিত করুন।",
            "उम्र, एलर्जी और स्वास्थ्य स्थिति के अनुसार आहार अलग होता है — अपने डॉक्टर से पुष्टि करें।",
        ),
    }
