/**
 * Conversation controls spoken to the app ("stop", "ruko", "phir se", "explain simply", "go ahead").
 *
 * A control is recognised only when the WHOLE utterance is a short control phrase, never inside a longer
 * question ("help me read my report" is a question). This is the instant, on-device path for the commonest
 * phrases in English, Hindi and Hinglish (plus emergency / repeat / stop words in every Indian app language);
 * anything else goes to the server (/assistant/parse), which understands every app language through English.
 */
export type VoiceIntent =
  | "emergency" | "stop" | "pause" | "resume" | "repeat" | "simpler" | "more" | "yes" | "no" | "read" | "home"
  | "summary" | "snooze" | "cancelReminder";

const PHRASES: Record<VoiceIntent, string[]> = {
  emergency: [
    "help", "help me", "emergency", "sos", "ambulance", "call ambulance", "call 108", "call 112",
    "madad", "madad karo", "bachao", "bachaao", "मदद", "मदद करो", "बचाओ", "आपातकाल", "एम्बुलेंस",
    "সাহায্য", "বাঁচাও", "உதவி", "காப்பாற்று", "సహాయం", "కాపాడండి", "मदत", "वाचवा", "મદદ", "બચાવો",
    "ಸಹಾಯ", "ಕಾಪಾಡಿ", "സഹായം", "രക്ഷിക്കൂ", "ସାହାଯ୍ୟ", "ବଞ୍ଚାଅ", "ਮਦਦ", "ਬਚਾਓ", "সহায়", "مدد", "بچاؤ",
  ],
  stop: ["stop", "stop it", "stop talking", "quiet", "be quiet", "enough", "ruko", "ruk jao", "bas", "bas karo", "chup",
         "रुको", "रुक जाओ", "बस", "बस करो", "चुप", "থামো", "நிறுத்து", "ఆపు", "थांबा", "રોકો", "ನಿಲ್ಲಿಸು",
         "നിർത്തൂ", "ବନ୍ଦ କର", "ਰੁਕੋ", "বন্ধ কৰা", "رکو", "بس"],
  pause: ["pause", "wait", "hold on", "one minute", "one moment", "ek minute", "ek second", "thoda ruko", "ruko zara",
          "एक मिनट", "थोड़ा रुको", "ज़रा रुको", "रुकिए", "একটু থামো", "একটু দাঁড়াও", "থামুন"],
  resume: ["resume", "continue", "go on", "carry on", "start again", "chalo", "aage bolo", "jaari rakho", "chalu karo",
           "चलो", "आगे बोलो", "जारी रखो", "चालू करो", "আবার বলো", "শুরু করো", "চালিয়ে যাও"],
  repeat: ["repeat", "repeat that", "say again", "say that again", "again", "once more", "phir se", "phir se bolo",
           "dobara", "dubara", "fir se", "फिर से", "फिर से बोलो", "दोबारा", "আবার বলো", "மீண்டும்", "మళ్ళీ", "पुन्हा",
           "ફરીથી", "ಮತ್ತೆ", "വീണ്ടും", "ପୁଣି", "ਦੁਬਾਰਾ", "আকৌ", "دوبارہ"],
  simpler: ["explain simply", "explain it simply", "simpler", "in simple words", "i dont understand", "i didnt understand",
            "make it simple", "aasan bhasha mein", "samajh nahi aaya", "samajh nahi aya", "saral shabdon mein",
            "समझ नहीं आया", "आसान भाषा में", "सरल शब्दों में", "आसान करके बताओ",
            "সহজ করে বলুন", "সহজ ভাষায় বলো", "বুঝতে পারিনি"],
  more: ["tell me more", "more", "more details", "explain more", "in detail", "aur batao", "detail mein batao",
         "aur bataiye", "और बताओ", "विस्तार से बताओ", "और बताइए", "पूरा बताओ",
         "আরও বলুন", "বিস্তারিত বলো"],
  yes: ["yes", "yeah", "yep", "ok", "okay", "sure", "go ahead", "do it", "confirm", "correct", "haan", "haa", "han",
        "ji", "ji haan", "haan ji", "theek hai", "thik hai", "sahi", "bilkul", "karo", "kar do",
        "हाँ", "हां", "जी", "जी हाँ", "ठीक है", "सही", "बिल्कुल", "करो", "कर दो",
        "হ্যাঁ", "হাঁ", "ঠিক আছে", "করুন", "আচ্ছা"],
  no: ["no", "nope", "cancel", "dont", "do not", "never mind", "not now", "leave it", "nahi", "nahin", "na", "mat",
       "mat karo", "rehne do", "रहने दो", "नहीं", "ना", "मत", "मत करो",
       "না", "থাক", "বাতিল", "দরকার নেই"],
  read: ["read", "read this", "read page", "read this page", "read it", "read aloud", "read the screen",
         "padho", "padh do", "padh ke sunao", "suna do", "sunao", "पढ़ो", "पढ़ दो", "पढ़कर सुनाओ", "सुनाओ", "सुना दो",
         "পড়ুন", "পড়ে শোনাও", "শোনাও"],
  home: ["go home", "home", "home page", "होम", "घर जाओ", "मुख्य पृष्ठ", "home par jao", "হোম", "বাড়ি যাও"],
  snooze: ["snooze", "remind me later", "remind me in 15 minutes", "later", "baad mein", "baad mein yaad dilana",
           "thodi der baad", "बाद में", "बाद में याद दिलाना", "थोड़ी देर बाद", "পরে মনে করান", "পরে"],
  cancelReminder: ["cancel reminder", "cancel the reminder", "stop reminder", "no more reminders today",
                   "reminder band karo", "याद दिलाना बंद करो", "रिमाइंडर बंद करो", "রিমাইন্ডার বন্ধ করুন"],
  summary: ["prepare summary for doctor", "summary for doctor", "doctor summary", "make a report", "prepare summary",
            "doctor ke liye summary", "डॉक्टर के लिए सारांश", "रिपोर्ट बनाओ", "सारांश बनाओ", "ডাক্তারের জন্য সারসংক্ষেপ"],
};

