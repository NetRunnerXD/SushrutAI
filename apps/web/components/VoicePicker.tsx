"use client";

import { Volume2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useI18n } from "@/lib/i18n";
import { deviceVoices, pickVoice, setVoicePrefs, speak, voicePrefs } from "@/lib/voice";

const SAMPLE: Record<string, string> = {
  en: "Hello, I am SushrutAI. Drink plenty of water and rest today.",
  hi: "नमस्ते, मैं सुश्रुत एआई हूँ। आज खूब पानी पिएँ और आराम करें।",
};
const NAMES: Record<string, string> = { en: "English", hi: "हिन्दी", bn: "বাংলা", ta: "தமிழ்", te: "తెలుగు", mr: "मराठी",
  gu: "ગુજરાતી", kn: "ಕನ್ನಡ", ml: "മലയാളം", or: "ଓଡ଼ିଆ" };

/** Which on-device voice reads each language aloud (English and the app language), plus pitch.
 *  Languages with no device voice say so: the app then uses its own local voice for them. */
export function VoicePicker() {
  const { t, lang } = useI18n();
  const [voices, setVoices] = useState<SpeechSynthesisVoice[] | null>(null);
  const [prefs, setP] = useState(voicePrefs());
  useEffect(() => { deviceVoices().then(setVoices); }, []);
  const langs = [...new Set(["en", lang])];
  const save = (p: Parameters<typeof setVoicePrefs>[0]) => { setVoicePrefs(p); setP(voicePrefs()); };
  if (voices === null) return <div className="skeleton h-12" />;
  return (
    <div className="space-y-3">
      {langs.map((l) => {
        const base = l === "en" ? "en" : l;
        const list = voices.filter((v) => v.lang.toLowerCase().startsWith(base));
        const current = pickVoice(voices, l);
        return (
          <div key={l} className="space-y-1">
            <label htmlFor={`voice-${l}`} className="block font-semibold">{t.deviceVoice} — {NAMES[l] ?? l}</label>
            {list.length ? (
              <div className="flex flex-wrap items-center gap-2">
                <select id={`voice-${l}`} className="input min-h-12 flex-1" value={current?.voiceURI ?? ""}
                  onChange={(e) => save({ voices: { ...(prefs.voices ?? {}), [l]: e.target.value } })}>
                  {list.map((v) => <option key={v.voiceURI} value={v.voiceURI}>{v.name} ({v.lang}){v.localService ? "" : " · online"}</option>)}
                </select>
                <button type="button" onClick={() => speak(SAMPLE[l] ?? SAMPLE.en, l)}
                  className="inline-flex min-h-12 items-center gap-1.5 rounded-full bg-surface-2 px-3 font-semibold hover:bg-border">
                  <Volume2 aria-hidden className="h-4 w-4" />{t.tryVoice}</button>
              </div>
            ) : <p className="text-[0.9375rem] text-muted">{t.noDeviceVoice}</p>}
          </div>
        );
      })}
      <div className="space-y-1">
        <label htmlFor="voice-pitch" className="block font-semibold">{t.voicePitch}</label>
        <input id="voice-pitch" type="range" min={0.7} max={1.3} step={0.05} value={prefs.pitch ?? 1}
          onChange={(e) => save({ pitch: Number(e.target.value) })} className="w-full accent-[var(--brand)]" />
      </div>
    </div>
  );
}
