/**
 * Spoken commands. A command is recognised only when the WHOLE utterance is a short command phrase
 * ("help", "phir se", "ruko") — never inside a longer question, so "help me read my report" is a question.
 * Emergency words open the emergency sheet (which only shows call buttons; nothing is dialled automatically).
 */
export type VoiceCommand = "emergency" | "repeat" | "stop" | "home" | "read";

const PHRASES: Record<VoiceCommand, string[]> = {
  emergency: [
    "help", "help me", "emergency", "sos", "ambulance", "call ambulance", "call 108",
    "madad", "madad karo", "bachao", "bachaao", "मदद", "मदद करो", "बचाओ", "आपातकाल", "एम्बुलेंस",
    "সাহায্য", "বাঁচাও", "உதவி", "காப்பாற்று", "సహాయం", "కాపాడండి", "मदत", "वाचवा", "મદદ", "બચાવો",
    "ಸಹಾಯ", "ಕಾಪಾಡಿ", "സഹായം", "രക്ഷിക്കൂ", "ସାହାଯ୍ୟ", "ବଞ୍ଚାଅ",
  ],
  repeat: ["repeat", "say again", "say that again", "again", "phir se", "phir se bolo", "dobara", "dubara",
           "फिर से", "फिर से बोलो", "दोबारा", "আবার বলো", "மீண்டும்", "మళ్ళీ", "पुन्हा", "ફરીથી", "ಮತ್ತೆ", "വീണ്ടും", "ପୁଣି"],
  stop: ["stop", "stop it", "quiet", "be quiet", "ruko", "ruk jao", "bas", "chup", "रुको", "रुक जाओ", "बस", "चुप",
         "থামো", "நிறுத்து", "ఆపు", "थांबा", "રોકો", "ನಿಲ್ಲಿಸು", "നിർത്തൂ", "ବନ୍ଦ କର"],
  home: ["go home", "home", "home page", "होम", "घर जाओ", "मुख्य पृष्ठ"],
  read: ["read", "read this", "read page", "read this page", "read it", "read aloud", "read the screen",
         "padho", "padh do", "padh ke sunao", "suna do", "sunao", "पढ़ो", "पढ़ दो", "पढ़कर सुनाओ", "सुनाओ", "सुना दो"],
};

const FILLER = /\b(please|plz|ji|na|now)\b|कृपया|प्लीज़|जी/g;

export function normalise(text: string): string {
  return text.toLowerCase().replace(/[.,!?।॥"'“”‘’-]/g, " ").replace(FILLER, " ").replace(/\s+/g, " ").trim();
}

export function matchCommand(text: string): VoiceCommand | null {
  const n = normalise(text);
  if (!n || n.split(" ").length > 4) return null;
  for (const [cmd, phrases] of Object.entries(PHRASES) as [VoiceCommand, string[]][]) {
    if (phrases.some((p) => normalise(p) === n)) return cmd;
  }
  return null;
}