const FILLER = /\b(please|plz|pls|ji|na|now|abhi)\b|कृपया|प्लीज़|अभी/g;

export function normalise(text: string): string {
  return text.toLowerCase().replace(/[.,!?।॥"'“”‘’-]/g, " ").replace(FILLER, " ").replace(/\s+/g, " ").trim();
}

/** Edit distance with an early exit (words are short). */
function close(a: string, b: string): boolean {
  if (a === b) return true;
  if (Math.min(a.length, b.length) < 4 || Math.abs(a.length - b.length) > 1) return false;
  let i = 0, j = 0, edits = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { i++; j++; continue; }
    if (++edits > 1) return false;
    if (a.length === b.length && a[i] === b[j + 1] && a[i + 1] === b[j]) { i += 2; j += 2; continue; }  // swapped letters
    if (a.length > b.length) i++; else if (b.length > a.length) j++; else { i++; j++; }
  }
  return edits + (a.length - i) + (b.length - j) <= 1;
}

const TABLE: [VoiceIntent, string[]][] = (Object.entries(PHRASES) as [VoiceIntent, string[]][])
  .map(([k, list]) => [k, list.map(normalise)]);

/** The control the whole utterance is, or null. Tolerates one misheard letter per longer word ("repaet", "resum"). */
export function matchIntent(text: string): VoiceIntent | null {
  const n = normalise(text);
  if (!n) return null;
  const words = n.split(" ");
  if (words.length > 5) return null;
  for (const [intent, list] of TABLE) if (list.includes(n)) return intent;   // exact first
  for (const [intent, list] of TABLE) {
    for (const p of list) {
      const pw = p.split(" ");
      if (pw.length === words.length && pw.every((w, i) => close(w, words[i]))) return intent;
    }
  }
  return null;
}
